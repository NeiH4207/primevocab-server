#!/usr/bin/env python3
"""Compare pack membership counts: vocab_full_table.json vs Postgres vocab_pack_items.

Expected (JSON): unique lexemes per pack from source_packs on each row.
Actual (DB): COUNT(*) FROM vocab_pack_items GROUP BY pack_id.

Usage:
  python -m aiforen.scripts.vocab.verify_pack_membership
  DATABASE_URL=postgresql://... python -m aiforen.scripts.vocab.verify_pack_membership
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

from aiforen.scripts.vocab.pack_membership import (
    DEFAULT_STORAGE,
    build_pack_membership_items,
    lexeme_id_for_vocab_row,
    pack_ids_for_vocab_row,
)


def expected_counts_from_json(rows: list) -> dict[str, int]:
    by_pack = build_pack_membership_items(rows, lid_for_row=lexeme_id_for_vocab_row)
    return {pack_id: len(items) for pack_id, items in sorted(by_pack.items())}


def primary_only_counts(rows: list) -> Counter:
    c: Counter = Counter()
    for row in rows:
        pid = row.get("pack_id")
        if pid:
            c[str(pid)] += 1
    return c


def db_counts() -> dict[str, int]:
    url = os.environ.get("DATABASE_URL", "").strip().replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    if not url:
        return {}
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 not installed — skipping DB comparison.", file=sys.stderr)
        return {}

    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pack_id, count(*)::int
        FROM vocab_pack_items
        GROUP BY pack_id
        ORDER BY pack_id
        """
    )
    out = {str(pid): int(n) for pid, n in cur.fetchall()}
    conn.close()
    return out


def main() -> None:
    storage = Path(os.environ.get("VOCAB_STORAGE_DIR", DEFAULT_STORAGE))
    path = storage / "vocab_full_table.json"
    if not path.is_file():
        raise SystemExit(f"Missing {path}")

    rows = json.loads(path.read_text(encoding="utf-8"))
    expected = expected_counts_from_json(rows)
    legacy = primary_only_counts(rows)
    multi = sum(1 for r in rows if len(pack_ids_for_vocab_row(r)) > 1)
    actual = db_counts()

    pack_ids = sorted(set(expected) | set(legacy) | set(actual))
    print(f"vocab_full_table.json rows: {len(rows)}")
    print(f"rows with multiple source_packs: {multi}")
    print()
    print(
        f"{'pack_id':<22} {'primary':>8} {'membership':>11} {'db':>8} {'db_ok':>7}"
    )
    print("-" * 62)

    mismatches = 0
    for pid in pack_ids:
        prim = legacy.get(pid, 0)
        memb = expected.get(pid, 0)
        db_n = actual.get(pid, 0) if actual else None
        ok = ""
        if db_n is not None:
            ok = "yes" if db_n == memb else "NO"
            if db_n != memb:
                mismatches += 1
        print(
            f"{pid:<22} {prim:>8} {memb:>11} "
            f"{db_n if db_n is not None else '-':>8} {ok:>7}"
        )

    print()
    if not actual:
        print("Set DATABASE_URL to compare against Postgres.")
    elif mismatches:
        print(
            f"{mismatches} pack(s) differ from JSON membership — "
            "run: python -m aiforen.scripts.vocab.import_vocab_storage_bulk --packs-only"
        )
        sys.exit(1)
    else:
        print("DB pack counts match JSON source_packs membership.")


if __name__ == "__main__":
    main()
