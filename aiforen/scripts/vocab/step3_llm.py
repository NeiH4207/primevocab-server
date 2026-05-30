"""Shared GRE/IELTS Step 3 prompt generation (vi_translate + topic)."""

from __future__ import annotations

import difflib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aiforen.scripts.vocab.mcq_llm import extract_json

TEMPLATE_MARKERS = (
    "Dịch sang tiếng Anh (dùng từ",
    "Nhiều người cho rằng cuộc sống",
    "ngữ cảnh IELTS",
)

MULTI_STEP3_SCHEMA = """
Return ONLY valid JSON (no markdown):
{
  "items": [
    {
      "word": "exact display_word from the list",
      "vi_translate_prompt": "one complete Vietnamese sentence to translate into English (learner must use the target word in English)",
      "topic_prompt": "Vietnamese instruction to write one English sentence with the target word in a concrete academic context"
    }
  ]
}
"""


@dataclass
class Step3Word:
    lexeme_id: uuid.UUID
    sense_id: uuid.UUID
    display_word: str
    pos: str
    pack_id: str
    pack_family: str
    definition_en: str
    example: str
    vi_gloss: str


@dataclass
class Step3Chunk:
    custom_id: str
    words: List[Step3Word]


def is_template_prompt(vi_translate: str, topic: str) -> bool:
    blob = f"{vi_translate} {topic}".lower()
    return any(m.lower() in blob for m in TEMPLATE_MARKERS)


def infer_step3_band_level(*, pack_id: str = "", pack_family: str = "band") -> str:
    """Band tone for Step 3: easy (4–6), standard (7), hard (8–9), gre."""
    if pack_family == "gre" or pack_id == "pack_gre":
        return "gre"
    m = re.search(r"pack_band_(\d+)", pack_id or "")
    if m:
        band = int(m.group(1))
        if band <= 6:
            return "easy"
        if band <= 7:
            return "standard"
        return "hard"
    return "standard"


def _step3_style_notes(level: str) -> str:
    if level == "gre":
        return (
            "GRE/academic register. Do NOT mention IELTS. "
            "Contexts: research, policy, ethics, science, law."
        )
    if level == "easy":
        return (
            "IELTS band 4–6: everyday situations (family, school, travel, health, hobbies). "
            "Simple, natural Vietnamese."
        )
    if level == "hard":
        return (
            "IELTS band 8–9: academic/news tone allowed, but prompts stay short. "
            "Avoid GRE-level near-synonym games in the instruction text."
        )
    return "IELTS band 7: clear academic/everyday mix, natural Vietnamese."


def build_multi_step3_prompt(words: List[Step3Word], *, pack_family: str) -> str:
    exam = (
        "GRE / academic English" if pack_family == "gre" else "IELTS / academic English"
    )
    ielts_rule = (
        "Do NOT mention IELTS."
        if pack_family == "gre"
        else "Do not use the word 'IELTS' in prompts."
    )
    lines = []
    for w in words:
        level = infer_step3_band_level(pack_id=w.pack_id, pack_family=pack_family)
        lines.append(
            f"- {w.display_word} ({w.pos}) [level={level}] | def: {w.definition_en[:200]} | "
            f"ex: {w.example[:160]} | vi: {w.vi_gloss[:80]}"
        )
    word_list = ", ".join(w.display_word for w in words)
    return (
        f"You write Step 3 vocabulary writing prompts for Vietnamese learners ({exam}).\n\n"
        f"Words ({len(words)}): {word_list}\n\n"
        f"Word data:\n" + "\n".join(lines) + "\n\n"
        f"Rules for EACH word:\n"
        f"- vi_translate_prompt: ONE natural Vietnamese sentence (NOT meta text like 'Dịch sang tiếng Anh...'). "
        f"The learner translates it into English and must use the English target word in the definition sense.\n"
        f"- topic_prompt: ONE SHORT Vietnamese instruction (about 8–18 words, max ~90 characters). "
        f"Only name a brief situation hook (e.g. 'Viết một câu tiếng Anh về…') — do NOT over-specify details; "
        f"the learner chooses how to complete the English sentence.\n"
        f"- **Different themes**: vi_translate_prompt and topic_prompt must use clearly different contexts "
        f"(e.g. translate about daily life / travel; topic about work / environment / education) — "
        f"not the same scenario rephrased.\n"
        f"- Match [level] per word: {_step3_style_notes('easy')} | "
        f"{_step3_style_notes('standard')} | {_step3_style_notes('hard')} | "
        f"{_step3_style_notes('gre')}\n"
        f"- Prompts must be specific to that word's definition — not interchangeable.\n"
        f"- {ielts_rule}\n"
        f"- Avoid generic filler: 'Nhiều người cho rằng cuộc sống và xã hội rất quan trọng.'\n\n"
        f"{MULTI_STEP3_SCHEMA}"
    )


def max_tokens_step3_chunk(n_words: int) -> int:
    return min(12_000, 300 + n_words * 320)


def _resolve_word_key(key: str, expected: set[str]) -> Optional[str]:
    k = key.strip().lower()
    if k in expected:
        return k
    match = difflib.get_close_matches(k, list(expected), n=1, cutoff=0.82)
    return match[0] if match else None


def parse_multi_step3_response(
    raw: str, words: List[Step3Word]
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
    missing = expected - set(by_word)
    if missing:
        raise ValueError(f"missing words in response: {sorted(missing)}")
    return by_word


def validate_step3_item(item: Dict[str, Any], w: Step3Word) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    trans = (item.get("vi_translate_prompt") or "").strip()
    topic = (item.get("topic_prompt") or "").strip()
    if len(trans) < 20:
        issues.append("translate too short")
    if len(topic) < 12:
        issues.append("topic too short")
    if len(topic) > 120:
        issues.append("topic too long (keep brief)")
    if w.pack_family == "gre" and (
        "ielts" in trans.lower() or "ielts" in topic.lower()
    ):
        issues.append("mentions IELTS on GRE pack")
    if "nhiều người cho rằng cuộc sống" in trans.lower():
        issues.append("generic template filler")
    if trans.lower().startswith("dịch sang tiếng anh (dùng từ"):
        issues.append("meta translate wrapper")
    if trans and topic:
        t_words = set(re.findall(r"\w+", trans.lower())[:12])
        p_words = set(re.findall(r"\w+", topic.lower())[:12])
        overlap = len(t_words & p_words) / max(len(t_words | p_words), 1)
        if overlap > 0.55:
            issues.append("translate and topic themes too similar")
    return (len(issues) == 0, issues)


async def fetch_words_for_step3(
    session: AsyncSession,
    *,
    pack_id: str,
    limit: Optional[int] = None,
    skip_existing: bool = True,
    word_list: Optional[List[str]] = None,
) -> List[Step3Word]:
    sql = """
    SELECT l.id AS lexeme_id, s.id AS sense_id, i.pack_id, p.pack_family,
           l.display_word, l.pos, s.definition_en,
           coalesce(nullif(trim(s.gre_example),''), nullif(trim(s.ielts_example),'')) AS ex,
           s.vi_gloss, s.vi_translate_prompt, s.topic_prompt
    FROM vocab_pack_items i
    JOIN vocab_packs p ON p.pack_id = i.pack_id
    JOIN vocab_lexemes l ON l.id = i.lexeme_id
    JOIN vocab_senses s ON s.lexeme_id = l.id AND s.sense_order = 1
    WHERE length(trim(coalesce(s.definition_en,''))) > 10
      AND length(trim(coalesce(s.vi_gloss,''))) > 2
      AND (
        length(trim(coalesce(s.ielts_example, s.gre_example, ''))) > 15
        OR coalesce(s.vi_translate_prompt, '') LIKE 'Dịch sang tiếng Anh (dùng từ%'
      )
    """
    params: Dict[str, Any] = {"pack_id": pack_id}
    sql += " AND i.pack_id = :pack_id"
    if word_list:
        sql += " AND lower(l.display_word) = ANY(:words)"
        params["words"] = [w.lower() for w in word_list]
    if skip_existing:
        sql += """
      AND (
        coalesce(s.vi_translate_prompt,'') LIKE 'Dịch sang tiếng Anh (dùng từ%'
        OR coalesce(s.vi_translate_prompt,'') LIKE '%Nhiều người cho rằng cuộc sống%'
        OR coalesce(s.topic_prompt,'') ILIKE '%IELTS%'
        OR length(trim(coalesce(s.vi_translate_prompt,''))) < 20
      )
    """
    sql += " ORDER BY l.display_word"
    if limit and not word_list:
        sql += " LIMIT :lim"
        params["lim"] = limit
    rows = (await session.execute(text(sql), params)).all()
    return [
        Step3Word(
            lexeme_id=r[0],
            sense_id=r[1],
            pack_id=r[2],
            pack_family=r[3] or "band",
            display_word=r[4],
            pos=r[5] or "word",
            definition_en=r[6] or r[4],
            example=r[7] or "",
            vi_gloss=r[8] or "",
        )
        for r in rows
    ]


def chunk_step3_words(
    words: List[Step3Word], words_per_request: int
) -> List[Step3Chunk]:
    chunks: List[Step3Chunk] = []
    for i in range(0, len(words), words_per_request):
        batch = words[i : i + words_per_request]
        chunks.append(
            Step3Chunk(
                custom_id=f"step3-chunk-{i // words_per_request:05d}", words=batch
            )
        )
    return chunks
