from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, Request
from psycopg import Connection

from .security import token_hash

SESSION_COOKIE = "kh_session"


class ApiError(Exception):
    """Ошибка API: HTTP-код + машинный код (фронт переводит его на RU/KZ) + сообщение."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.field = field


@dataclass(frozen=True)
class User:
    id: int
    login: str
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_conn(request: Request) -> Iterator[Connection]:
    with request.app.state.pool.connection() as conn:
        yield conn


def current_user(request: Request, conn: Connection = Depends(get_conn)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise ApiError(401, "unauthorized", "Требуется вход")
    row = conn.execute(
        """
        SELECT u.id, u.login, u.name, u.role
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = %s AND s.expires_at > now() AND u.active
        """,
        (token_hash(token),),
    ).fetchone()
    if not row:
        raise ApiError(401, "unauthorized", "Сессия истекла, войдите снова")
    return User(**row)


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise ApiError(403, "forbidden", "Недостаточно прав")
    return user
