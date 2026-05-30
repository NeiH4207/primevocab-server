"""Authentication endpoints.

Frontend contract:
  - POST /auth/google       { id_token } | { access_token } -> tokens + user
  - POST /auth/google/code  { code, redirect_uri } -> tokens + user (redirect UX)
  - POST /auth/login      { email, password }
  - POST /auth/refresh    { refresh_token }
  - POST /auth/logout     { refresh_token? }
  - POST /sign-up         { email, password, confirm_password }
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.core.deps import get_pg
from aiforen.services.auth_service import AuthService

router = APIRouter()
legacy_router = APIRouter()


class GoogleIn(BaseModel):
    """FE may send `id_token` (GoogleLogin / One Tap) or `access_token` (useGoogleLogin popup)."""

    id_token: Optional[str] = None
    access_token: Optional[str] = None

    @model_validator(mode="after")
    def _require_one_token(self):
        has_id = bool(self.id_token and self.id_token.strip())
        has_access = bool(self.access_token and self.access_token.strip())
        if has_id == has_access:
            if not has_id:
                raise ValueError("Either id_token or access_token is required")
            raise ValueError("Provide only id_token or access_token, not both")
        return self


class GoogleCodeIn(BaseModel):
    code: str
    redirect_uri: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class SignupIn(BaseModel):
    email: EmailStr
    password: str
    confirm_password: str


def _meta(req: Request) -> tuple[Optional[str], Optional[str]]:
    return req.headers.get("user-agent"), (req.client.host if req.client else None)


@router.post("/google")
async def google_auth(
    payload: GoogleIn, request: Request, pg: AsyncSession = Depends(get_pg)
):
    ua, ip = _meta(request)
    auth = AuthService(pg)
    data = await auth.login_with_google(
        id_token=payload.id_token,
        access_token=payload.access_token,
        user_agent=ua,
        ip=ip,
    )
    return {"status": "success", "data": data}


@router.post("/google/code")
async def google_auth_code(
    payload: GoogleCodeIn, request: Request, pg: AsyncSession = Depends(get_pg)
):
    ua, ip = _meta(request)
    auth = AuthService(pg)
    data = await auth.login_with_google_code(
        code=payload.code,
        redirect_uri=payload.redirect_uri,
        user_agent=ua,
        ip=ip,
    )
    return {"status": "success", "data": data}


@router.post("/login")
async def login(payload: LoginIn, request: Request, pg: AsyncSession = Depends(get_pg)):
    ua, ip = _meta(request)
    auth = AuthService(pg)
    data = await auth.sign_in_with_password(
        email=payload.email, password=payload.password, user_agent=ua, ip=ip
    )
    return {"status": "success", "data": data}


@router.post("/refresh")
async def refresh(
    payload: RefreshIn, request: Request, pg: AsyncSession = Depends(get_pg)
):
    ua, ip = _meta(request)
    auth = AuthService(pg)
    data = await auth.refresh(refresh_token=payload.refresh_token, user_agent=ua, ip=ip)
    return {"status": "success", "data": data}


@router.post("/logout")
async def logout(
    payload: Optional[RefreshIn] = None, pg: AsyncSession = Depends(get_pg)
):
    auth = AuthService(pg)
    await auth.logout(refresh_token=payload.refresh_token if payload else None)
    return {"status": "success"}


@legacy_router.post("/sign-up")
async def sign_up(
    payload: SignupIn, request: Request, pg: AsyncSession = Depends(get_pg)
):
    ua, ip = _meta(request)
    auth = AuthService(pg)
    data = await auth.sign_up(
        email=payload.email,
        password=payload.password,
        confirm_password=payload.confirm_password,
        user_agent=ua,
        ip=ip,
    )
    return {"status": "success", "data": data}
