from fastapi import APIRouter, Depends, Request
from psycopg import Connection
from pydantic import BaseModel, Field

from ..deps import ApiError, User, current_user, get_conn, require_admin

router = APIRouter(prefix="/api", tags=["catalog"])


# Медиана нетто машины за последние 120 дней – для проверки первого рейса дня (ADR-003).
# Меньше 5 рейсов – медианы нет, проверка по сезону не работает.
VEHICLES_SQL = """
    SELECT v.id, v.name, v.tare_kg, m.median_net_kg
    FROM vehicles v
    LEFT JOIN LATERAL (
        SELECT CASE WHEN count(*) >= 5
                    THEN round(percentile_cont(0.5) WITHIN GROUP (ORDER BY t.net_kg))::int END AS median_net_kg
        FROM trips t
        WHERE t.vehicle_id = v.id AND t.weighed_at > now() - interval '120 days'
    ) m ON true
    WHERE v.active ORDER BY v.id
"""


@router.get("/vehicles")
def list_vehicles(_: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    return conn.execute(VEHICLES_SQL).fetchall()


@router.get("/client-config")
def client_config(request: Request, _: User = Depends(current_user)):
    """Настройки для телефона; кэшируются вместе со справочниками для работы без связи."""
    return {"weight_check": request.app.state.settings.weight_check, "weight_check_threshold_pct": 15}


@router.get("/fields")
def list_fields(_: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    return conn.execute(
        "SELECT id, name, crop FROM fields WHERE active ORDER BY id"
    ).fetchall()


class VehiclePatch(BaseModel):
    tare_kg: int = Field(gt=0, le=60000)


@router.patch("/vehicles/{vehicle_id}")
def update_vehicle_tare(
    vehicle_id: int, body: VehiclePatch,
    _: User = Depends(require_admin), conn: Connection = Depends(get_conn),
):
    """Тара машины на сезон. Меняет только подстановку для НОВЫХ рейсов: в старых рейсах тара сохранена."""
    with conn.transaction():
        row = conn.execute(
            "UPDATE vehicles SET tare_kg = %s WHERE id = %s RETURNING id, name, tare_kg",
            (body.tare_kg, vehicle_id),
        ).fetchone()
    if not row:
        raise ApiError(404, "not_found", "Машина не найдена")
    return row
