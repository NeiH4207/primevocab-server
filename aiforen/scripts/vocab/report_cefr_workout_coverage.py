"""Report CEFR workout-ready quiz coverage by level, slot, and interaction."""

from __future__ import annotations

from sqlalchemy import func, select

from aiforen.domain.sql_models import VocabQuestion
from aiforen.scripts.vocab._common import pg_session, run_async


async def main() -> None:
    async for repo in pg_session():
        rows = (
            await repo.s.execute(
                select(
                    VocabQuestion.track_id,
                    VocabQuestion.mastery_slot,
                    VocabQuestion.interaction_kind,
                    func.count(),
                )
                .where(
                    VocabQuestion.track_id.like("cefr:%"),
                    VocabQuestion.status.in_(("validated", "approved")),
                    VocabQuestion.quality_tier.in_(("good", "excellent", "elite")),
                )
                .group_by(
                    VocabQuestion.track_id,
                    VocabQuestion.mastery_slot,
                    VocabQuestion.interaction_kind,
                )
                .order_by(
                    VocabQuestion.track_id,
                    VocabQuestion.mastery_slot,
                    VocabQuestion.interaction_kind,
                )
            )
        ).all()
        print("track_id\tslot\tinteraction\tworkout_ready_questions")
        for track_id, slot, interaction, count in rows:
            print(f"{track_id}\t{slot}\t{interaction}\t{count}")


if __name__ == "__main__":
    run_async(main())
