"""Синтетические тестовые данные для нагрузочного теста и UAT (ЛЗ 11).

Реальных людей и реальных весов здесь нет: рейсы генерируются случайно
по справочникам машин и полей. Пользователи – вымышленные (load_op1…).

    python scripts/make_test_data.py --trips 5000            # НФТ-01
    python scripts/make_test_data.py --trips 15000 --clean   # НФТ-03

Переменные окружения: DATABASE_URL, TEST_PASSWORD (пароль тестовых операторов).
"""
import argparse
import os
import random
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.security import hash_password

TZ = ZoneInfo(os.environ.get("APP_TZ", "Asia/Qostanay"))
SEASON_START = date(2026, 8, 1)
SEASON_DAYS = 92  # август–октябрь, уборка


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trips", type=int, default=5000)
    ap.add_argument("--users", type=int, default=60, help="вымышленные операторы для нагрузки")
    ap.add_argument("--clean", action="store_true", help="удалить прежние тестовые рейсы")
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    password = os.environ.get("TEST_PASSWORD", "load-test-only")
    url = os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/weighing")

    with psycopg.connect(url, autocommit=True) as conn:
        pw = hash_password(password)
        for i in range(1, a.users + 1):
            conn.execute(
                "INSERT INTO users (login, name, role, password_hash) VALUES (%s, %s, 'operator', %s)"
                " ON CONFLICT (login) DO UPDATE SET password_hash = EXCLUDED.password_hash",
                (f"load_op{i}", f"Тестовый оператор {i}", pw),
            )
        uid = conn.execute("SELECT id FROM users WHERE login = 'load_op1'").fetchone()[0]
        if a.clean:
            conn.execute(
                "DELETE FROM trip_revisions WHERE trip_id IN (SELECT t.id FROM trips t JOIN users u"
                " ON u.id = t.created_by WHERE u.login LIKE 'load_op%%')"
            )
            conn.execute("DELETE FROM trips WHERE created_by IN (SELECT id FROM users WHERE login LIKE 'load_op%%')")
        vehicles = conn.execute("SELECT id, tare_kg FROM vehicles WHERE tare_kg IS NOT NULL").fetchall()
        fields = [r[0] for r in conn.execute("SELECT id FROM fields").fetchall()]

        rows = []
        for _ in range(a.trips):
            vid, tare = rnd.choice(vehicles)
            day = SEASON_START + timedelta(days=rnd.randrange(SEASON_DAYS))
            t = time(rnd.randint(7, 19), rnd.randint(0, 59), rnd.randint(0, 59))
            weighed = datetime.combine(day, t, TZ)
            gross = tare + rnd.randint(8000, 22000)
            rows.append((uuid.uuid4(), vid, rnd.choice(fields), gross, tare, weighed, uid))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO trips (id, vehicle_id, field_id, gross_kg, tare_kg, weighed_at, created_by)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
        total = conn.execute("SELECT count(*) FROM trips").fetchone()[0]
        print(f"добавлено {len(rows)} рейсов, всего в базе {total}; операторов load_op1..{a.users}")


if __name__ == "__main__":
    main()
