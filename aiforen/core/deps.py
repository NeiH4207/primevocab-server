"""FastAPI dependencies: DB sessions, current user, plan guards."""

from __future__ import annotations

import uuid
from typing import AsyncIterator, Optional

import jwt
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.repositories.pg.users import UserRepo

from . import db as core_db
from . import security
from .errors import Forbidden, Unauthorized


async def get_pg() -> AsyncIterator[AsyncSession]:
    sm = core_db.pg_sessionmaker()
    session = sm()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def get_redis():
    return core_db.redis_client()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class CurrentUser:
    """Lightweight current-user object passed to handlers."""

    def __init__(self, user_id: str, email: str, plan_code: str, claims: dict):
        self.id = user_id
        self.email = email
        self.plan_code = plan_code
        self.claims = claims

    @property
    def is_paid(self) -> bool:
        return self.plan_code not in ("free", "guest")


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
) -> CurrentUser:
    token = _bearer(authorization)
    if not token:
        raise Unauthorized("Missing bearer token")
    try:
        claims = security.decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Token expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid token") from exc

    if claims.get("typ") != "access":
        raise Unauthorized("Wrong token type")

    user_id = claims.get("sub")
    if not user_id:
        raise Unauthorized("Token missing subject")

    return CurrentUser(
        user_id=user_id,
        email=claims.get("email", ""),
        plan_code=claims.get("plan", "free"),
        claims=claims,
    )


async def get_optional_user(
    authorization: Optional[str] = Header(default=None),
) -> Optional[CurrentUser]:
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except Unauthorized:
        return None


async def get_current_admin(
    user: CurrentUser = Depends(get_current_user),
    pg: AsyncSession = Depends(get_pg),
) -> CurrentUser:
    """JWT + Postgres is_admin check (admin console)."""
    try:
        uid = uuid.UUID(user.id)
    except ValueError as exc:
        raise Forbidden("Invalid user id") from exc
    row = await UserRepo(pg).get(uid)
    if not row or not row.is_admin:
        raise Forbidden("Admin access required")
    return user


def require_plan(*allowed_plans: str):
    async def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.plan_code not in allowed_plans:
            raise Forbidden(
                f"Plan '{user.plan_code}' is not allowed; requires one of {allowed_plans}"
            )
        return user

    return _guard
