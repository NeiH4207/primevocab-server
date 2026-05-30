"""Production cleanup: quiz rows from vocab_storage only; optional legacy sense prompts.

  DATABASE_URL=postgresql://... python -m aiforen.scripts.vocab.cleanup_vocab_storage --dry-run
  DATABASE_URL=postgresql://... python -m aiforen.scripts.vocab.cleanup_vocab_storage
  DATABASE_URL=postgresql://... python -m aiforen.scripts.vocab.cleanup_vocab_storage --clear-legacy-prompts
"""

from __future__ import annotations

import argparse
import os

import psycopg2

VOCAB_STORAGE = "vocab_storage"


def _pg_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("Set DATABASE_URL (postgresql://, not +asyncpg)")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Keep vocab quiz + senses aligned with quiz_*_vocab.json SSOT"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts only; do not DELETE/UPDATE",
    )
    parser.add_argument(
        "--clear-legacy-prompts",
        action="store_true",
        help="SET vi_translate_prompt and topic_prompt to NULL on all senses",
    )
    parser.add_argument(
        "--skip-questions",
        action="store_true",
        help="Only run --clear-legacy-prompts (if set)",
    )
    args = parser.parse_args()

    conn = psycopg2.connect(_pg_url())
    cur = conn.cursor()

    if not args.skip_questions:
        cur.execute(
            """
            SELECT coalesce(generator_meta->>'source', '(null)'), count(*)
            FROM vocab_questions
            GROUP BY 1
            ORDER BY 2 DESC
            """
        )
        print("vocab_questions by source:")
        for src, n in cur.fetchall():
            print(f"  {src}: {n}")

        cur.execute(
            """
            SELECT count(*) FROM vocab_questions
            WHERE coalesce(generator_meta->>'source', '') <> %s
            """,
            (VOCAB_STORAGE,),
        )
        orphan = cur.fetchone()[0]
        print(f"\nNon-{VOCAB_STORAGE} questions to delete: {orphan}")

        cur.execute(
            f"""
            SELECT count(*) FROM vocab_questions old
            WHERE coalesce(old.generator_meta->>'source', '') <> %s
              AND EXISTS (
                SELECT 1 FROM vocab_questions v
                WHERE v.lexeme_id = old.lexeme_id
                  AND v.track_id = old.track_id
                  AND v.task_type = old.task_type
                  AND coalesce(v.mastery_slot, -1) = coalesce(old.mastery_slot, -1)
                  AND v.generator_meta->>'source' = %s
              )
            """,
            (VOCAB_STORAGE, VOCAB_STORAGE),
        )
        safe = cur.fetchone()[0]
        print(f"  (of which have a {VOCAB_STORAGE} replacement: {safe})")

        if not args.dry_run and orphan:
            cur.execute(
                """
                DELETE FROM vocab_questions
                WHERE coalesce(generator_meta->>'source', '') <> %s
                """,
                (VOCAB_STORAGE,),
            )
            deleted = cur.rowcount
            conn.commit()
            print(f"Deleted {deleted} non-{VOCAB_STORAGE} questions.")

    if args.clear_legacy_prompts:
        cur.execute(
            """
            SELECT count(*) FROM vocab_senses
            WHERE vi_translate_prompt IS NOT NULL OR topic_prompt IS NOT NULL
            """
        )
        n = cur.fetchone()[0]
        print(f"\nSenses with legacy translate/topic prompts: {n}")
        if not args.dry_run and n:
            cur.execute(
                """
                UPDATE vocab_senses
                SET vi_translate_prompt = NULL,
                    topic_prompt = NULL,
                    updated_at = NOW()
                WHERE vi_translate_prompt IS NOT NULL OR topic_prompt IS NOT NULL
                """
            )
            conn.commit()
            print(f"Cleared prompts on {cur.rowcount} senses.")

    cur.close()
    conn.close()
    if args.dry_run:
        print("\n(dry-run — no changes written)")


if __name__ == "__main__":
    main()
