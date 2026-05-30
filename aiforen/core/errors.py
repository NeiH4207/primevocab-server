"""Centralised exception hierarchy + FastAPI handlers."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from aiforen.core.config import get_settings


class AppError(Exception):
    status_code: int = 400
    code: str = "app_error"

    def __init__(
        self, message: str, *, code: str | None = None, status_code: int | None = None
    ):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class QuotaExceeded(AppError):
    status_code = 402  # Payment Required
    code = "quota_exceeded"


def _cors_headers(request: Request) -> Dict[str, str]:
    """Exception responses bypass CORSMiddleware; mirror allowed origins on errors."""
    origin = request.headers.get("origin")
    if not origin:
        return {}
    settings = get_settings()
    if origin not in (settings.cors_origins or []):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
    }


def _payload(message: str, code: str, **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "status": "error",
        "code": code,
        "message": message,
    }
    if extra:
        body.update(extra)
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.message, exc.code),
            headers=_cors_headers(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(str(exc.detail), "http_error"),
            headers=_cors_headers(request),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_payload(
                "Invalid request payload", "validation_error", errors=exc.errors()
            ),
            headers=_cors_headers(request),
        )

    @app.exception_handler(Exception)
    async def fallback_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on {} {}", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_payload("Internal server error", "internal_error"),
            headers=_cors_headers(request),
        )
