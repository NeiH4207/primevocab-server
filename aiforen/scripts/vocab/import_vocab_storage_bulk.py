"""Fast bulk import from vocab_storage using psycopg2 (sync).

Use when async row-by-row import is too slow over Railway public proxy.

  DATABASE_URL=postgresql://... python -m aiforen.scripts.vocab.import_vocab_storage_bulk
  python -m aiforen.scripts.vocab.import_vocab_storage_bulk --questions-only
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import psycopg2
import psycopg2.extras
from loguru import logger

from aiforen.repositories.pg.vocab_lexicon import lexeme_id_for
from aiforen.scripts.vocab.oxford_packs import normalize_pos
from aiforen.scripts.vocab.pack_membership import (
    DEFAULT_STORAGE,
    build_pack_membership_items,
    lexeme_id_for_vocab_row,
    pack_ids_for_vocab_row,
)
from aiforen.scripts.vocab.pack_specs import infer_stat_labels
from aiforen.scripts.vocab.quiz_import_utils import (
    question_row_from_quiz,
    track_id_from_level,
)

BATCH = 500


def _normalize_pos(raw: str) -> str:
    return normalize_pos((raw or "noun").strip().lower())


def _pg_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("Set DATABASE_URL (postgresql://, not +asyncpg)")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _connect_pg():
    return psycopg2.connect(
        _pg_url(),
        connect_timeout=60,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def _lid(row: Dict[str, Any]) -> uuid.UUID:
    lemma = (row.get("lemma") or row.get("display_word") or "").strip().lower()
    pos = _normalize_pos(row.get("pos") or "noun")
    return lexeme_id_for(lemma, pos)


def import_lexemes(conn, rows: List[Dict[str, Any]]) -> None:
    cur = conn.cursor()
    buffer: List[Tuple[Any, ...]] = []
    for row in rows:
        lemma = (row.get("lemma") or row.get("display_word") or "").strip().lower()
        if not lemma:
            continue
        pos = _normalize_pos(row.get("pos") or "noun")
        lid = lexeme_id_for(lemma, pos)
        bmin = row.get("ielts_band_min")
        bmax = row.get("ielts_band_max")
        try:
            bmin_f = float(bmin) if bmin is not None else None
        except (TypeError, ValueError):
            bmin_f = None
        try:
            bmax_f = float(bmax) if bmax is not None else None
        except (TypeError, ValueError):
            bmax_f = None
        buffer.append(
            (
                str(lid),
                lemma,
                (row.get("display_word") or lemma).strip()[:128],
                pos,
                row.get("cefr_level"),
                bmin_f,
                bmax_f,
                row.get("gre_tier"),
                (bmin_f or 0) >= 7.0,
                ["ielts"],
                "approved",
            )
        )
    for i in range(0, len(buffer), BATCH):
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO vocab_lexemes (
              id, lemma, display_word, pos, cefr_level,
              ielts_band_min, ielts_band_max, gre_tier, is_academic,
              exam_types, status, created_at, updated_at
            ) VALUES (
              %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
              display_word = EXCLUDED.display_word,
              cefr_level = COALESCE(EXCLUDED.cefr_level, vocab_lexemes.cefr_level),
              ielts_band_min = COALESCE(EXCLUDED.ielts_band_min, vocab_lexemes.ielts_band_min),
              ielts_band_max = COALESCE(EXCLUDED.ielts_band_max, vocab_lexemes.ielts_band_max),
              status = EXCLUDED.status,
              updated_at = NOW()
            """,
            buffer[i : i + BATCH],
        )
        conn.commit()
    cur.close()
    logger.info("Lexemes upserted {}", len(buffer))


def import_senses(conn, rows: List[Dict[str, Any]]) -> None:
    cur = conn.cursor()
    order_by_lexeme: Dict[uuid.UUID, int] = {}
    updated = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        params: List[Tuple[Any, ...]] = []
        sense_ins: List[Tuple[str, str, int, str, Any]] = []
        for row in chunk:
            lid = _lid(row)
            tips = row.get("tips") if isinstance(row.get("tips"), list) else []
            syns = row.get("synonyms") if isinstance(row.get("synonyms"), list) else []
            def_en = (row.get("definition_en") or row.get("lemma") or "")[:8000]
            raw_sid = (row.get("sense_id") or "").strip()
            try:
                sense_id = str(uuid.UUID(raw_sid)) if raw_sid else str(uuid.uuid4())
            except ValueError:
                sense_id = str(uuid.uuid4())
            raw_order = row.get("sense_order")
            if raw_order is not None:
                sense_order = max(1, int(raw_order))
            else:
                order_by_lexeme[lid] = order_by_lexeme.get(lid, 0) + 1
                sense_order = order_by_lexeme[lid]
            sense_ins.append(
                (sense_id, str(lid), sense_order, def_en, row.get("vi_gloss"))
            )
            params.append(
                (
                    def_en,
                    row.get("vi_gloss"),
                    row.get("usage_note") or row.get("common_mistake"),
                    row.get("example") or row.get("ielts_example"),
                    row.get("gre_example"),
                    row.get("phonetic"),
                    row.get("audio_url"),
                    [row.get("pack_id") or "general"],
                    json.dumps(tips),
                    json.dumps(syns),
                    sense_id,
                )
            )
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO vocab_senses (
              id, lexeme_id, sense_order, definition_en, vi_gloss,
              created_at, updated_at
            ) VALUES (%s::uuid, %s::uuid, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE SET
              lexeme_id = EXCLUDED.lexeme_id,
              sense_order = EXCLUDED.sense_order,
              definition_en = EXCLUDED.definition_en,
              vi_gloss = COALESCE(EXCLUDED.vi_gloss, vocab_senses.vi_gloss),
              updated_at = NOW()
            """,
            sense_ins,
        )
        psycopg2.extras.execute_batch(
            cur,
            """
            UPDATE vocab_senses SET
              definition_en = %s,
              vi_gloss = %s,
              usage_note = %s,
              ielts_example = %s,
              gre_example = %s,
              phonetic = %s,
              audio_url = %s,
              topic_tags = %s,
              tips = %s::jsonb,
              synonyms = %s::jsonb,
              updated_at = NOW()
            WHERE id = %s::uuid
            """,
            params,
        )
        updated += len(chunk)
        conn.commit()
        logger.info("Senses updated {}/{}", min(i + BATCH, len(rows)), len(rows))
    cur.close()


def upsert_pack_items_for_rows(conn, rows: List[Dict[str, Any]]) -> None:
    """Add/update pack_items for given rows only (no pack-wide DELETE)."""

    cur = conn.cursor()
    touched_packs: set[str] = set()
    upserted = 0
    for row in rows:
        lid = lexeme_id_for_vocab_row(row)
        order = int(row.get("vocab_index") or 0)
        labels = infer_stat_labels((row.get("lemma") or row.get("display_word") or ""))
        for pack_id in pack_ids_for_vocab_row(row):
            cur.execute(
                """
                INSERT INTO vocab_pack_items (
                  id, pack_id, lexeme_id, order_index, is_core, stat_labels
                ) VALUES (gen_random_uuid(), %s, %s::uuid, %s, %s, %s)
                ON CONFLICT (pack_id, lexeme_id) DO UPDATE SET
                  order_index = EXCLUDED.order_index,
                  stat_labels = EXCLUDED.stat_labels
                """,
                (pack_id, str(lid), order, True, labels),
            )
            touched_packs.add(pack_id)
            upserted += 1
    conn.commit()
    for pack_id in sorted(touched_packs):
        cur.execute(
            "SELECT count(*) FROM vocab_pack_items WHERE pack_id = %s",
            (pack_id,),
        )
        n = cur.fetchone()[0]
        cur.execute(
            """
            UPDATE vocab_packs SET
              target_word_count = %s,
              completed_word_count = %s,
              content_status = 'complete',
              updated_at = NOW()
            WHERE pack_id = %s
            """,
            (n, n, pack_id),
        )
    conn.commit()
    cur.close()
    logger.info(
        "Pack items upserted {} links across {} packs", upserted, len(touched_packs)
    )


def import_packs(conn, rows: List[Dict[str, Any]]) -> None:
    by_pack = build_pack_membership_items(rows, lid_for_row=lexeme_id_for_vocab_row)
    multi = sum(1 for r in rows if len(pack_ids_for_vocab_row(r)) > 1)
    logger.info(
        "Pack membership: {} packs, {} rows with multiple source_packs",
        len(by_pack),
        multi,
    )

    cur = conn.cursor()
    for pack_id, items in sorted(by_pack.items()):
        items.sort(key=lambda t: t[0])
        cur.execute("DELETE FROM vocab_pack_items WHERE pack_id = %s", (pack_id,))
        insert_rows = []
        for idx, (order, lid, labels) in enumerate(items):
            insert_rows.append((pack_id, str(lid), idx, True, labels))
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO vocab_pack_items (id, pack_id, lexeme_id, order_index, is_core, stat_labels)
            VALUES (gen_random_uuid(), %s, %s::uuid, %s, %s, %s)
            ON CONFLICT (pack_id, lexeme_id) DO UPDATE SET
              order_index = EXCLUDED.order_index,
              stat_labels = EXCLUDED.stat_labels
            """,
            [(r[0], r[1], r[2], r[3], r[4]) for r in insert_rows],
            page_size=BATCH,
        )
        cur.execute(
            """
            UPDATE vocab_packs SET
              target_word_count = %s,
              completed_word_count = %s,
              content_status = 'complete'
            WHERE pack_id = %s
            """,
            (len(items), len(items), pack_id),
        )
        conn.commit()
        logger.info("Pack {} — {} items", pack_id, len(items))
    cur.close()


def import_questions(
    conn, storage: Path, *, level_codes: List[str] | None = None
) -> None:
    cur = conn.cursor()
    quiz_paths = sorted(storage.glob("quiz_*_vocab.json"))
    order = {"A1": 0, "A2": 1, "B1": 2, "B2": 3, "C1": 4, "C2": 5, "IELTS": 6, "GRE": 7}

    def sort_key(p: Path) -> Tuple[int, str]:
        level = json.loads(p.read_text(encoding="utf-8")).get("level_code") or ""
        return (order.get(str(level).upper(), 99), p.name)

    quiz_paths = sorted(quiz_paths, key=sort_key)

    if level_codes:
        want = {str(code).strip().upper() for code in level_codes if str(code).strip()}
        quiz_paths = [
            path
            for path in quiz_paths
            if str(json.loads(path.read_text(encoding="utf-8")).get("level_code") or "")
            .strip()
            .upper()
            in want
        ]
        if not quiz_paths:
            raise SystemExit(f"No quiz files for level(s): {sorted(want)}")
        storage_files = [path.name for path in quiz_paths]
        cur.execute(
            """
            DELETE FROM vocab_questions
            WHERE generator_meta->>'source' = 'vocab_storage'
              AND generator_meta->>'storage_file' = ANY(%s)
            """,
            (storage_files,),
        )
        logger.info(
            "Removed {} prior vocab_storage questions for {}",
            cur.rowcount,
            ", ".join(sorted(want)),
        )
    else:
        cur.execute(
            "DELETE FROM vocab_questions WHERE generator_meta->>'source' = 'vocab_storage'"
        )
        logger.info("Removed {} prior vocab_storage questions", cur.rowcount)
    conn.commit()

    cur.execute(
        "SELECT lexeme_id::text, id::text FROM vocab_senses WHERE sense_order = 1"
    )
    sense_by_lexeme: Dict[str, str] = dict(cur.fetchall())
    cur.execute("SELECT id::text FROM vocab_senses")
    valid_sense_ids = {row[0] for row in cur.fetchall()}

    # One row per (sense|lexeme, track, task_type, mastery_slot); later quiz files win.
    pending: Dict[Tuple[str, str, str, int], Tuple[Any, ...]] = {}
    skipped = 0

    for path in quiz_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        level_code = str(data.get("level_code") or "B1")
        track_id = track_id_from_level(level_code)
        for item in data.get("items") or []:
            ref = item.get("vocab_ref") or {}
            lemma = (ref.get("lemma") or ref.get("display_word") or "").strip().lower()
            if not lemma:
                continue
            lid = lexeme_id_for(lemma, _normalize_pos(ref.get("pos") or "noun"))
            raw_sid = (ref.get("sense_id") or "").strip()
            sense_id = sense_by_lexeme.get(str(lid))
            if raw_sid and raw_sid in valid_sense_ids:
                sense_id = raw_sid
            for q in item.get("questions") or []:
                row = question_row_from_quiz(
                    q,
                    lexeme_id=str(lid),
                    sense_id=str(sense_id) if sense_id else None,
                    track_id=track_id,
                    level_code=level_code,
                    storage_file=path.name,
                )
                if not row:
                    skipped += 1
                    continue
                slot = max(1, min(5, int(q.get("mastery_slot") or 1)))
                task_type = row[4]
                slot_key = str(sense_id) if sense_id else str(lid)
                key = (slot_key, track_id, task_type, slot)
                pending[key] = row

    rows = list(pending.values())
    inserted = 0
    cur.close()
    conn.close()
    q_batch = 200
    for i in range(0, len(rows), q_batch):
        chunk = rows[i : i + q_batch]
        for attempt in range(6):
            conn = None
            try:
                conn = _connect_pg()
                cur = conn.cursor()
                _flush_questions(cur, conn, chunk)
                cur.close()
                conn.close()
                break
            except psycopg2.OperationalError as exc:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                if attempt >= 5:
                    raise
                wait = min(30, 2**attempt)
                logger.warning(
                    "Question batch {}-{} failed (attempt {}): {} — retry in {}s",
                    i,
                    i + len(chunk),
                    attempt + 1,
                    exc,
                    wait,
                )
                time.sleep(wait)
        inserted += len(chunk)
        if inserted % 5000 == 0 or inserted == len(rows):
            logger.info("Questions inserted: {}/{}", inserted, len(rows))
    logger.info(
        "Questions done: inserted={}, unique_slots={}, skipped={}",
        inserted,
        len(pending),
        skipped,
    )


def _flush_questions(cur, conn, rows: List[Tuple[Any, ...]]) -> None:
    psycopg2.extras.execute_batch(
        cur,
        """
        INSERT INTO vocab_questions (
          id, lexeme_id, sense_id, track_id, task_type, skill, level_code,
          mastery_slot, interaction_kind, prompt, options, correct_option_id,
          explanation, difficulty, status, payload, generator_meta,
          created_at, updated_at
        ) VALUES (
          %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s,
          %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s::jsonb, NOW(), NOW()
        )
        """,
        rows,
        page_size=BATCH,
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions-only", action="store_true")
    parser.add_argument("--skip-questions", action="store_true")
    parser.add_argument("--skip-packs", action="store_true")
    parser.add_argument("--skip-lexemes", action="store_true")
    parser.add_argument("--skip-senses", action="store_true")
    parser.add_argument(
        "--packs-only",
        action="store_true",
        help="Rebuild vocab_pack_items only (skip lexemes, senses, questions)",
    )
    parser.add_argument(
        "--pack-items-only",
        action="store_true",
        help="Upsert pack_items for selected rows only (no DELETE per pack)",
    )
    parser.add_argument(
        "--lemma",
        action="append",
        metavar="LEMMA",
        help="Only process row(s) with this lemma (repeatable, e.g. --lemma aids)",
    )
    parser.add_argument(
        "--quiz-levels",
        metavar="LEVEL",
        help="Comma-separated quiz level codes to import (e.g. A1,A2,GRE). "
        "With --questions-only, replaces only those quiz files.",
    )
    args = parser.parse_args()
    if args.packs_only:
        args.skip_lexemes = True
        args.skip_senses = True
        args.skip_questions = True
    if args.pack_items_only:
        args.skip_questions = True

    storage = Path(os.environ.get("VOCAB_STORAGE_DIR", DEFAULT_STORAGE))
    vocab_path = storage / "vocab_full_table.json"
    rows = json.loads(vocab_path.read_text(encoding="utf-8"))
    if args.lemma:
        want = {str(x).strip().lower() for x in args.lemma if str(x).strip()}
        rows = [
            r
            for r in rows
            if (r.get("lemma") or r.get("display_word") or "").strip().lower() in want
        ]
        logger.info("Filtered to {} row(s) for lemma(s) {}", len(rows), sorted(want))
        if not rows:
            raise SystemExit(f"No vocab rows for lemma(s): {sorted(want)}")
    logger.info("Loaded {} vocab rows to process", len(rows))

    quiz_levels: List[str] | None = None
    if args.quiz_levels:
        quiz_levels = [
            part.strip().upper() for part in args.quiz_levels.split(",") if part.strip()
        ]
        if not quiz_levels:
            raise SystemExit("--quiz-levels must list at least one level code")

    conn = _connect_pg()
    conn.autocommit = False
    try:
        if not args.questions_only:
            if not args.skip_lexemes:
                logger.info("Upserting lexemes…")
                import_lexemes(conn, rows)
            if args.pack_items_only:
                logger.info("Upserting pack items (selected rows only)…")
                upsert_pack_items_for_rows(conn, rows)
            elif not args.skip_packs:
                logger.info("Rebuilding pack items…")
                import_packs(conn, rows)
            if not args.skip_senses:
                logger.info("Updating senses…")
                import_senses(conn, rows)
        if not args.skip_questions:
            import_questions(conn, storage, level_codes=quiz_levels)
    finally:
        conn.close()
    logger.info("Bulk import complete")


if __name__ == "__main__":
    main()
