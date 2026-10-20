from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from psycopg import Connection
from pydantic import BaseModel, Field

from ..deps import SESSION_COOKIE, ApiError, User, current_user, get_conn
from ..security import DUMMY_HASH, new_session_token, token_hash, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


def _user_out(u: User) -> dict:
    return {"id": u.id, "login": u.login, "name": u.name, "role": u.role}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, conn: Connection = Depends(get_conn)):
    settings = request.app.state.settings
    row = conn.execute(
        "SELECT id, login, name, role, password_hash FROM users WHERE login = %s AND active",
        (body.login.strip().lower(),),
    ).fetchone()
    ok = verify_password(body.password, row["password_hash"] if row else DUMMY_HASH)
    if not row or not ok:
        raise ApiError(401, "bad_credentials", "Неверный логин или пароль")

    token = new_session_token()
    with conn.transaction():
        conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (%s, %s, now() + %s)",
            (token_hash(token), row["id"], timedelta(days=settings.session_days)),
        )
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=settings.session_days * 86400,
        httponly=True, samesite="lax", secure=settings.cookie_secure, path="/",
    )
    user = User(id=row["id"], login=row["login"], name=row["name"], role=row["role"])
    return _user_out(user)


@router.post("/logout")
def logout(request: Request, response: Response, conn: Connection = Depends(get_conn)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with conn.transaction():
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash(token),))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user_out(user)
