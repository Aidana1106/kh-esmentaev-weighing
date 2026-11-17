"""Нагрузочный тест (ЛЗ 11, НФТ-01 и НФТ-03).

Профиль – рабочий день весовой в уборку: оператор чаще всего открывает историю
рейсов за день, иногда итоги и сохраняет новый рейс.

    # НФТ-01: 20 пользователей, 5 000 рейсов в базе, цель p95 «История» ≤ 1,5 с
    locust -f tests/load/locustfile.py --host http://127.0.0.1:8000 \
        --headless -u 20 -r 5 -t 3m --csv results/nft01
    # НФТ-03: 60 пользователей, 15 000 рейсов
    locust -f tests/load/locustfile.py --host http://127.0.0.1:8000 \
        --headless -u 60 -r 10 -t 3m --csv results/nft03

Пароль тестовых операторов – переменная TEST_PASSWORD (как в make_test_data.py).
"""
import itertools
import os
import random
import uuid
from datetime import date, timedelta

from locust import HttpUser, between, task

PASSWORD = os.environ.get("TEST_PASSWORD", "load-test-only")
SEASON_START = date(2026, 8, 1)
_ids = itertools.count(1)


class Operator(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        n = next(_ids)
        self.client.post("/api/auth/login", json={"login": f"load_op{n}", "password": PASSWORD}, name="вход")
        self.vehicles = self.client.get("/api/vehicles", name="справочники").json()
        self.fields = self.client.get("/api/fields", name="справочники").json()

    def _day(self) -> str:
        return (SEASON_START + timedelta(days=random.randrange(92))).isoformat()

    @task(6)
    def history(self):
        self.client.get(f"/api/trips?date={self._day()}", name="История рейсов за день")

    @task(2)
    def day_summary(self):
        self.client.get(f"/api/summary/day?date={self._day()}", name="Итоги дня")

    @task(1)
    def season_summary(self):
        self.client.get("/api/summary/season", name="Итоги сезона")

    @task(2)
    def save_trip(self):
        v = random.choice(self.vehicles)
        f = random.choice(self.fields)
        gross = (v.get("tare_kg") or 6000) + random.randint(8000, 22000)
        self.client.put(
            f"/api/trips/{uuid.uuid4()}",
            json={"vehicle_id": v["id"], "field_id": f["id"], "gross_kg": gross},
            name="Сохранить рейс",
        )
