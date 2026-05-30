"""Stub for the FE `/login-as` social-login redirect flow (development only)."""

from __future__ import annotations

from base64 import urlsafe_b64encode
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from aiforen.core.config import get_settings
from aiforen.core.errors import AppError, NotFound

router = APIRouter()
settings = get_settings()


def _require_dev() -> None:
    if settings.app_env != "dev":
        raise NotFound("Not found")


def _safe_frontend_origin(origin: Optional[str]) -> str:
    allowed = {settings.frontend_base_url.rstrip("/")}
    allowed.update(item.rstrip("/") for item in settings.cors_origins)
    if not origin:
        return settings.frontend_base_url

    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return settings.frontend_base_url

    candidate = f"{parsed.scheme}://{parsed.netloc}"
    return candidate if candidate in allowed else settings.frontend_base_url


@router.get("/login-as")
async def login_as(provider: str, origin: Optional[str] = None):
    _require_dev()
    provider_key = provider.strip().lower()
    if provider_key not in {"google", "facebook"}:
        raise AppError("Unsupported development login provider", status_code=400)

    fake_id_token = jwt.encode(
        {
            "sub": f"dev-{provider_key}-12345",
            "email": f"{provider_key}-demo@aiforen.local",
            "name": f"{provider_key.title()} Demo",
            "picture": "/images/avatar-default.png",
            "iat": int(datetime.utcnow().timestamp()),
        },
        "dev",
        algorithm="HS256",
    )
    encoded = urlsafe_b64encode(fake_id_token.encode()).decode().rstrip("=")
    target = _safe_frontend_origin(origin)
    return RedirectResponse(
        f"{target}/auth/callback?code={encoded}&state={provider_key}",
        status_code=302,
    )


@router.get("/login-as/callback")
async def login_as_callback(request: Request):
    _require_dev()
    return {
        "status": "success",
        "data": {"message": "Use /auth/google to exchange the token"},
    }
