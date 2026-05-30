"""Shared LLM MCQ prompts, validation, and multi-word batch helpers."""

from __future__ import annotations

import difflib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FALLBACK_EX_PATTERNS = (
    "Many people say",
    "The passage uses",
    "Learners often encounter",
    "when they want to show appreciation",
)

MODEL_PRICING_USD_PER_MTOK: List[Tuple[str, float, float]] = [
    ("gpt-5-nano", 0.05, 0.40),
    ("gpt-5.4-nano", 0.20, 1.25),
    ("gpt-5.4-mini", 0.75, 4.50),
    ("gpt-5.5", 5.0, 30.0),
    ("opus-4-7", 5.0, 25.0),
    ("opus-4-6", 5.0, 25.0),
    ("sonnet-4-6", 3.0, 15.0),
    ("sonnet-4-5", 3.0, 15.0),
    ("haiku-4-5", 1.0, 5.0),
    ("haiku-3-5", 0.8, 4.0),
]

BATCH_DISCOUNT = 0.5  # Anthropic Message Batches API


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "TokenUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class WordSample:
    lexeme_id: uuid.UUID
    sense_id: uuid.UUID
    display_word: str
    pos: str
    pack_id: str
    definition_en: str
    example: str
    vi_gloss: str


@dataclass
class McqChunk:
    custom_id: str
    words: List[WordSample] = field(default_factory=list)


SINGLE_MCQ_SCHEMA = """
Return ONLY valid JSON (no markdown):
{
  "meaning_mcq": {
    "prompt": "string",
    "options": [{"id": "a|b|c|d", "text": "string"}, ...],
    "correct_option_id": "a|b|c|d",
    "explanation": "string"
  },
  "cloze": {
    "prompt": "string",
    "options": [{"id": "a|b|c|d", "text": "string"}, ...],
    "correct_option_id": "a|b|c|d",
    "explanation": "string"
  }
}
"""

MULTI_MCQ_SCHEMA = """
Return ONLY valid JSON (no markdown):
{
  "items": [
    {
      "word": "exact display_word from the list",
      "meaning_mcq": { "prompt": "...", "options": [...], "correct_option_id": "a|b|c|d", "explanation": "..." },
      "cloze": { "prompt": "...", "options": [...], "correct_option_id": "a|b|c|d", "explanation": "..." }
    }
  ]
}
"""

# Reasoning-model output budgets (reasoning tokens + JSON).
GPT5_NANO_LEGACY_MAX_OUTPUT: Dict[str, int] = {
    "minimal": 8_000,
    "low": 14_000,
    "medium": 22_000,
    "high": 32_000,
}
GPT5_5_MAX_OUTPUT: Dict[str, int] = {
    "none": 8_000,
    "low": 12_000,
    "medium": 18_000,
    "high": 28_000,
    "xhigh": 36_000,
}


def is_gpt5_nano_legacy(model: str) -> bool:
    m = model.lower()
    return "gpt-5-nano" in m and "5.4" not in m and "5.5" not in m


def is_gpt5_5_family(model: str) -> bool:
    return "gpt-5.5" in model.lower()


def is_gpt5_4_nano(model: str) -> bool:
    return "gpt-5.4-nano" in model.lower()


def openai_supports_temperature(model: str) -> bool:
    return not (
        is_gpt5_nano_legacy(model) or is_gpt5_5_family(model) or is_gpt5_4_nano(model)
    )


def _normalize_reasoning_effort(model: str, reasoning_effort: str) -> str:
    if is_gpt5_nano_legacy(model):
        return (
            reasoning_effort
            if reasoning_effort in GPT5_NANO_LEGACY_MAX_OUTPUT
            else "minimal"
        )
    if is_gpt5_5_family(model) or is_gpt5_4_nano(model):
        effort = reasoning_effort
        if effort == "minimal":
            effort = "none"
        return effort if effort in GPT5_5_MAX_OUTPUT else "none"
    return reasoning_effort


def openai_mcq_request_kwargs(
    model: str,
    prompt: str,
    *,
    reasoning_effort: str = "minimal",
) -> Dict[str, Any]:
    """Responses API kwargs for vocab MCQ generation."""
    kwargs: Dict[str, Any] = {
        "model": model,
        "input": prompt,
        "max_output_tokens": 1400,
    }
    effort = _normalize_reasoning_effort(model, reasoning_effort)
    if is_gpt5_nano_legacy(model):
        kwargs["max_output_tokens"] = GPT5_NANO_LEGACY_MAX_OUTPUT[effort]
        kwargs["reasoning"] = {"effort": effort}
    elif is_gpt5_5_family(model) or is_gpt5_4_nano(model):
        kwargs["max_output_tokens"] = GPT5_5_MAX_OUTPUT[effort]
        kwargs["reasoning"] = {"effort": effort}
    elif openai_supports_temperature(model):
        kwargs["temperature"] = 0.35
    return kwargs


def openai_reasoning_tokens(resp: Any) -> int:
    u = getattr(resp, "usage", None)
    if not u:
        return 0
    details = getattr(u, "output_tokens_details", None)
    return int(getattr(details, "reasoning_tokens", 0) or 0)


def infer_mcq_difficulty(*, pack_id: str = "", exam: str = "ielts") -> str:
    """Map pack → trap difficulty: easy (band 4–6), standard/hard (7–9), gre."""
    if exam == "gre" or pack_id == "pack_gre":
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


def build_understanding_mcq_prompt(
    *,
    display_word: str,
    pos: str,
    definition_en: str,
    example: str,
    vi_gloss: str,
    schema: str = SINGLE_MCQ_SCHEMA,
    exam: str = "ielts",
    difficulty: str = "standard",
) -> str:
    """Authoring instructions for meaning + cloze MCQs."""
    diff = (
        difficulty
        if difficulty in ("easy", "standard", "hard", "gre")
        else infer_mcq_difficulty(exam=exam)
    )

    if diff == "gre":
        audience = (
            "You are an expert GRE / graduate-level vocabulary item writer for Vietnamese "
            "learners preparing for academic English (NOT IELTS)."
        )
        context_note = (
            "Use formal academic register. Distractors must include plausible "
            "near-synonyms and subtle wrong senses typical of GRE passages."
        )
        trap_rules = """- Three distractors — make them genuinely hard:
  1) same lemma, wrong sense (polysemy / rare sense),
  2) wrong collocation or register in an academic sentence,
  3) a near-synonym that almost fits but fails the definition nuance (e.g. mitigate vs abate)."""
        sentence_len = "12–24 words"
    elif diff == "easy":
        audience = "You are an IELTS vocabulary item writer for Vietnamese learners (target band 4–6)."
        context_note = (
            "Use clear everyday contexts. Distractors should be plausible but NOT highly subtle — "
            "learners at this level should not need GRE-style near-synonym traps."
        )
        trap_rules = """- Three distractors — moderate difficulty only:
  1) same lemma, clearly wrong sense (avoid obscure polysemy),
  2) awkward or wrong collocation,
  3) a different common word that obviously changes meaning."""
        sentence_len = "10–18 words"
    elif diff == "hard":
        audience = "You are an IELTS vocabulary item writer for Vietnamese learners (target band 8–9)."
        context_note = (
            "Use academic or nuanced contexts. Distractors should be strong traps similar to "
            "high-band reading — including near-synonyms and idiomatic shifts."
        )
        trap_rules = """- Three distractors — high difficulty:
  1) same lemma, subtle wrong sense,
  2) wrong collocation/register in a fluent sentence,
  3) a near-synonym or related word that fits the slot but changes meaning."""
        sentence_len = "14–24 words"
    else:  # standard — band 7
        audience = "You are an IELTS vocabulary item writer for Vietnamese learners (target band 7)."
        context_note = "Balanced difficulty: clear contexts with meaningful traps (wrong sense, collocation, related word)."
        trap_rules = """- Three distractors:
  1) same lemma, wrong sense (polysemy / homograph),
  2) lemma with wrong collocation or register,
  3) a related word that fits the sentence slot but changes meaning."""
        sentence_len = "12–22 words"

    return f"""{audience}

Your task: create exactly TWO items that test whether the learner knows the TARGET SENSE of a word.
This is NOT a grammar quiz — every option in meaning_mcq must be fluent, grammatical English.
{context_note}

## Authoritative word data (definition wins over example if they differ)
| Field | Value |
| lemma | {display_word} |
| part of speech | {pos} |
| definition (ONLY sense to test) | {definition_en} |
| reference sentence (correct usage — paraphrase in items; never copy verbatim into options) | {example} |
| Vietnamese gloss (learner L1 hint) | {vi_gloss} |

## Item 1 — meaning_mcq
- Prompt must ask: Which sentence uses "{display_word}" with the correct meaning? (or equivalent).
- Write exactly four options (ids a–d), each a full sentence of similar length ({sentence_len}).
- Exactly ONE sentence uses "{display_word}" (or a natural inflection) in the definition sense.
{trap_rules}
- Shuffle which option is correct — do not always use "a".
- explanation: one concise English sentence stating why the correct option matches the definition.

## Item 2 — cloze
- Write ONE new sentence (not copied from the reference) with exactly one ______ blank.
- Only "{display_word}" (or natural inflection) completes the blank for the definition sense.
- Four options: single words or short phrases (≤3 words), same POS where possible.
- Three distractors: real English words that fit the grammar of the sentence but NOT this definition.
- explanation: one sentence — why the answer fits the definition.

## Self-check (do this before responding)
- [ ] No option is ungrammatical or absurdly short/long vs the others.
- [ ] Correct meaning_mcq option contains the target lemma in the tested sense.
- [ ] No option text equals the reference sentence verbatim.
- [ ] Cloze prompt contains ______ or ___.
- [ ] correct_option_id is a, b, c, or d and appears in options.

## Output format
Return ONLY valid JSON matching this schema. No markdown fences, no commentary outside JSON.

{schema}"""


def build_single_prompt(
    w: WordSample,
    *,
    understanding: bool = True,
    exam: str = "ielts",
    pack_id: str = "",
) -> str:
    if understanding:
        diff = infer_mcq_difficulty(
            pack_id=pack_id or getattr(w, "pack_id", ""), exam=exam
        )
        return build_understanding_mcq_prompt(
            display_word=w.display_word,
            pos=w.pos,
            definition_en=w.definition_en,
            example=w.example,
            vi_gloss=w.vi_gloss,
            schema=SINGLE_MCQ_SCHEMA,
            exam=exam,
            difficulty=diff,
        )

    return f"""IELTS vocab quiz for Vietnamese learners.

Target: {w.display_word} ({w.pos})
Definition: {w.definition_en}
Example: {w.example}
VI: {w.vi_gloss}

meaning_mcq + cloze (one correct option each). Do not copy the example verbatim.

{SINGLE_MCQ_SCHEMA}"""


def difficulty_trap_summary(difficulty: str) -> str:
    """One-line trap policy for multi-word batch prompts."""
    summaries = {
        "easy": "band 4–6: moderate traps only (clear wrong sense, no GRE near-synonyms)",
        "standard": "band 7: balanced traps (wrong sense, collocation, related word)",
        "hard": "band 8–9: strong traps including near-synonyms",
        "gre": "GRE: hardest traps (polysemy, register, near-synonyms)",
    }
    return summaries.get(difficulty, summaries["standard"])


def build_multi_prompt(words: List[WordSample], *, understanding: bool = True) -> str:
    lines = []
    for w in words:
        diff = infer_mcq_difficulty(pack_id=w.pack_id)
        lines.append(
            f"- {w.display_word} ({w.pos}) [difficulty={diff}] | def: {w.definition_en[:200]} | "
            f"ex: {w.example[:160]} | vi: {w.vi_gloss[:80]} | traps: {difficulty_trap_summary(diff)}"
        )
    word_list = ", ".join(w.display_word for w in words)
    if understanding:
        rules = """For EVERY word, follow its [difficulty] trap policy:
- meaning_mcq: four grammatical sentences; exactly one correct definition sense; three plausible wrong-sense distractors per policy.
- cloze: new sentence with ______; only the target lemma fits the definition; four word/phrase options.
- Field "word" must equal display_word exactly. Do not copy reference examples verbatim into options.
- Include a short explanation per item."""
    else:
        rules = "Generate meaning_mcq + cloze per word. One correct option each."

    return (
        f"You are an expert IELTS vocabulary item writer. Test real word understanding, not grammar spotting.\n\n"
        f"Words ({len(words)}): {word_list}\n\n"
        f"Word data:\n" + "\n".join(lines) + f"\n\n{rules}\n\n{MULTI_MCQ_SCHEMA}"
    )


def max_tokens_for_chunk(n_words: int) -> int:
    return min(16_000, 400 + n_words * 1_400)


def _strip_markdown_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def normalize_options(options: Any) -> List[Dict[str, str]]:
    """Accept [{id,text}] or ['a) sentence', ...] from multi-word batch output."""
    if not options:
        return []
    out: List[Dict[str, str]] = []
    ids = "abcd"
    if not isinstance(options, list):
        return out
    for i, opt in enumerate(options):
        if isinstance(opt, dict):
            oid = str(opt.get("id", "")).strip().lower()
            text = str(opt.get("text", "")).strip()
            if oid and text:
                out.append({"id": oid[:1], "text": text})
        elif isinstance(opt, str):
            s = opt.strip()
            m = re.match(r"^([a-d])\)\s*(.+)$", s, re.I)
            if m:
                out.append({"id": m.group(1).lower(), "text": m.group(2).strip()})
            elif i < 4:
                out.append({"id": ids[i], "text": s})
    return out


def normalize_mcq_block(block: Dict[str, Any]) -> Dict[str, Any]:
    block = dict(block or {})
    block["options"] = normalize_options(block.get("options"))
    return block


def normalize_word_item(item: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(item)
    item["meaning_mcq"] = normalize_mcq_block(item.get("meaning_mcq") or {})
    item["cloze"] = normalize_mcq_block(item.get("cloze") or {})
    return item


def extract_json(raw: str) -> Dict[str, Any]:
    raw = _strip_markdown_fences(raw)
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError("no JSON in response")
    return json.loads(m.group(0))


def _resolve_word_key(key: str, expected: set[str]) -> Optional[str]:
    k = key.strip().lower()
    if k in expected:
        return k
    match = difflib.get_close_matches(k, list(expected), n=1, cutoff=0.82)
    return match[0] if match else None


def parse_multi_response(
    raw: str, words: List[WordSample]
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
            by_word[key] = normalize_word_item(item)
    missing = expected - set(by_word)
    if missing:
        raise ValueError(f"missing words in response: {sorted(missing)}")
    return by_word


def pricing_for_model(model: str) -> Tuple[float, float]:
    m = model.lower()
    for needle, inp, out in MODEL_PRICING_USD_PER_MTOK:
        if needle in m:
            return inp, out
    return 3.0, 15.0


def cost_usd(model: str, usage: TokenUsage, *, batch: bool = False) -> float:
    inp_rate, out_rate = pricing_for_model(model)
    cost = (usage.input_tokens / 1_000_000 * inp_rate) + (
        usage.output_tokens / 1_000_000 * out_rate
    )
    return cost * BATCH_DISCOUNT if batch else cost


def fmt_cost(usd: float) -> str:
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:.2f}"


def usage_from_response(msg: Any) -> TokenUsage:
    u = getattr(msg, "usage", None)
    if not u:
        return TokenUsage()
    return TokenUsage(
        input_tokens=int(getattr(u, "input_tokens", 0) or 0),
        output_tokens=int(getattr(u, "output_tokens", 0) or 0),
    )


def _looks_ungrammatical(sentence: str) -> bool:
    s = sentence.lower().strip()
    if (
        " is very people" in s
        or " go to school yesterday" in s
        or " because it is" in s
    ):
        return True
    if re.search(r"\bi \w+ go\b", s):
        return True
    return False


def score_meaning_understanding(
    block: Dict[str, Any], *, word: str, correct_id: str
) -> Dict[str, Any]:
    options = block.get("options") or []
    texts = {
        str(o.get("id")): str(o.get("text", "")) for o in options if isinstance(o, dict)
    }
    texts.get(correct_id, "")
    wrong = [t for oid, t in texts.items() if oid != correct_id]
    wrong_ungram = sum(1 for t in wrong if _looks_ungrammatical(t))
    wrong_gram = len(wrong) - wrong_ungram
    homograph_trap = sum(
        1 for t in wrong if word.lower() in t.lower() and not _looks_ungrammatical(t)
    )
    return {
        "wrong_grammatical": wrong_gram,
        "wrong_broken_english": wrong_ungram,
        "wrong_uses_word_different_sense": homograph_trap,
        "tests_understanding": wrong_gram >= 2 and wrong_ungram <= 1,
    }


def validate_mcq_block(
    block: Dict[str, Any], *, word: str, example: str, qtype: str
) -> Tuple[List[str], Dict[str, Any]]:
    issues: List[str] = []
    extra: Dict[str, Any] = {}
    prompt = (block.get("prompt") or "").strip()
    options = block.get("options") or []
    correct = (block.get("correct_option_id") or "").strip()
    expl = (block.get("explanation") or "").strip()

    if len(prompt) < 12:
        issues.append("prompt too short")
    if len(options) != 4:
        issues.append(f"expected 4 options, got {len(options)}")
    ids = {str(o.get("id")) for o in options if isinstance(o, dict)}
    if ids != {"a", "b", "c", "d"}:
        issues.append(f"option ids must be a-d, got {ids}")
    if correct not in ids:
        issues.append(f"correct_option_id {correct!r} not in options")
    texts = [str(o.get("text", "")).strip() for o in options if isinstance(o, dict)]
    if len(set(texts)) < 4:
        issues.append("duplicate option text")
    correct_text = next(
        (
            str(o.get("text", ""))
            for o in options
            if isinstance(o, dict) and o.get("id") == correct
        ),
        "",
    )
    if qtype == "meaning_mcq":
        if word.lower() not in correct_text.lower():
            issues.append("correct option may not contain target word")
        if correct_text.strip() == example.strip():
            issues.append("correct option identical to reference example")
        extra = score_meaning_understanding(block, word=word, correct_id=correct)
    if qtype == "cloze":
        if "______" not in prompt and "___" not in prompt:
            issues.append("cloze prompt missing blank")
        if word.lower() not in correct_text.lower():
            issues.append("cloze answer should be target word")
    if not expl:
        issues.append("missing explanation")
    return issues, extra


def validate_word_items(
    item: Dict[str, Any], w: WordSample, *, normalize: bool = True
) -> Tuple[bool, List[str]]:
    """Return (ok, all_issues)."""
    item = normalize_word_item(item) if normalize else item
    all_issues: List[str] = []
    for qtype in ("meaning_mcq", "cloze"):
        block = item.get(qtype) or {}
        issues, _ = validate_mcq_block(
            block, word=w.display_word, example=w.example, qtype=qtype
        )
        if issues:
            all_issues.extend(f"{qtype}:{x}" for x in issues)
    return (len(all_issues) == 0, all_issues)


async def fetch_words_for_mcq(
    session: AsyncSession,
    *,
    pack_id: Optional[str] = None,
    limit: Optional[int] = None,
    skip_existing: bool = True,
    word_list: Optional[List[str]] = None,
) -> List[WordSample]:
    sql = """
    SELECT l.id AS lexeme_id, s.id AS sense_id, i.pack_id, l.display_word, l.pos,
           s.definition_en,
           coalesce(nullif(trim(s.ielts_example),''), nullif(trim(s.gre_example),'')) AS ex,
           s.vi_gloss
    FROM vocab_pack_items i
    JOIN vocab_lexemes l ON l.id = i.lexeme_id
    JOIN vocab_senses s ON s.lexeme_id = l.id AND s.sense_order = 1
    WHERE length(trim(coalesce(s.ielts_example, s.gre_example, ''))) > 20
      AND length(trim(coalesce(s.vi_gloss, ''))) > 2
      AND length(trim(coalesce(s.definition_en, ''))) > 10
    """
    params: Dict[str, Any] = {}
    if pack_id:
        sql += " AND i.pack_id = :pack_id"
        params["pack_id"] = pack_id
    if word_list:
        sql += " AND lower(l.display_word) = ANY(:words)"
        params["words"] = [w.lower() for w in word_list]
    else:
        for pat in FALLBACK_EX_PATTERNS:
            sql += f" AND coalesce(s.ielts_example,'') NOT LIKE '%{pat}%'"
    if skip_existing:
        sql += """
      AND NOT EXISTS (
        SELECT 1 FROM vocab_questions qm
        WHERE qm.lexeme_id = l.id
          AND qm.type = 'meaning_mcq'
          AND coalesce(qm.generator_meta->>'source','') IN ('llm_mcq_batch', 'llm_mcq_openai')
      )
      AND NOT EXISTS (
        SELECT 1 FROM vocab_questions qc
        WHERE qc.lexeme_id = l.id
          AND qc.type = 'cloze'
          AND coalesce(qc.generator_meta->>'source','') IN ('llm_mcq_batch', 'llm_mcq_openai')
      )
    """
    sql += " ORDER BY i.pack_id, l.display_word"
    if limit and not word_list:
        sql += " LIMIT :lim"
        params["lim"] = limit
    rows = (await session.execute(text(sql), params)).all()
    return [
        WordSample(
            lexeme_id=r[0],
            sense_id=r[1],
            pack_id=r[2],
            display_word=r[3],
            pos=r[4] or "word",
            definition_en=r[5] or r[3],
            example=r[6] or "",
            vi_gloss=r[7] or "",
        )
        for r in rows
    ]


def chunk_words(words: List[WordSample], words_per_request: int) -> List[McqChunk]:
    chunks: List[McqChunk] = []
    for i in range(0, len(words), words_per_request):
        batch = words[i : i + words_per_request]
        chunks.append(
            McqChunk(custom_id=f"mcq-chunk-{i // words_per_request:05d}", words=batch)
        )
    return chunks
