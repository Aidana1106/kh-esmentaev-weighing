import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    timezone: str            # по нему определяется «день» для итогов
    cookie_secure: bool      # true за HTTPS (НФТ-10)
    session_days: int        # долгая сессия: в поле без интернета логин не должен слетать
    admin_login: str | None  # первый запуск: создать администратора, если пользователей нет
    admin_password: str | None


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/weighing"),
        timezone=os.environ.get("APP_TZ", "Asia/Qostanay"),
        cookie_secure=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        session_days=int(os.environ.get("SESSION_DAYS", "30")),
        admin_login=os.environ.get("ADMIN_LOGIN") or None,
        admin_password=os.environ.get("ADMIN_PASSWORD") or None,
    )
