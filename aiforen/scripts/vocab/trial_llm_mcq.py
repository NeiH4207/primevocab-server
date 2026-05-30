"""Trial: generate vocab MCQs with Claude — compare models / understanding-focused items."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

os.environ["CORS_ORIGINS"] = '["http://localhost:3000","http://127.0.0.1:3000"]'
os.environ.setdefault("PG_HOST", "127.0.0.1")
os.environ.setdefault("PG_PORT", "55432")

from loguru import logger
from sqlalchemy import text

from aiforen.core import db as core_db
from aiforen.core.config import get_settings
from aiforen.repositories.pg.vocab_lexicon import VocabLexiconRepo
from aiforen.scripts.vocab.mcq_llm import (
    SINGLE_MCQ_SCHEMA,
    build_understanding_mcq_prompt,
    infer_mcq_difficulty,
    openai_mcq_request_kwargs,
    openai_reasoning_tokens,
)

# Band 7 words for A/B model comparison (polysemy + idioms at the end).
COMPARE_WORDS = [
    "technical",
    "laughter",
    "thread",
    "normally",
    "till",
    "wood",
    "reality",
    "minority",
    "description",
    "mission",
]
DEFAULT_COMPARE_SAMPLE = len(COMPARE_WORDS)  # full 10-word compare set

# GRE pack — academic / near-synonym traps (not band-7 common words).
GRE_COMPARE_WORDS = [
    "abate",
    "ameliorate",
    "aberration",
    "absolve",
    "abstemious",
    "abrogate",
    "abyss",
    "absurdity",
    "accelerate",
    "acclaim",
]
DEFAULT_GRE_PACK_WORDS = 858


def _compare_word_list(sample: int, *, gre: bool = False) -> List[str]:
    pool = GRE_COMPARE_WORDS if gre else COMPARE_WORDS
    n = max(1, min(sample, len(pool)))
    return pool[:n]


FALLBACK_EX_PATTERNS = (
    "Many people say",
    "The passage uses",
    "Learners often encounter",
    "when they want to show appreciation",
)

# USD per 1M tokens (base input / output). Source: platform.claude.com/docs pricing.
MODEL_PRICING_USD_PER_MTOK: List[Tuple[str, float, float]] = [
    ("gpt-5-nano", 0.05, 0.40),
    ("gpt-5.4-nano", 0.20, 1.25),
    ("gpt-5.4-mini", 0.75, 4.50),
    ("gpt-5.4", 2.50, 15.0),
    ("gpt-5.5", 5.0, 30.0),
    ("opus-4-7", 5.0, 25.0),
    ("opus-4-6", 5.0, 25.0),
    ("sonnet-4-6", 3.0, 15.0),
    ("sonnet-4-5", 3.0, 15.0),
    ("haiku-4-5", 1.0, 5.0),
    ("haiku-3-5", 0.8, 4.0),
]


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "TokenUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class WordSample:
    display_word: str
    pos: str
    pack_id: str
    definition_en: str
    example: str
    vi_gloss: str


def _build_prompt(w: WordSample, *, understanding: bool, exam: str = "ielts") -> str:
    if understanding:
        diff = infer_mcq_difficulty(pack_id=w.pack_id, exam=exam)
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

    return f"""You write IELTS vocabulary quiz items for Vietnamese learners (band 7).

Target word: {w.display_word} ({w.pos})
English definition: {w.definition_en}
Example sentence (correct usage): {w.example}
Vietnamese gloss: {w.vi_gloss}

Rules:
- meaning_mcq: which sentence uses the word correctly; one correct; three wrong via wrong sense or unnatural use.
- cloze: one blank for the target word; four word options.
- Do not copy the example verbatim as all four options.
{SINGLE_MCQ_SCHEMA}"""


def _pricing_for_model(model: str) -> Tuple[float, float]:
    m = model.lower()
    for needle, inp, out in MODEL_PRICING_USD_PER_MTOK:
        if needle in m:
            return inp, out
    return 3.0, 15.0  # conservative default (Sonnet-like)


def _cost_usd(model: str, usage: TokenUsage) -> float:
    inp_rate, out_rate = _pricing_for_model(model)
    return (usage.input_tokens / 1_000_000 * inp_rate) + (
        usage.output_tokens / 1_000_000 * out_rate
    )


def _usage_from_response(msg: Any) -> TokenUsage:
    u = getattr(msg, "usage", None)
    if not u:
        return TokenUsage()
    return TokenUsage(
        input_tokens=int(getattr(u, "input_tokens", 0) or 0),
        output_tokens=int(getattr(u, "output_tokens", 0) or 0),
    )


def _fmt_cost(usd: float) -> str:
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:.2f}"


def _print_cost_line(model: str, usage: TokenUsage, *, label: str = "") -> float:
    cost = _cost_usd(model, usage)
    prefix = f"{label} " if label else ""
    print(
        f"{prefix}Tokens: in={usage.input_tokens:,} out={usage.output_tokens:,} "
        f"→ est. {_fmt_cost(cost)} ({model})"
    )
    return cost


def _extract_json(raw: str) -> Dict[str, Any]:
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


def _score_meaning_understanding(
    block: Dict[str, Any], *, word: str, correct_id: str
) -> Dict[str, Any]:
    """Heuristic: are wrong options grammatical traps (good) vs broken English (bad)?"""
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


def _validate_mcq_block(
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
        extra = _score_meaning_understanding(block, word=word, correct_id=correct)
    if qtype == "cloze":
        if "______" not in prompt and "___" not in prompt:
            issues.append("cloze prompt missing blank")
        if word.lower() not in correct_text.lower():
            issues.append("cloze answer should be target word")
    if not expl:
        issues.append("missing explanation")
    return issues, extra


async def _sample_words(
    repo: VocabLexiconRepo,
    n: int,
    pack_id: Optional[str],
    *,
    word_list: Optional[List[str]] = None,
) -> List[WordSample]:
    sql = """
    SELECT i.pack_id, l.display_word, l.pos, s.definition_en,
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
    if pack_id and not word_list:
        sql += " AND i.pack_id = :pack_id"
        params["pack_id"] = pack_id
    if word_list:
        sql += " AND lower(l.display_word) = ANY(:words)"
        params["words"] = [w.lower() for w in word_list]
    else:
        for pat in FALLBACK_EX_PATTERNS:
            sql += f" AND coalesce(s.ielts_example,'') NOT LIKE '%{pat}%'"
        sql += " ORDER BY random() LIMIT :n"
        params["n"] = n
    rows = (await repo.s.execute(text(sql), params)).all()
    by_word = {
        r[1].lower(): WordSample(
            pack_id=r[0],
            display_word=r[1],
            pos=r[2] or "word",
            definition_en=r[3] or r[1],
            example=r[4] or "",
            vi_gloss=r[5] or "",
        )
        for r in rows
    }
    if word_list:
        out: List[WordSample] = []
        for w in word_list:
            key = w.lower()
            if key not in by_word:
                continue
            sample = by_word[key]
            if pack_id:
                sample = WordSample(
                    display_word=sample.display_word,
                    pos=sample.pos,
                    pack_id=pack_id,
                    definition_en=sample.definition_en,
                    example=sample.example,
                    vi_gloss=sample.vi_gloss,
                )
            out.append(sample)
        return out
    return list(by_word.values())


async def _generate_claude(
    w: WordSample, *, model: str, understanding: bool, exam: str = "ielts"
) -> Tuple[Dict[str, Any], TokenUsage]:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY missing")

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    msg = await client.messages.create(
        model=model,
        max_tokens=1400,
        temperature=0.35,
        messages=[
            {
                "role": "user",
                "content": _build_prompt(w, understanding=understanding, exam=exam),
            }
        ],
    )
    raw = msg.content[0].text if msg.content else "{}"
    return _extract_json(raw), _usage_from_response(msg)


def _usage_from_openai_response(resp: Any) -> TokenUsage:
    u = getattr(resp, "usage", None)
    if not u:
        return TokenUsage()
    inp = int(getattr(u, "input_tokens", 0) or getattr(u, "prompt_tokens", 0) or 0)
    out = int(getattr(u, "output_tokens", 0) or getattr(u, "completion_tokens", 0) or 0)
    return TokenUsage(input_tokens=inp, output_tokens=out)


async def _generate_openai(
    w: WordSample,
    *,
    model: str,
    understanding: bool,
    reasoning_effort: str = "minimal",
    exam: str = "ielts",
) -> Tuple[Dict[str, Any], TokenUsage, int]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY missing")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    kwargs = openai_mcq_request_kwargs(
        model,
        _build_prompt(w, understanding=understanding, exam=exam),
        reasoning_effort=reasoning_effort,
    )
    resp = await client.responses.create(**kwargs)
    raw = (resp.output_text or "").strip() or "{}"
    return (
        _extract_json(raw),
        _usage_from_openai_response(resp),
        openai_reasoning_tokens(resp),
    )


async def _run_trial(
    repo: VocabLexiconRepo,
    words: List[WordSample],
    *,
    model: str,
    understanding: bool,
    label: str,
    provider: str = "anthropic",
    reasoning_effort: str = "minimal",
    exam: str = "ielts",
) -> Dict[str, Any]:
    summary = {
        "label": label,
        "model": model,
        "provider": provider,
        "understanding": understanding,
        "ok": 0,
        "warn": 0,
        "fail": 0,
        "understanding_pass": 0,
        "meaning_total": 0,
        "usage": TokenUsage(),
        "cost_usd": 0.0,
        "calls": 0,
        "elapsed_sec": 0.0,
        "latencies_sec": [],
        "reasoning_tokens": 0,
        "reasoning_effort": reasoning_effort if provider == "openai" else None,
    }

    for i, w in enumerate(words, 1):
        diff = infer_mcq_difficulty(pack_id=w.pack_id, exam=exam)
        print(
            f"\n{'='*72}\n[{label}] [{i}/{len(words)}] {w.display_word} ({w.pack_id}, difficulty={diff})"
        )
        print(f"Def: {w.definition_en[:90]}")
        print(f"Ex:  {w.example[:90]}")

        try:
            t0 = time.perf_counter()
            r_tokens = 0
            if provider == "openai":
                data, usage, r_tokens = await _generate_openai(
                    w,
                    model=model,
                    understanding=understanding,
                    reasoning_effort=reasoning_effort,
                    exam=exam,
                )
                summary["reasoning_tokens"] += r_tokens
            else:
                data, usage = await _generate_claude(
                    w, model=model, understanding=understanding, exam=exam
                )
            elapsed = time.perf_counter() - t0
            summary["elapsed_sec"] += elapsed
            summary["latencies_sec"].append(elapsed)
            summary["usage"].add(usage)
            summary["calls"] += 1
            call_cost = _print_cost_line(model, usage, label=label)
            if provider == "openai" and r_tokens:
                print(f"[{label}] Reasoning tokens (this call): {r_tokens:,}")
            print(f"[{label}] Latency: {elapsed:.2f}s")
            summary["cost_usd"] += call_cost
            for qtype in ("meaning_mcq", "cloze"):
                block = data.get(qtype) or {}
                issues, extra = _validate_mcq_block(
                    block, word=w.display_word, example=w.example, qtype=qtype
                )
                print(f"\n--- {qtype} ---")
                print(f"Q: {block.get('prompt', '')}")
                for o in block.get("options") or []:
                    mark = " *" if o.get("id") == block.get("correct_option_id") else ""
                    print(f"  {o.get('id')}) {str(o.get('text', ''))[:110]}{mark}")
                if qtype == "meaning_mcq" and extra:
                    summary["meaning_total"] += 1
                    u = extra.get("tests_understanding")
                    print(
                        f"Understanding score: grammatical_wrong={extra.get('wrong_grammatical')}/3, "
                        f"broken={extra.get('wrong_broken_english')}, "
                        f"homograph_traps={extra.get('wrong_uses_word_different_sense')} "
                        f"→ {'PASS' if u else 'WEAK'}"
                    )
                    if u:
                        summary["understanding_pass"] += 1
                if issues:
                    print(f"Issues: {', '.join(issues)}")
                    summary["warn"] += 1
                else:
                    print("Issues: none")
                    summary["ok"] += 1
            await asyncio.sleep(0.4)
        except Exception as exc:
            print(f"FAIL: {exc}")
            summary["fail"] += 1

    u: TokenUsage = summary["usage"]
    r_line = ""
    if summary.get("reasoning_tokens"):
        r_line = f" | reasoning={summary['reasoning_tokens']:,}"
    print(
        f"\n[{label}] COST TOTAL: {summary['calls']} calls | "
        f"in={u.input_tokens:,} out={u.output_tokens:,}{r_line} | "
        f"est. {_fmt_cost(summary['cost_usd'])}"
    )
    if summary["calls"]:
        per = summary["cost_usd"] / summary["calls"]
        avg_lat = summary["elapsed_sec"] / summary["calls"]
        print(
            f"[{label}] Per word (1 call = meaning+cloze): {_fmt_cost(per)} | avg latency {avg_lat:.2f}s"
        )
    return summary


def _print_compare_summary(summaries: List[Dict[str, Any]], *, pack_words: int) -> None:
    print(f"\n{'#'*72}\nSUMMARY (quality heuristics + cost + latency)")
    for s in summaries:
        pct = (
            100 * s["understanding_pass"] / s["meaning_total"]
            if s["meaning_total"]
            else 0
        )
        u = s["usage"]
        calls = s.get("calls") or 0
        avg_lat = (s["elapsed_sec"] / calls) if calls else 0.0
        lats = s.get("latencies_sec") or []
        p50 = sorted(lats)[len(lats) // 2] if lats else 0.0
        r_tok = s.get("reasoning_tokens") or 0
        r_eff = s.get("reasoning_effort")
        r_part = f" | reasoning={r_tok:,} ({r_eff})" if r_tok or r_eff else ""
        print(
            f"{s['label']} ({s['model']} / {s.get('provider', 'anthropic')}): "
            f"blocks_ok={s['ok']}, warn={s['warn']}, fail={s['fail']} | "
            f"meaning_understanding_pass={s['understanding_pass']}/{s['meaning_total']} ({pct:.0f}%) | "
            f"tokens in={u.input_tokens:,} out={u.output_tokens:,}{r_part} | "
            f"cost {_fmt_cost(s['cost_usd'])} | "
            f"latency avg={avg_lat:.2f}s p50={p50:.2f}s total={s['elapsed_sec']:.1f}s"
        )
    _print_batch_projection(summaries, pack_words=pack_words)


def _print_batch_projection(
    summaries: List[Dict[str, Any]], *, pack_words: int
) -> None:
    print(f"\n{'#'*72}\nBATCH PROJECTION (~{pack_words:,} pack words, 1 API call/word)")
    for s in summaries:
        calls = s.get("calls") or 0
        if not calls:
            continue
        per = s["cost_usd"] / calls
        total = per * pack_words
        print(
            f"  {s['label']} ({s['model']}): "
            f"{_fmt_cost(per)}/word → ~{_fmt_cost(total)} for full pack"
        )
    print(
        "  Note: Anthropic Console → Settings → Billing shows actual invoiced usage.\n"
        "  Batch API (-50%) and prompt caching can cut cost further in production."
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trial / compare Claude MCQ generation"
    )
    parser.add_argument("-n", type=int, default=10)
    parser.add_argument("--pack-id", default="pack_band_7")
    parser.add_argument("--model", default=None, help="Override ANTHROPIC_MODEL")
    parser.add_argument(
        "--understanding",
        action="store_true",
        help="Prompt tuned to test real meaning (grammatical distractors)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_COMPARE_SAMPLE,
        metavar="N",
        help=f"Words from compare list (max {len(COMPARE_WORDS)}; default {DEFAULT_COMPARE_SAMPLE})",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help=f"Run Haiku + Sonnet on first N compare words (default N={DEFAULT_COMPARE_SAMPLE})",
    )
    parser.add_argument(
        "--compare-openai",
        action="store_true",
        help=f"Run Haiku vs OpenAI on first N compare words (default N={DEFAULT_COMPARE_SAMPLE})",
    )
    parser.add_argument(
        "--compare-gre",
        action="store_true",
        help=f"GRE pack: Haiku vs OpenAI on {len(GRE_COMPARE_WORDS)} GRE words (use --openai-model gpt-5.5-...)",
    )
    parser.add_argument(
        "--openai-only",
        action="store_true",
        help="With --compare-openai: skip Haiku (OpenAI model only)",
    )
    parser.add_argument(
        "--openai-model",
        default=None,
        help="Override OPENAI_MODEL (default from settings)",
    )
    parser.add_argument(
        "--provider",
        choices=("anthropic", "openai"),
        default="anthropic",
        help="LLM backend for single-model trial",
    )
    parser.add_argument(
        "--project-words",
        type=int,
        default=12_521,
        help="Pack word count for batch cost projection (default 12521)",
    )
    parser.add_argument(
        "--reasoning-effort",
        default="minimal",
        choices=("none", "minimal", "low", "medium", "high"),
        help="OpenAI reasoning.effort (minimal=gpt-5-nano; none/low for gpt-5.5 / gpt-5.4-nano)",
    )
    args = parser.parse_args()

    core_db.init_pg()
    sm = core_db.pg_sessionmaker()
    async with sm() as session:
        repo = VocabLexiconRepo(session)

        if args.compare_openai or args.compare_gre:
            gre = bool(args.compare_gre)
            pack_id = "pack_gre" if gre else args.pack_id
            pick = _compare_word_list(args.sample, gre=gre)
            words = await _sample_words(repo, len(pick), pack_id, word_list=pick)
            settings = get_settings()
            haiku = settings.anthropic_model
            openai_model = args.openai_model or (
                "gpt-5.5-2026-04-23" if gre else settings.openai_model
            )
            exam = "gre" if gre else "ielts"
            pack_words = DEFAULT_GRE_PACK_WORDS if gre else args.project_words
            effort = args.reasoning_effort
            if (
                "5.4-nano" in openai_model or "5.5" in openai_model
            ) and effort == "minimal":
                effort = "none"
            diff_label = infer_mcq_difficulty(pack_id=pack_id, exam=exam)
            print(
                f"Compare {'GRE' if gre else 'IELTS'} sample ({len(words)} words: {', '.join(pick)})\n"
                f"  Haiku={haiku}\n  OpenAI={openai_model}\n"
                f"  Pack={pack_id} | prompt difficulty={diff_label}\n"
                f"  OpenAI reasoning.effort={effort}\n"
            )

            summaries: List[Dict[str, Any]] = []
            if not args.openai_only:
                summaries.append(
                    await _run_trial(
                        repo,
                        words,
                        model=haiku,
                        understanding=True,
                        label="Haiku",
                        provider="anthropic",
                        exam=exam,
                    )
                )
            oai_label = openai_model.split("/")[-1][:24]
            summaries.append(
                await _run_trial(
                    repo,
                    words,
                    model=openai_model,
                    understanding=True,
                    label=oai_label,
                    provider="openai",
                    reasoning_effort=effort,
                    exam=exam,
                )
            )
            _print_compare_summary(summaries, pack_words=pack_words)
            return

        if args.compare:
            pick = _compare_word_list(args.sample, gre=False)
            words = await _sample_words(repo, len(pick), args.pack_id, word_list=pick)
            haiku = get_settings().anthropic_model
            sonnet = args.model or "claude-sonnet-4-6"
            print(
                f"Compare sample ({len(words)} words: {', '.join(pick)}) | Haiku={haiku} vs Sonnet={sonnet}"
            )
            print("Prompt mode: understanding-focused (both models)\n")

            s_h = await _run_trial(
                repo, words, model=haiku, understanding=True, label="Haiku"
            )
            s_s = await _run_trial(
                repo, words, model=sonnet, understanding=True, label="Sonnet-4.6"
            )
            _print_compare_summary([s_h, s_s], pack_words=args.project_words)
            return

        if args.provider == "openai":
            model = args.openai_model or get_settings().openai_model
        else:
            model = args.model or get_settings().anthropic_model
        logger.info(
            "Model: {} | provider={} | understanding={}",
            model,
            args.provider,
            args.understanding,
        )
        words = await _sample_words(repo, args.n, args.pack_id)
        await _run_trial(
            repo,
            words,
            model=model,
            understanding=args.understanding,
            label=model,
            provider=args.provider,
            reasoning_effort=args.reasoning_effort,
        )


if __name__ == "__main__":
    asyncio.run(main())
