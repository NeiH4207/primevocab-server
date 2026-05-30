"""FastAPI application factory.

We avoid global state so tests can spin up parallel apps.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from aiforen.api.v1.router import api_router
from aiforen.core import db as core_db
from aiforen.core.config import get_settings
from aiforen.core.errors import register_exception_handlers
from aiforen.core.schema_repair import apply_pg_schema_repairs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logger.info("Starting Aiforen API ({})", settings.app_env)
    from aiforen.core.db import init_pg

    engine = init_pg()
    try:
        await apply_pg_schema_repairs(engine)
    except Exception as exc:
        logger.warning("Postgres schema repair skipped or partial: {}", exc)
    await core_db.ping_all()
    yield
    await core_db.shutdown_all()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="3.0.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/")
    async def root():
        return {
            "service": settings.app_name,
            "docs": "/docs",
            "api": settings.api_v1_prefix,
        }

    return app


app = create_app()
