"""LLM backfill for vi_gloss + English synonyms (sense-aligned to definition_en)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.scripts.vocab.mcq_llm import extract_json
from aiforen.scripts.vocab.step3_llm import _resolve_word_key, infer_step3_band_level

MULTI_GLOSS_SCHEMA = """
Return ONLY valid JSON (no markdown):
{
  "items": [
    {
      "word": "exact display_word from the list",
      "vi_gloss": "short Vietnamese gloss for THIS definition sense (3-12 words)",
      "synonyms": ["syn1", "syn2", "syn3"]
    }
  ]
}
"""


@dataclass
class GlossWord:
    lexeme_id: uuid.UUID
    display_word: str
    pos: str
    pack_id: str
    pack_family: str
    definition_en: str


def build_multi_gloss_prompt(words: List[GlossWord]) -> str:
    lines = []
    for w in words:
        level = infer_step3_band_level(pack_id=w.pack_id, pack_family=w.pack_family)
        lines.append(
            f"- {w.display_word} ({w.pos}) [level={level}] | def: {w.definition_en[:220]}"
        )
    exam = "GRE / academic" if words[0].pack_family == "gre" else "IELTS"
    syn_rule = (
        "3–5 near-synonyms (same sense, formal register)"
        if words[0].pack_family == "gre"
        else "2–4 near-synonyms (same sense, appropriate for band)"
    )
    return (
        f"You write Vietnamese glosses and English synonyms for {exam} vocabulary.\n\n"
        f"Words ({len(words)}):\n" + "\n".join(lines) + "\n\n"
        f"Rules for EACH word:\n"
        f"- vi_gloss: concise Vietnamese meaning matching definition_en (not word-for-word EN translation).\n"
        f"- synonyms: {syn_rule}; lowercase single words/phrases; NO antonyms; NO different POS.\n"
        f"- Anchor strictly to the definition sense — ignore other meanings of the lemma.\n\n"
        f"{MULTI_GLOSS_SCHEMA}"
    )


def max_tokens_gloss_chunk(n_words: int) -> int:
    return min(8000, 200 + n_words * 180)


def parse_multi_gloss_response(
    raw: str, words: List[GlossWord]
) -> Dict[str, Dict[str, Any]]:
    data = extract_json(raw)
    items = data.get("items") or []
    expected = {w.display_word.lower() for w in words}
    by_word: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_key = str(item.get("word", "")).strip().lower()
        key = _resolve_word_key(raw_key, expected) if raw_key else None
        if key:
            by_word[key] = item
    return by_word


def validate_gloss_item(item: Dict[str, Any], w: GlossWord) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    gloss = (item.get("vi_gloss") or "").strip()
    syns = item.get("synonyms") or []
    if len(gloss) < 2:
        issues.append("vi_gloss too short")
    if not isinstance(syns, list) or len(syns) < 2:
        issues.append("need at least 2 synonyms")
    if len(syns) > 6:
        issues.append("too many synonyms")
    cleaned = []
    for s in syns:
        t = str(s).strip().lower()
        if t and t != w.display_word.lower():
            cleaned.append(t)
    if len(cleaned) < 2:
        issues.append("synonyms invalid after clean")
    if gloss.lower() == w.display_word.lower():
        issues.append("vi_gloss equals English word")
    return (len(issues) == 0, issues)


async def fetch_words_for_gloss(
    session: AsyncSession,
    *,
    pack_id: str,
    limit: Optional[int] = None,
    force: bool = False,
) -> List[GlossWord]:
    sql = """
    SELECT l.id, l.display_word, l.pos, i.pack_id, p.pack_family, s.definition_en
    FROM vocab_pack_items i
    JOIN vocab_packs p ON p.pack_id = i.pack_id
    JOIN vocab_lexemes l ON l.id = i.lexeme_id
    JOIN vocab_senses s ON s.lexeme_id = l.id AND s.sense_order = 1
    WHERE i.pack_id = :pack_id
      AND length(trim(coalesce(s.definition_en, ''))) >= 20
    """
    params: Dict[str, Any] = {"pack_id": pack_id}
    if not force:
        sql += """
      AND (
        length(trim(coalesce(s.vi_gloss, ''))) <= 2
        OR coalesce(s.vi_gloss, '') LIKE '(nghĩa%'
        OR coalesce(s.vi_translate_prompt, '') LIKE 'Dịch sang tiếng Anh (dùng từ%'
        OR jsonb_array_length(coalesce(s.synonyms, '[]'::jsonb)) < 2
      )
    """
    sql += " ORDER BY l.display_word"
    if limit:
        sql += " LIMIT :lim"
        params["lim"] = limit
    rows = (await session.execute(text(sql), params)).all()
    return [
        GlossWord(
            lexeme_id=r[0],
            display_word=r[1],
            pos=r[2] or "word",
            pack_id=r[3],
            pack_family=r[4] or "band",
            definition_en=r[5] or r[1],
        )
        for r in rows
    ]


def chunk_gloss_words(
    words: List[GlossWord], words_per_request: int
) -> List[List[GlossWord]]:
    n = max(1, words_per_request)
    return [words[i : i + n] for i in range(0, len(words), n)]
