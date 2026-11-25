from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from psycopg import Connection

from ..deps import ApiError, User, current_user, get_conn, require_admin
from ..timeutil import day_bounds, parse_day, year_bounds

router = APIRouter(prefix="/api/summary", tags=["summary"])

_GROUP_SQL = """
    SELECT g.id, g.name, count(t.id)::int AS trips, coalesce(sum(t.net_kg), 0)::bigint AS net_kg
    FROM {table} g
    LEFT JOIN trips t ON t.{fk} = g.id AND t.weighed_at >= %(start)s AND t.weighed_at < %(end)s
    GROUP BY g.id, g.name, g.active
    HAVING g.active OR count(t.id) > 0
    ORDER BY g.id
"""


def _totals(conn: Connection, start, end) -> dict:
    params = {"start": start, "end": end}
    total = conn.execute(
        "SELECT count(*)::int AS trips, coalesce(sum(net_kg), 0)::bigint AS net_kg "
        "FROM trips WHERE weighed_at >= %(start)s AND weighed_at < %(end)s",
        params,
    ).fetchone()
    return {
        "trips": total["trips"],
        "net_kg": total["net_kg"],
        # Все активные машины и поля, включая нулевые (ZER-12, ZER-13)
        "by_vehicle": conn.execute(_GROUP_SQL.format(table="vehicles", fk="vehicle_id"), params).fetchall(),
        "by_field": conn.execute(_GROUP_SQL.format(table="fields", fk="field_id"), params).fetchall(),
    }


@router.get("/day")
def day_summary(
    request: Request, date: str | None = None,
    _: User = Depends(current_user), conn: Connection = Depends(get_conn),
):
    """Итог за день (ZER-11), по машинам (ZER-12) и по полям (ZER-13)."""
    tz = request.app.state.settings.timezone
    day = parse_day(date, tz)
    start, end = day_bounds(day, tz)
    return {"date": day.isoformat(), **_totals(conn, start, end)}


@router.get("/season")
def season_summary(
    request: Request, year: int | None = None,
    _: User = Depends(current_user), conn: Connection = Depends(get_conn),
):
    """Накопительный итог за сезон (календарный год)."""
    tz = request.app.state.settings.timezone
    year = year or datetime.now(ZoneInfo(tz)).year
    if not 2000 <= year <= 2100:
        raise ApiError(422, "bad_year", "Неверный год", field="year")
    start, end = year_bounds(year, tz)
    return {"year": year, **_totals(conn, start, end)}


@router.get("/weight-check")
def weight_check_summary(
    days: int = 7, _: User = Depends(require_admin), conn: Connection = Depends(get_conn),
):
    """Мониторинг проверки веса (ADR-003) для отчёта PM: сколько предупреждений, сколько подтверждено,
    сколько рейсов всё равно исправили после сохранения (бизнес-метрика)."""
    if not 1 <= days <= 366:
        raise ApiError(422, "bad_days", "Период от 1 до 366 дней", field="days")
    row = conn.execute(
        """
        SELECT count(*)::int AS trips,
               count(*) FILTER (WHERE weight_warning IS NOT NULL)::int AS warned,
               count(*) FILTER (WHERE weight_confirmed)::int AS confirmed,
               count(*) FILTER (WHERE is_corrected)::int AS corrected,
               count(*) FILTER (WHERE is_corrected AND weight_warning IS NULL)::int AS corrected_without_warning
        FROM trips WHERE weighed_at > now() - make_interval(days => %s)
        """,
        (days,),
    ).fetchone()
    n = row["trips"] or 1
    return {"days": days, **row,
            "warned_share": round(row["warned"] / n, 3),
            "confirmed_share": round(row["confirmed"] / n, 3),
            "corrected_share": round(row["corrected"] / n, 3)}
