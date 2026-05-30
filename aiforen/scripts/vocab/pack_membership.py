"""Pack membership helpers for vocab_storage import (source_packs overlap)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from loguru import logger

from aiforen.repositories.pg.vocab_lexicon import lexeme_id_for
from aiforen.scripts.vocab.oxford_packs import normalize_pos
from aiforen.scripts.vocab.pack_specs import infer_stat_labels

DEFAULT_STORAGE = Path(__file__).resolve().parents[4] / "vocab_storage"


def lexeme_id_for_vocab_row(row: Dict[str, Any]) -> uuid.UUID:
    lemma = (row.get("lemma") or row.get("display_word") or "").strip().lower()
    pos = normalize_pos((row.get("pos") or "noun").strip().lower())
    return lexeme_id_for(lemma, pos)


def pack_ids_for_vocab_row(row: Dict[str, Any]) -> List[str]:
    """All packs a vocab row belongs to (UI membership), not only primary pack_id."""

    out: List[str] = []
    source = row.get("source_packs")
    if isinstance(source, list):
        for raw in source:
            pid = str(raw or "").strip()
            if pid and pid not in out:
                out.append(pid)
    if out:
        return out

    primary = str(row.get("pack_id") or "").strip()
    if primary:
        out.append(primary)

    packs_field = row.get("packs")
    if isinstance(packs_field, str) and packs_field.strip():
        for part in packs_field.replace("|", ",").split(","):
            pid = part.strip()
            if pid and pid not in out:
                out.append(pid)
    elif isinstance(packs_field, list):
        for raw in packs_field:
            pid = str(raw or "").strip()
            if pid and pid not in out:
                out.append(pid)

    cefr = str(row.get("cefr_level") or "").strip().upper()
    if cefr == "C2" and "pack_oxford_c2" not in out:
        out.append("pack_oxford_c2")

    return out


def build_pack_membership_items(
    rows: List[Dict[str, Any]],
    *,
    lid_for_row: Callable[[Dict[str, Any]], uuid.UUID],
) -> Dict[str, List[Tuple[int, uuid.UUID, List[str]]]]:
    """Group lexemes per pack using source_packs overlap."""

    buckets: Dict[str, Dict[uuid.UUID, Tuple[int, List[str]]]] = defaultdict(dict)
    skipped = 0
    for row in rows:
        pack_ids = pack_ids_for_vocab_row(row)
        if not pack_ids:
            skipped += 1
            continue
        lid = lid_for_row(row)
        idx = int(row.get("vocab_index") or 0)
        labels = infer_stat_labels((row.get("lemma") or row.get("display_word") or ""))
        for pack_id in pack_ids:
            prev = buckets[pack_id].get(lid)
            if prev is None or idx < prev[0]:
                buckets[pack_id][lid] = (idx, labels)

    if skipped:
        logger.warning("Skipped {} vocab rows with no pack membership", skipped)

    by_pack: Dict[str, List[Tuple[int, uuid.UUID, List[str]]]] = {}
    for pack_id, lex_map in buckets.items():
        by_pack[pack_id] = sorted(
            ((idx, lid, labels) for lid, (idx, labels) in lex_map.items()),
            key=lambda t: t[0],
        )
    return by_pack
