from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from ..deps import ApiError, User, current_user, get_conn, require_admin
from ..timeutil import day_bounds, parse_day

router = APIRouter(prefix="/api/trips", tags=["trips"])

MAX_KG = 100_000
FUTURE_TOLERANCE = timedelta(minutes=10)

TRIP_SELECT = """
    SELECT t.id, t.vehicle_id, v.name AS vehicle_name, t.field_id, f.name AS field_name,
           t.gross_kg, t.tare_kg, t.net_kg, t.weighed_at,
           to_char(t.weighed_at AT TIME ZONE %(tz)s, 'HH24:MI') AS weighed_local,
           t.is_corrected, t.updated_at, u.name AS created_by_name
    FROM trips t
    JOIN vehicles v ON v.id = t.vehicle_id
    JOIN fields f ON f.id = t.field_id
    JOIN users u ON u.id = t.created_by
"""


class TripIn(BaseModel):
    vehicle_id: int
    field_id: int
    gross_kg: int = Field(gt=0, le=MAX_KG)
    tare_kg: int | None = Field(default=None, gt=0, le=MAX_KG)  # не передали — берём тару машины
    weighed_at: datetime | None = None                            # не передали — текущее время

    @field_validator("weighed_at")
    @classmethod
    def must_have_tz(cls, v: datetime | None):
        if v is not None and v.tzinfo is None:
            raise ValueError("weighed_at must include timezone")
        return v


class TripPatch(BaseModel):
    vehicle_id: int | None = None
    field_id: int | None = None
    gross_kg: int | None = Field(default=None, gt=0, le=MAX_KG)
    tare_kg: int | None = Field(default=None, gt=0, le=MAX_KG)


def _fetch_trip(conn: Connection, trip_id: UUID, tz: str) -> dict | None:
    return conn.execute(TRIP_SELECT + " WHERE t.id = %(id)s", {"tz": tz, "id": trip_id}).fetchone()


def _check_refs(conn: Connection, vehicle_id: int, field_id: int) -> dict:
    vehicle = conn.execute(
        "SELECT id, tare_kg FROM vehicles WHERE id = %s AND active", (vehicle_id,)
    ).fetchone()
    if not vehicle:
        raise ApiError(422, "unknown_vehicle", "Машина не выбрана или не найдена", field="vehicle_id")
    if not conn.execute("SELECT 1 FROM fields WHERE id = %s AND active", (field_id,)).fetchone():
        raise ApiError(422, "unknown_field", "Поле не выбрано или не найдено", field="field_id")
    return vehicle


def _check_weights(gross: int, tare: int) -> None:
    if tare >= gross:
        raise ApiError(422, "tare_ge_gross", "Тара должна быть меньше брутто", field="gross_kg")


@router.get("")
def list_trips(
    request: Request, date: str | None = None,
    _: User = Depends(current_user), conn: Connection = Depends(get_conn),
):
    """История рейсов за день (ZER-8). По умолчанию — сегодня по времени весовой."""
    tz = request.app.state.settings.timezone
    day = parse_day(date, tz)
    start, end = day_bounds(day, tz)
    rows = conn.execute(
        TRIP_SELECT + " WHERE t.weighed_at >= %(start)s AND t.weighed_at < %(end)s ORDER BY t.weighed_at, t.received_at",
        {"tz": tz, "start": start, "end": end},
    ).fetchall()
    return {"date": day.isoformat(), "trips": rows}


@router.put("/{trip_id}")
def create_trip(
    trip_id: UUID, body: TripIn, request: Request, response: Response,
    user: User = Depends(current_user), conn: Connection = Depends(get_conn),
):
    """Создание рейса (ZER-1..7). Идемпотентно по id: повтор из офлайн-очереди вернёт уже сохранённый рейс."""
    tz = request.app.state.settings.timezone
    existing = _fetch_trip(conn, trip_id, tz)
    if existing:
        response.status_code = 200
        return existing

    vehicle = _check_refs(conn, body.vehicle_id, body.field_id)
    tare = body.tare_kg if body.tare_kg is not None else vehicle["tare_kg"]
    if tare is None:
        raise ApiError(422, "tare_missing", "У машины не задана тара — введите тару вручную", field="tare_kg")
    _check_weights(body.gross_kg, tare)

    now = datetime.now(timezone.utc)
    weighed_at = body.weighed_at or now
    if weighed_at > now + FUTURE_TOLERANCE:
        raise ApiError(422, "future_time", "Время взвешивания в будущем — проверьте часы телефона", field="weighed_at")

    with conn.transaction():
        inserted = conn.execute(
            """
            INSERT INTO trips (id, vehicle_id, field_id, gross_kg, tare_kg, weighed_at, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """,
            (trip_id, body.vehicle_id, body.field_id, body.gross_kg, tare, weighed_at, user.id),
        ).fetchone()
    response.status_code = 201 if inserted else 200
    return _fetch_trip(conn, trip_id, tz)


@router.patch("/{trip_id}")
def correct_trip(
    trip_id: UUID, body: TripPatch, request: Request,
    user: User = Depends(current_user), conn: Connection = Depends(get_conn),
):
    """Исправление записи (ZER-9): пересчёт нетто, отметка «исправлено» (ZER-10), запись в журнал (НФТ-05)."""
    tz = request.app.state.settings.timezone
    editable = ("vehicle_id", "field_id", "gross_kg", "tare_kg")
    with conn.transaction():
        old = conn.execute(
            "SELECT vehicle_id, field_id, gross_kg, tare_kg FROM trips WHERE id = %s FOR UPDATE",
            (trip_id,),
        ).fetchone()
        if not old:
            raise ApiError(404, "not_found", "Рейс не найден")

        changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
        new = {**old, **changes}
        if new == old:
            return _fetch_trip(conn, trip_id, tz)

        _check_refs(conn, new["vehicle_id"], new["field_id"])
        _check_weights(new["gross_kg"], new["tare_kg"])

        conn.execute(
            """
            UPDATE trips SET vehicle_id = %s, field_id = %s, gross_kg = %s, tare_kg = %s,
                   is_corrected = true, updated_at = now()
            WHERE id = %s
            """,
            (new["vehicle_id"], new["field_id"], new["gross_kg"], new["tare_kg"], trip_id),
        )
        diff_old = {k: old[k] for k in editable if old[k] != new[k]}
        diff_new = {k: new[k] for k in editable if old[k] != new[k]}
        conn.execute(
            "INSERT INTO trip_revisions (trip_id, changed_by, old, new) VALUES (%s, %s, %s, %s)",
            (trip_id, user.id, Jsonb(diff_old), Jsonb(diff_new)),
        )
    return _fetch_trip(conn, trip_id, tz)


@router.get("/{trip_id}/revisions")
def trip_revisions(
    trip_id: UUID, _: User = Depends(require_admin), conn: Connection = Depends(get_conn),
):
    """Журнал исправлений рейса — только администратор (НФТ-04, НФТ-05). Экран журнала — версия 2 (ZER-16)."""
    return conn.execute(
        """
        SELECT r.id, r.changed_at, u.name AS changed_by_name, r.old, r.new
        FROM trip_revisions r JOIN users u ON u.id = r.changed_by
        WHERE r.trip_id = %s ORDER BY r.id
        """,
        (trip_id,),
    ).fetchall()
