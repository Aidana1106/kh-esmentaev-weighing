import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import hash_password

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/weighing_test")


def _reset_db():
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")


@pytest.fixture()
def app_client():
    _reset_db()
    settings = Settings(
        database_url=TEST_DB, timezone="Asia/Qostanay", cookie_secure=False,
        session_days=30, admin_login=None, admin_password=None,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with psycopg.connect(TEST_DB, autocommit=True) as conn:
            for login, role in (("operator", "operator"), ("admin", "admin")):
                conn.execute(
                    "INSERT INTO users (login, name, role, password_hash) VALUES (%s, %s, %s, %s)",
                    (login, login.title(), role, hash_password("secret123")),
                )
        yield client


def login(client: TestClient, who: str = "operator") -> TestClient:
    r = client.post("/api/auth/login", json={"login": who, "password": "secret123"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def operator(app_client):
    return login(app_client, "operator")


def new_id() -> str:
    return str(uuid.uuid4())


def db():
    return psycopg.connect(TEST_DB, autocommit=True)
