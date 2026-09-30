"""FastAPI application factory for the public research API."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.routers.research import router as research_router
from backend.app.routers.status import router as status_router
from backend.app.routers.validation import router as validation_router
from backend.app.routers.portfolios import router as portfolio_router
from backend.app.routers.admin import router as admin_router
from quant.admin import admin_mode_enabled


def _cors_origins(value: str | None) -> list[str]:
    return [origin.strip() for origin in (value or "").split(",") if origin.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title="Quant Research API", version="0.1.0")
    admin_enabled = admin_mode_enabled(os.getenv("QUANT_ADMIN_MODE"))
    origins = _cors_origins(os.getenv("QUANT_CORS_ORIGINS"))
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type"],
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": "服务暂时不可用，请稍后重试。"},
        )

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/capabilities")
    def capabilities() -> dict[str, bool]:
        return {"admin_enabled": admin_enabled}

    app.include_router(research_router)
    app.include_router(status_router)
    app.include_router(validation_router)
    app.include_router(portfolio_router)
    if admin_enabled:
        app.include_router(admin_router)
    return app


app = create_app()
