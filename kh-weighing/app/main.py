import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .db import create_pool, migrate
from .deps import ApiError
from .routers import auth, catalog, summary, trips
from .security import hash_password

log = logging.getLogger("weighing")
STATIC_DIR = Path(__file__).parent / "static"


def _bootstrap_admin(pool, settings: Settings) -> None:
    """Первый запуск: если пользователей нет и заданы ADMIN_LOGIN/ADMIN_PASSWORD — создаём администратора."""
    if not (settings.admin_login and settings.admin_password):
        return
    with pool.connection() as conn, conn.transaction():
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return
        conn.execute(
            "INSERT INTO users (login, name, role, password_hash) VALUES (%s, %s, 'admin', %s)",
            (settings.admin_login.lower(), "Администратор", hash_password(settings.admin_password)),
        )
        log.info("Создан администратор %s", settings.admin_login)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = create_pool(settings.database_url)
        applied = migrate(pool)
        if applied:
            log.info("Применены миграции: %s", ", ".join(applied))
        _bootstrap_admin(pool, settings)
        app.state.pool = pool
        app.state.settings = settings
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(title="Весовая КХ «Есментаев»", version="1.0.0-mvp", lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError):
        body = {"code": exc.code, "detail": exc.message}
        if exc.field:
            body["field"] = exc.field
        return JSONResponse(status_code=exc.status, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        errors = [
            {"field": str(e["loc"][-1]) if e.get("loc") else None, "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"code": "validation_error", "detail": "Проверьте введённые данные", "errors": errors},
        )

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        path = request.url.path
        if path.startswith("/api/") or path in ("/", "/index.html", "/sw.js"):
            # API и оболочку не кэшируем в браузере: офлайн-кэш ведёт Service Worker
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/health", tags=["system"])
    def health():
        with app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(catalog.router)
    app.include_router(trips.router)
    app.include_router(summary.router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
