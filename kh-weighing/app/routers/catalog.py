from fastapi import APIRouter, Depends
from psycopg import Connection
from pydantic import BaseModel, Field

from ..deps import ApiError, User, current_user, get_conn, require_admin

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/vehicles")
def list_vehicles(_: User = Depends(current_user), conn: Connection = Depends(get_conn)):
    return conn.execute(
        "SELECT id, name, tare_kg FROM vehicles WHERE active ORDER BY id"
    ).fetchall()


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
