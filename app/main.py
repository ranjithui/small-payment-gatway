"""FeeFlow payment workflow service (FastAPI)."""
import asyncio
import contextlib
import logging
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from sqlalchemy import text

from app.bootstrap import bootstrap
from app.db import SessionLocal, create_all, get_engine, init_engine
from app.routers import admin, devices, pages, platform, public, students, workspace
from app.routers.pages import STATIC
from app.services.fees import generate_monthly_fees
from app.services.reminders import process_grace_expiry
from app.states import InvalidTransition

log = logging.getLogger("feeflow")


async def _jobs_loop() -> None:
    while True:
        await asyncio.sleep(settings.background_interval_seconds)
        try:
            db = SessionLocal()
            try:
                process_grace_expiry(db)
                generate_monthly_fees(db)
            finally:
                db.close()
        except Exception:  # pragma: no cover - keep the loop alive
            log.exception("background job failed")


def create_app(database_url: Optional[str] = None, run_jobs: Optional[bool] = None) -> FastAPI:
    if database_url is None:  # real server start (tests pass an explicit URL)
        settings.validate()
    init_engine(database_url or settings.database_url)
    create_all()
    if database_url is None:
        db = SessionLocal()
        try:
            bootstrap(db)
        finally:
            db.close()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if settings.background_jobs if run_jobs is None else run_jobs:
            task = asyncio.create_task(_jobs_loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(
        title="FeeFlow Payment Workflow API",
        version="2.1.0",
        description="One-tap 'I have paid' declarations with administrator verification.",
        lifespan=lifespan,
    )

    @app.exception_handler(InvalidTransition)
    async def _invalid_transition(_req: Request, exc: InvalidTransition):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.public_base_url.startswith("https://"):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    app.include_router(public.router)  # public routes first (path precedence)
    app.include_router(workspace.router)
    app.include_router(students.router)
    app.include_router(platform.router)
    app.include_router(admin.router)
    app.include_router(devices.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


# Run with:  uvicorn app.main:create_app --factory --reload
# Behind a proxy (Render etc.) add: --proxy-headers --forwarded-allow-ips="*"  so rate limits see real client IPs.
