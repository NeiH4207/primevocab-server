"""Top-level v1 API router."""

from __future__ import annotations

from fastapi import APIRouter

from .endpoints import (
    admin,
    auth,
    health,
    leaderboard,
    learning,
    login_as,
    payments,
    users,
    writing,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/auth", tags=["🔐 Auth"])
api_router.include_router(users.router, prefix="/auth", tags=["🔐 Auth"])
# Some FE pages already POST to /sign-up at the v1 root.
api_router.include_router(auth.legacy_router, tags=["🔐 Auth"])
api_router.include_router(login_as.router, tags=["🔐 Auth"])
api_router.include_router(writing.router, prefix="/writing", tags=["✍️ Writing"])
api_router.include_router(learning.router, prefix="/learning", tags=["📚 Learning"])
api_router.include_router(payments.router, prefix="/payment", tags=["💳 Payments"])
api_router.include_router(admin.router, prefix="/admin", tags=["🛡️ Admin"])
api_router.include_router(
    leaderboard.router, prefix="/leaderboard", tags=["🏆 Leaderboard"]
)
