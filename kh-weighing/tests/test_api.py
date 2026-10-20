"""Тесты по критериям приёмки историй ZER-1..13 и НФТ-04/05/07."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from .conftest import db, login, new_id

QOSTANAY = timezone(timedelta(hours=5))


def vehicle_id(client, name="МТЗ 82"):
    return next(v["id"] for v in client.get("/api/vehicles").json() if v["name"] == name)


def field_id(client, name="Березка"):
    return next(f["id"] for f in client.get("/api/fields").json() if f["name"] == name)


def put_trip(client, **kw):
    tid = kw.pop("id", None) or new_id()
    body = {"vehicle_id": vehicle_id(client), "field_id": field_id(client), "gross_kg": 25000, **kw}
    return client.put(f"/api/trips/{tid}", json=body), tid


# --- Авторизация и роли (НФТ-04) -------------------------------------------

def test_requires_login(app_client: TestClient):
    r = app_client.get("/api/trips")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


def test_bad_password(app_client):
    r = app_client.post("/api/auth/login", json={"login": "operator", "password": "nope"})
    assert r.status_code == 401 and r.json()["code"] == "bad_credentials"


def test_me_and_logout(operator):
    assert operator.get("/api/auth/me").json()["role"] == "operator"
    operator.post("/api/auth/logout")
    assert operator.get("/api/auth/me").status_code == 401


def test_operator_cannot_change_tare_or_read_audit(operator):
    vid = vehicle_id(operator)
    assert operator.patch(f"/api/vehicles/{vid}", json={"tare_kg": 7000}).status_code == 403
    _, tid = put_trip(operator)
    assert operator.get(f"/api/trips/{tid}/revisions").status_code == 403


def test_admin_changes_tare_only_for_new_trips(app_client):
    admin = login(app_client, "admin")
    vid = vehicle_id(admin)
    r1, tid1 = put_trip(admin)  # тестовая тара МТЗ 82 = 5800
    assert r1.json()["tare_kg"] == 5800
    assert admin.patch(f"/api/vehicles/{vid}", json={"tare_kg": 6100}).json()["tare_kg"] == 6100
    r2, _ = put_trip(admin)
    assert r2.json()["tare_kg"] == 6100
    # старый рейс не пересчитался
    assert admin.get("/api/trips").json()["trips"][0]["tare_kg"] == 5800


# --- Справочники ------------------------------------------------------------

def test_catalog_seeded(operator):
    vehicles = [v["name"] for v in operator.get("/api/vehicles").json()]
    fields = [f["name"] for f in operator.get("/api/fields").json()]
    assert vehicles == ["МТЗ 952", "МТЗ 82", "МТЗ 80", "МТЗ 1025"]
    assert len(fields) == 10 and "Балгабай" in fields and "409" in fields


# --- ZER-1..5, 7: создание рейса и расчёт нетто ------------------------------

def test_create_trip_calculates_netto(operator):
    r, tid = put_trip(operator, gross_kg=25000, tare_kg=7000)
    assert r.status_code == 201
    t = r.json()
    assert t["id"] == tid
    assert t["net_kg"] == 18000                       # пример из ZER-5
    assert t["vehicle_name"] == "МТЗ 82" and t["field_name"] == "Березка"   # ZER-2
    assert t["weighed_at"] and t["weighed_local"]     # ZER-4
    assert t["is_corrected"] is False
    assert operator.get("/api/trips").json()["trips"][0]["id"] == tid   # ZER-7: появляется в истории


def test_tare_defaults_from_vehicle(operator):
    r, _ = put_trip(operator, gross_kg=16000)
    assert r.json()["tare_kg"] == 5800 and r.json()["net_kg"] == 10200


def test_idempotent_retry_from_offline_queue(operator):
    r1, tid = put_trip(operator, gross_kg=20000)
    r2, _ = put_trip(operator, id=tid, gross_kg=20000)
    assert r1.status_code == 201 and r2.status_code == 200
    assert len(operator.get("/api/trips").json()["trips"]) == 1


def test_offline_weighed_at_is_kept(operator):
    # время из прошлого: фиксированное 09:15 после полуночи по Костанаю оказывалось в будущем
    when = (datetime.now(QOSTANAY) - timedelta(hours=2)).replace(second=0, microsecond=0)
    r, _ = put_trip(operator, weighed_at=when.isoformat())
    assert r.json()["weighed_local"] == when.strftime("%H:%M")


# --- ZER-1, 2, 3, 6: проверка ввода -------------------------------------------

def test_missing_vehicle_rejected(operator):
    r = operator.put(f"/api/trips/{new_id()}", json={"field_id": field_id(operator), "gross_kg": 20000})
    assert r.status_code == 422
    assert {"field": "vehicle_id", "type": "missing"} in r.json()["errors"]


def test_unknown_field_rejected(operator):
    r, _ = put_trip(operator, field_id=99999)
    assert r.status_code == 422 and r.json()["code"] == "unknown_field"


def test_tare_greater_than_gross_rejected(operator):
    r, _ = put_trip(operator, gross_kg=5000, tare_kg=7000)
    assert r.status_code == 422 and r.json()["code"] == "tare_ge_gross"
    assert operator.get("/api/trips").json()["trips"] == []


def test_negative_and_non_numeric_rejected(operator):
    r, _ = put_trip(operator, gross_kg=-100)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "gross_kg"
    r, _ = put_trip(operator, gross_kg="много")
    assert r.status_code == 422


def test_future_time_rejected(operator):
    r, _ = put_trip(operator, weighed_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
    assert r.status_code == 422 and r.json()["code"] == "future_time"


def test_db_constraint_backs_up_api(operator):
    """Даже в обход API база не примет тару >= брутто."""
    import psycopg
    import pytest
    with db() as conn:
        uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO trips (id, vehicle_id, field_id, gross_kg, tare_kg, weighed_at, created_by)"
                " VALUES (gen_random_uuid(), 1, 1, 5000, 7000, now(), %s)", uid,
            )


# --- ZER-8: пустая история -------------------------------------------------

def test_empty_history(operator):
    assert operator.get("/api/trips").json()["trips"] == []
    assert operator.get("/api/trips?date=2026-13-45").status_code == 422


# --- ZER-9, 10 + НФТ-05: исправление и журнал ----------------------------------

def test_correction_recalculates_marks_and_logs(app_client):
    op = login(app_client, "operator")
    _, tid = put_trip(op, gross_kg=25000, tare_kg=7000)
    r = op.patch(f"/api/trips/{tid}", json={"gross_kg": 24000})
    assert r.status_code == 200
    assert r.json()["net_kg"] == 17000 and r.json()["is_corrected"] is True
    assert op.get("/api/trips").json()["trips"][0]["is_corrected"] is True

    admin = login(app_client, "admin")
    revs = admin.get(f"/api/trips/{tid}/revisions").json()
    assert len(revs) == 1
    assert revs[0]["old"] == {"gross_kg": 25000} and revs[0]["new"] == {"gross_kg": 24000}


def test_correction_without_changes_is_not_logged(operator):
    _, tid = put_trip(operator, gross_kg=25000, tare_kg=7000)
    r = operator.patch(f"/api/trips/{tid}", json={"gross_kg": 25000})
    assert r.json()["is_corrected"] is False


def test_correction_validates(operator):
    _, tid = put_trip(operator, gross_kg=25000, tare_kg=7000)
    r = operator.patch(f"/api/trips/{tid}", json={"tare_kg": 26000})
    assert r.status_code == 422 and r.json()["code"] == "tare_ge_gross"
    assert operator.patch(f"/api/trips/{new_id()}", json={"gross_kg": 1}).status_code == 404


def test_audit_log_is_append_only(app_client):
    import psycopg
    import pytest
    op = login(app_client, "operator")
    _, tid = put_trip(op, gross_kg=25000, tare_kg=7000)
    op.patch(f"/api/trips/{tid}", json={"gross_kg": 24000})
    with db() as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM trip_revisions")


# --- ZER-11..13: итоги ------------------------------------------------------

def test_day_summary_matches_sum_of_trips(operator):
    v82, v80 = vehicle_id(operator, "МТЗ 82"), vehicle_id(operator, "МТЗ 80")
    f1, f2 = field_id(operator, "Березка"), field_id(operator, "Ракета")
    nets = []
    for v, f, g, t in [(v82, f1, 25000, 7000), (v82, f2, 23000, 7000), (v80, f1, 21000, 6000)]:
        r, _ = put_trip(operator, vehicle_id=v, field_id=f, gross_kg=g, tare_kg=t)
        nets.append(r.json()["net_kg"])

    s = operator.get("/api/summary/day").json()
    assert s["trips"] == 3 and s["net_kg"] == sum(nets) == 49000            # ZER-11
    by_v = {x["name"]: x for x in s["by_vehicle"]}
    assert by_v["МТЗ 82"]["trips"] == 2 and by_v["МТЗ 82"]["net_kg"] == 34000  # ZER-12
    assert by_v["МТЗ 1025"]["trips"] == 0 and by_v["МТЗ 1025"]["net_kg"] == 0  # ZER-12: нулевой
    by_f = {x["name"]: x for x in s["by_field"]}
    assert by_f["Березка"]["net_kg"] == 33000 and by_f["Ракета"]["trips"] == 1  # ZER-13
    assert by_f["Навозка"]["trips"] == 0                                        # ZER-13: нет данных


def test_day_boundary_uses_local_time(operator):
    """Рейс в 23:30 по Костанаю — это тот же день, хотя по UTC уже 18:30 того же дня;
    рейс в 00:30 — уже следующий день, хотя по UTC это ещё предыдущий."""
    day = datetime(2026, 9, 10, tzinfo=QOSTANAY)
    put_trip(operator, gross_kg=20000, weighed_at=day.replace(hour=23, minute=30).isoformat())
    put_trip(operator, gross_kg=21000, weighed_at=(day + timedelta(days=1)).replace(hour=0, minute=30).isoformat())
    assert operator.get("/api/summary/day?date=2026-09-10").json()["trips"] == 1
    assert operator.get("/api/summary/day?date=2026-09-11").json()["trips"] == 1
    assert [t["weighed_local"] for t in operator.get("/api/trips?date=2026-09-11").json()["trips"]] == ["00:30"]


def test_season_summary(operator):
    put_trip(operator, gross_kg=20000, weighed_at=datetime(2026, 8, 20, 10, tzinfo=QOSTANAY).isoformat())
    put_trip(operator, gross_kg=21000, weighed_at=datetime(2026, 9, 1, 10, tzinfo=QOSTANAY).isoformat())
    s = operator.get("/api/summary/season?year=2026").json()
    assert s["trips"] == 2 and s["net_kg"] == (20000 - 5800) + (21000 - 5800)


def test_health(app_client):
    assert app_client.get("/api/health").json() == {"status": "ok"}
