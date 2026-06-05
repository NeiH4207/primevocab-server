#!/usr/bin/env python3
"""Insert vocab_packs rows required before vocab_storage bulk import."""

from __future__ import annotations

import os
import sys

import psycopg2
import psycopg2.extras

from aiforen.scripts.vocab.build_packs import BAND_PACKS
from aiforen.scripts.vocab.oxford_packs import CEFR_IELTS_BAND, OXFORD_PACKS

GRE_PACK = {
    "pack_id": "pack_gre",
    "title": "GRE Vocabulary",
    "description": "High-difficulty words for GRE verbal reasoning.",
    "category": "GRE",
    "pack_family": "gre",
    "exam_type": "gre",
    "sort_order": 20,
    "target_band_min": 8.0,
    "target_band_max": 9.0,
}

UPSERT = """
INSERT INTO vocab_packs (
  pack_id, title, description, category, task_type, exam_type, pack_family,
  source_band_min, source_band_max, target_band_min, target_band_max,
  sort_order, is_active, is_premium, content_status, target_word_count, completed_word_count
) VALUES (
  %(pack_id)s, %(title)s, %(description)s, %(category)s, 'Both', %(exam_type)s, %(pack_family)s,
  0, 9, %(target_band_min)s, %(target_band_max)s,
  %(sort_order)s, true, %(is_premium)s, 'filled', 0, 0
)
ON CONFLICT (pack_id) DO UPDATE SET
  title = EXCLUDED.title,
  description = EXCLUDED.description,
  category = EXCLUDED.category,
  exam_type = EXCLUDED.exam_type,
  pack_family = EXCLUDED.pack_family,
  target_band_min = EXCLUDED.target_band_min,
  target_band_max = EXCLUDED.target_band_max,
  sort_order = EXCLUDED.sort_order,
  is_premium = EXCLUDED.is_premium,
  content_status = 'filled',
  updated_at = NOW()
"""


def _pg_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("Set DATABASE_URL")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _rows() -> list[dict]:
    out: list[dict] = []
    for spec in BAND_PACKS:
        out.append(
            {
                "pack_id": spec["pack_id"],
                "title": spec["title"],
                "description": spec.get("description") or "",
                "category": spec.get("category") or spec["pack_id"],
                "exam_type": spec.get("exam_type") or "ielts",
                "pack_family": spec.get("pack_family") or "band",
                "target_band_min": float(spec.get("target_band_min") or 0),
                "target_band_max": float(spec.get("target_band_max") or 9),
                "sort_order": int(spec.get("sort_order") or 0),
                "is_premium": bool(spec.get("is_premium", False)),
            }
        )
    for spec in OXFORD_PACKS:
        cefr = spec.get("cefr_level") or "B1"
        band = CEFR_IELTS_BAND.get(cefr, (5.0, 7.0))
        out.append(
            {
                "pack_id": spec["pack_id"],
                "title": spec["title"],
                "description": spec.get("description") or "",
                "category": spec.get("category") or f"Oxford {cefr}",
                "exam_type": spec.get("exam_type") or "oxford",
                "pack_family": spec.get("pack_family") or "cefr",
                "target_band_min": band[0],
                "target_band_max": band[1],
                "sort_order": int(spec.get("sort_order") or 0),
                "is_premium": bool(spec.get("is_premium", False)),
            }
        )
    out.append(
        {
            "pack_id": GRE_PACK["pack_id"],
            "title": GRE_PACK["title"],
            "description": GRE_PACK.get("description") or "",
            "category": GRE_PACK.get("category") or "GRE",
            "exam_type": GRE_PACK.get("exam_type") or "gre",
            "pack_family": GRE_PACK.get("pack_family") or "gre",
            "target_band_min": float(GRE_PACK.get("target_band_min") or 8),
            "target_band_max": float(GRE_PACK.get("target_band_max") or 9),
            "sort_order": int(GRE_PACK.get("sort_order") or 20),
            "is_premium": False,
        }
    )
    return out


def main() -> None:
    conn = psycopg2.connect(_pg_url())
    cur = conn.cursor()
    rows = _rows()
    psycopg2.extras.execute_batch(cur, UPSERT, rows, page_size=50)
    conn.commit()
    cur.close()
    conn.close()
    print(f"Ensured {len(rows)} vocab_packs", file=sys.stderr)


if __name__ == "__main__":
    main()
