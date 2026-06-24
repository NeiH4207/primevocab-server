"""Reset coaching state and grant premium for a user by email.

Usage:
  python -m aiforen.scripts.admin.coaching_user_setup ngocanh13112k@gmail.com
  python -m aiforen.scripts.admin.coaching_user_setup user@example.com --no-premium
  python -m aiforen.scripts.admin.coaching_user_setup user@example.com --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from loguru import logger

from aiforen.core import db as core_db
from aiforen.repositories.pg.plans import SubscriptionRepo
from aiforen.repositories.pg.users import UserRepo
from aiforen.services.vocab_coaching_service import VocabCoachingService


async def setup_user(
    *,
    email: str,
    reset_coaching: bool = True,
    grant_premium: bool = True,
    premium_months: int = 12,
    dry_run: bool = False,
) -> dict:
    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        users = UserRepo(session)
        user = await users.by_email(email.strip().lower())
        if user is None:
            raise SystemExit(f"User not found: {email}")

        user_id = str(user.id)
        result: dict = {"user_id": user_id, "email": user.email}

        if reset_coaching:
            svc = VocabCoachingService(session)
            reset_out = await svc.reset(user_id=user_id)
            result["coaching_reset"] = reset_out

        if grant_premium:
            subs = SubscriptionRepo(session)
            existing = await subs.active_for_user(uuid.UUID(user_id))
            if existing and existing.plan_code == "premium":
                result["premium"] = {
                    "granted": False,
                    "reason": "already_active",
                    "plan_code": existing.plan_code,
                    "ends": existing.current_period_end.isoformat(),
                }
            else:
                sub = await subs.grant(
                    user_id=uuid.UUID(user_id),
                    plan_code="premium",
                    billing_cycle="annual",
                    months=premium_months,
                    price_paid=0.0,
                    payment_method="admin_grant",
                )
                result["premium"] = {
                    "granted": True,
                    "plan_code": sub.plan_code,
                    "ends": sub.current_period_end.isoformat(),
                }

        if dry_run:
            await session.rollback()
            result["dry_run"] = True
            logger.warning("Dry run — rolled back changes for {}", email)
        else:
            await session.commit()
            logger.info("Committed setup for {}: {}", email, result)

        return result


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset coaching + grant premium by email"
    )
    parser.add_argument("email", help="User email address")
    parser.add_argument("--no-reset", action="store_true", help="Skip coaching reset")
    parser.add_argument("--no-premium", action="store_true", help="Skip premium grant")
    parser.add_argument(
        "--months", type=int, default=12, help="Premium months (default 12)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Roll back after applying"
    )
    args = parser.parse_args()

    out = await setup_user(
        email=args.email,
        reset_coaching=not args.no_reset,
        grant_premium=not args.no_premium,
        premium_months=max(1, args.months),
        dry_run=args.dry_run,
    )
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
