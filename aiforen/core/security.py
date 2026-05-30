"""JWT + password helpers.

Access tokens are short-lived JWTs.  Refresh tokens are opaque random
strings — only their SHA-256 hash is stored in Postgres so a leaked DB
dump cannot replay sessions.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt

from .config import get_settings

settings = get_settings()


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Access tokens (JWT)
# ---------------------------------------------------------------------------


def issue_access_token(
    user_id: str,
    *,
    extra: Optional[Dict[str, Any]] = None,
    ttl_minutes: Optional[int] = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int(
            (
                now + timedelta(minutes=ttl_minutes or settings.jwt_access_ttl_minutes)
            ).timestamp()
        ),
        "jti": secrets.token_urlsafe(16),
        "typ": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ---------------------------------------------------------------------------
# Refresh tokens (opaque)
# ---------------------------------------------------------------------------


def new_refresh_token() -> str:
    """Return a fresh opaque token.  Send to client; only its hash hits the DB."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_ttl_days)
