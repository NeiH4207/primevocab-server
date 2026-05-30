"""Backfill IELTS band packs 4–9: Step 3 prompts + MCQ via OpenAI gpt-5.4-nano.

Workflow:
  1. trial   — spot-check prompts on a few words per band (no DB write)
  2. step3   — vi_translate_prompt + topic_prompt → DB
  3. mcq     — meaning_mcq + cloze → vocab_questions
  4. all     — step3 then mcq for each pack (or --pack-id one pack)

Example:
  docker compose exec api python -m aiforen.scripts.vocab.backfill_llm_bands trial
  docker compose exec api python -m aiforen.scripts.vocab.backfill_llm_bands all --concurrency 8
  docker compose exec api python -m aiforen.scripts.vocab.backfill_llm_bands mcq --pack-id pack_band_7 --limit 100
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("PG_HOST", "127.0.0.1")
os.environ.setdefault("PG_PORT", "55432")

from loguru import logger

from aiforen.core.config import get_settings
from aiforen.scripts.vocab._common import pg_session, run_async
from aiforen.scripts.vocab.gloss_llm import (
    GlossWord,
    build_multi_gloss_prompt,
    chunk_gloss_words,
    fetch_words_for_gloss,
    max_tokens_gloss_chunk,
    parse_multi_gloss_response,
    validate_gloss_item,
)
from aiforen.scripts.vocab.mcq_llm import (
    TokenUsage,
    WordSample,
    build_single_prompt,
    cost_usd,
    extract_json,
    fetch_words_for_mcq,
    fmt_cost,
    infer_mcq_difficulty,
    normalize_word_item,
    openai_mcq_request_kwargs,
    validate_word_items,
)
from aiforen.scripts.vocab.step3_llm import (
    Step3Word,
    build_multi_step3_prompt,
    chunk_step3_words,
    fetch_words_for_step3,
    infer_step3_band_level,
    max_tokens_step3_chunk,
    parse_multi_step3_response,
    validate_step3_item,
)

BAND_PACKS = [
    "pack_band_4",
    "pack_band_5",
    "pack_band_6",
    "pack_band_7",
    "pack_band_8",
    "pack_band_9",
]

ALL_STEP3_PACKS = [*BAND_PACKS, "pack_gre"]

MCQ_SOURCE = "llm_mcq_openai"
STEP3_SOURCE = "llm_step3_openai"


def _openai_model(args: argparse.Namespace) -> str:
    return args.model or get_settings().openai_model or "gpt-5.4-nano-2026-03-17"


def _reasoning_effort(model: str, raw: str) -> str:
    if "5.4-nano" in model or "5.5" in model:
        return "none" if raw in ("minimal", "none") else raw
    return raw


async def _openai_json(
    *,
    model: str,
    prompt: str,
    max_output_tokens: int,
    reasoning_effort: str,
) -> Tuple[str, TokenUsage]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY missing")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    kwargs = openai_mcq_request_kwargs(model, prompt, reasoning_effort=reasoning_effort)
    kwargs["max_output_tokens"] = max_output_tokens
    resp = await client.responses.create(**kwargs)
    raw = (resp.output_text or "").strip() or "{}"
    u = getattr(resp, "usage", None)
    usage = TokenUsage(
        input_tokens=int(
            getattr(u, "input_tokens", 0) or getattr(u, "prompt_tokens", 0) or 0
        ),
        output_tokens=int(
            getattr(u, "output_tokens", 0) or getattr(u, "completion_tokens", 0) or 0
        ),
    )
    return raw, usage


async def cmd_trial(args: argparse.Namespace) -> None:
    model = _openai_model(args)
    effort = _reasoning_effort(model, args.reasoning_effort)
    packs = [args.pack_id] if args.pack_id else ["pack_band_5", "pack_band_8"]
    usage = TokenUsage()

    async for repo in pg_session():
        for pack_id in packs:
            words_mcq = await fetch_words_for_mcq(
                repo.s,
                pack_id=pack_id,
                limit=args.sample,
                skip_existing=False,
            )
            words_s3 = await fetch_words_for_step3(
                repo.s,
                pack_id=pack_id,
                limit=args.sample,
                skip_existing=False,
            )
            if not words_mcq and not words_s3:
                print(f"\n=== {pack_id}: no words ===")
                continue

            print(
                f"\n{'#'*72}\nPACK {pack_id} | mcq_difficulty={infer_mcq_difficulty(pack_id=pack_id)} "
                f"| step3_level={infer_step3_band_level(pack_id=pack_id)}\n"
            )

            for w in words_mcq[: args.sample]:
                diff = infer_mcq_difficulty(pack_id=w.pack_id)
                prompt = build_single_prompt(w, understanding=True, pack_id=w.pack_id)
                raw, u = await _openai_json(
                    model=model,
                    prompt=prompt,
                    max_output_tokens=1800,
                    reasoning_effort=effort,
                )
                usage.add(u)
                data = extract_json(raw)
                item = normalize_word_item(data)
                ok, issues = validate_word_items(item, w)
                print(
                    f"\n[MCQ] {w.display_word} ({diff}) ok={ok} issues={issues or 'none'}"
                )
                mm = item.get("meaning_mcq") or {}
                print(f"  meaning Q: {(mm.get('prompt') or '')[:100]}...")

            if words_s3:
                chunk = chunk_step3_words(
                    words_s3[: args.sample], len(words_s3[: args.sample])
                )[0]
                prompt = build_multi_step3_prompt(chunk.words, pack_family="band")
                raw, u = await _openai_json(
                    model=model,
                    prompt=prompt,
                    max_output_tokens=max_tokens_step3_chunk(len(chunk.words)),
                    reasoning_effort=effort,
                )
                usage.add(u)
                by_word = parse_multi_step3_response(raw, chunk.words)
                for w in chunk.words:
                    item = by_word.get(w.display_word.lower()) or {}
                    ok, issues = validate_step3_item(item, w)
                    print(
                        f"\n[Step3] {w.display_word} ({infer_step3_band_level(pack_id=w.pack_id)}) "
                        f"ok={ok} issues={issues or 'none'}"
                    )
                    print(f"  translate: {item.get('vi_translate_prompt', '')}")
                    print(f"  topic:     {item.get('topic_prompt', '')}")

    print(
        f"\nTrial cost ~{fmt_cost(cost_usd(model, usage))} | model={model} effort={effort}"
    )


async def _generate_step3_chunk(
    chunk_words: List[Step3Word],
    *,
    model: str,
    effort: str,
    usage: TokenUsage,
) -> Optional[Dict[str, Dict[str, Any]]]:
    pack_family = chunk_words[0].pack_family if chunk_words else "band"
    prompt = build_multi_step3_prompt(chunk_words, pack_family=pack_family)
    try:
        raw, u = await _openai_json(
            model=model,
            prompt=prompt,
            max_output_tokens=max_tokens_step3_chunk(len(chunk_words)),
            reasoning_effort=effort,
        )
        usage.add(u)
        return parse_multi_step3_response(raw, chunk_words)
    except Exception as exc:
        logger.error("step3 chunk failed: {}", exc)
        return None


async def _persist_step3_chunk(
    repo: Any,
    chunk_words: List[Step3Word],
    by_word: Dict[str, Dict[str, Any]],
    *,
    allow_invalid: bool,
    stats: Dict[str, int],
    db_lock: asyncio.Lock,
) -> None:
    async with db_lock:
        for w in chunk_words:
            item = by_word.get(w.display_word.lower()) or {}
            ok, issues = validate_step3_item(item, w)
            if not ok and not allow_invalid:
                stats["warn"] += 1
                logger.warning("{} step3: {}", w.display_word, issues)
                continue
            trans = str(item.get("vi_translate_prompt", "")).strip()
            topic = str(item.get("topic_prompt", "")).strip()
            if not trans or not topic:
                stats["err"] += 1
                continue
            await repo.patch_primary_sense_gloss(
                w.lexeme_id,
                vi_gloss=w.vi_gloss,
                vi_translate_prompt=trans,
                topic_prompt=topic,
            )
            stats["ok"] += 1


async def _generate_mcq_item(
    w: WordSample,
    *,
    model: str,
    effort: str,
    usage: TokenUsage,
) -> Optional[Tuple[Dict[str, Any], List[str]]]:
    prompt = build_single_prompt(w, understanding=True, pack_id=w.pack_id)
    try:
        raw, u = await _openai_json(
            model=model,
            prompt=prompt,
            max_output_tokens=1800,
            reasoning_effort=effort,
        )
        usage.add(u)
        data = extract_json(raw)
        item = normalize_word_item(data)
        ok, issues = validate_word_items(item, w)
        return item, issues if not ok else []
    except Exception as exc:
        logger.error("{} mcq generate: {}", w.display_word, exc)
        return None


async def _persist_mcq_word(
    repo: Any,
    w: WordSample,
    item: Dict[str, Any],
    issues: List[str],
    *,
    model: str,
    allow_invalid: bool,
    approve: bool,
    stats: Dict[str, int],
    db_lock: asyncio.Lock,
) -> None:
    if issues and not allow_invalid:
        stats["warn"] += 1
        logger.warning("{} mcq: {}", w.display_word, issues)
        return
    status = "validated" if approve else "generated"
    meta_base = {
        "source": MCQ_SOURCE,
        "model": model,
        "difficulty": infer_mcq_difficulty(pack_id=w.pack_id),
        "understanding": True,
    }
    if issues:
        meta_base["validation_issues"] = issues
    async with db_lock:
        for qtype in ("meaning_mcq", "cloze"):
            block = item.get(qtype) or {}
            await repo.upsert_question(
                w.lexeme_id,
                qtype=qtype,
                prompt=str(block.get("prompt", "")),
                options=block.get("options") or [],
                correct_option_id=str(block.get("correct_option_id", "a")),
                explanation=str(block.get("explanation", "")),
                status=status,
                sense_id=w.sense_id,
                generator_meta={**meta_base, "qtype": qtype},
            )
            stats["questions"] += 1
        stats["ok"] += 1


async def _run_pack_step3(
    pack_id: str,
    args: argparse.Namespace,
    *,
    model: str,
    effort: str,
) -> Dict[str, Any]:
    stats = {"ok": 0, "warn": 0, "err": 0}
    usage = TokenUsage()
    t0 = time.perf_counter()

    async for repo in pg_session():
        words = await fetch_words_for_step3(
            repo.s,
            pack_id=pack_id,
            limit=args.limit,
            skip_existing=not args.force,
        )
        if not words:
            logger.info("{} step3: nothing to do", pack_id)
            return {"pack_id": pack_id, "words": 0, **stats}

        chunks = chunk_step3_words(words, max(1, args.words_per_request))
        sem = asyncio.Semaphore(max(1, args.concurrency))
        db_lock = asyncio.Lock()

        async def run_chunk(cw: List[Step3Word]) -> None:
            async with sem:
                by_word = await _generate_step3_chunk(
                    cw, model=model, effort=effort, usage=usage
                )
            if not by_word:
                stats["err"] += len(cw)
                return
            await _persist_step3_chunk(
                repo,
                cw,
                by_word,
                allow_invalid=args.allow_invalid,
                stats=stats,
                db_lock=db_lock,
            )

        await asyncio.gather(*[run_chunk(c.words) for c in chunks])
        await repo.s.commit()

    elapsed = time.perf_counter() - t0
    cost = cost_usd(model, usage)
    print(
        f"{pack_id} step3 | words={len(words)} chunks={len(chunks)} | "
        f"ok={stats['ok']} warn={stats['warn']} err={stats['err']} | "
        f"{fmt_cost(cost)} | {elapsed:.0f}s"
    )
    return {"pack_id": pack_id, "words": len(words), "cost": cost, **stats}


async def _run_pack_mcq(
    pack_id: str,
    args: argparse.Namespace,
    *,
    model: str,
    effort: str,
) -> Dict[str, Any]:
    stats = {"ok": 0, "warn": 0, "err": 0, "questions": 0}
    usage = TokenUsage()
    t0 = time.perf_counter()

    async for repo in pg_session():
        words = await fetch_words_for_mcq(
            repo.s,
            pack_id=pack_id,
            limit=args.limit,
            skip_existing=not args.force,
        )
        if not words:
            logger.info("{} mcq: nothing to do", pack_id)
            return {"pack_id": pack_id, "words": 0, **stats}

        sem = asyncio.Semaphore(max(1, args.concurrency))
        db_lock = asyncio.Lock()

        async def run_word(w: WordSample) -> None:
            async with sem:
                result = await _generate_mcq_item(
                    w, model=model, effort=effort, usage=usage
                )
            if not result:
                stats["err"] += 1
                return
            item, issues = result
            await _persist_mcq_word(
                repo,
                w,
                item,
                issues,
                model=model,
                allow_invalid=args.allow_invalid,
                approve=args.approve,
                stats=stats,
                db_lock=db_lock,
            )

        await asyncio.gather(*[run_word(w) for w in words])
        await repo.s.commit()

    elapsed = time.perf_counter() - t0
    cost = cost_usd(model, usage)
    print(
        f"{pack_id} mcq | words={len(words)} | ok={stats['ok']} warn={stats['warn']} "
        f"err={stats['err']} q={stats['questions']} | {fmt_cost(cost)} | {elapsed:.0f}s"
    )
    return {"pack_id": pack_id, "words": len(words), "cost": cost, **stats}


async def _generate_gloss_chunk(
    chunk_words: List[GlossWord],
    *,
    model: str,
    effort: str,
    usage: TokenUsage,
) -> Dict[str, Dict[str, Any]]:
    by_word: Dict[str, Dict[str, Any]] = {}

    async def _call(batch: List[GlossWord]) -> None:
        if not batch:
            return
        raw, u = await _openai_json(
            model=model,
            prompt=build_multi_gloss_prompt(batch),
            max_output_tokens=max_tokens_gloss_chunk(len(batch)),
            reasoning_effort=effort,
        )
        usage.add(u)
        by_word.update(parse_multi_gloss_response(raw, batch))

    try:
        await _call(chunk_words)
    except Exception as exc:
        logger.debug("gloss chunk {} words: {}", len(chunk_words), exc)

    for w in chunk_words:
        if w.display_word.lower() in by_word:
            continue
        try:
            await _call([w])
        except Exception as exc:
            logger.warning("{} gloss retry failed: {}", w.display_word, exc)
    return by_word


async def _persist_gloss_chunk(
    repo: Any,
    chunk_words: List[GlossWord],
    by_word: Dict[str, Dict[str, Any]],
    *,
    allow_invalid: bool,
    stats: Dict[str, int],
    db_lock: asyncio.Lock,
) -> None:
    async with db_lock:
        for w in chunk_words:
            item = by_word.get(w.display_word.lower()) or {}
            ok, issues = validate_gloss_item(item, w)
            if not ok and not allow_invalid:
                stats["warn"] += 1
                logger.warning("{} gloss: {}", w.display_word, issues)
                continue
            gloss = str(item.get("vi_gloss", "")).strip()
            syns = [
                str(s).strip().lower()
                for s in (item.get("synonyms") or [])
                if str(s).strip()
            ]
            syns = [s for s in syns if s != w.display_word.lower()][:6]
            if not gloss or len(syns) < 2:
                stats["err"] += 1
                continue
            await repo.patch_primary_sense_gloss(
                w.lexeme_id,
                vi_gloss=gloss,
                synonyms=syns,
            )
            stats["ok"] += 1


async def _run_pack_gloss(
    pack_id: str,
    args: argparse.Namespace,
    *,
    model: str,
    effort: str,
) -> Dict[str, Any]:
    stats = {"ok": 0, "warn": 0, "err": 0}
    usage = TokenUsage()
    t0 = time.perf_counter()

    async for repo in pg_session():
        words = await fetch_words_for_gloss(
            repo.s,
            pack_id=pack_id,
            limit=args.limit,
            force=args.force,
        )
        if not words:
            logger.info("{} gloss: nothing to do", pack_id)
            return {"pack_id": pack_id, "words": 0, **stats}

        chunks = chunk_gloss_words(words, max(1, args.words_per_request))
        sem = asyncio.Semaphore(max(1, args.concurrency))
        db_lock = asyncio.Lock()

        async def run_chunk(cw: List[GlossWord]) -> None:
            async with sem:
                by_word = await _generate_gloss_chunk(
                    cw, model=model, effort=effort, usage=usage
                )
            await _persist_gloss_chunk(
                repo,
                cw,
                by_word,
                allow_invalid=args.allow_invalid,
                stats=stats,
                db_lock=db_lock,
            )

        await asyncio.gather(*[run_chunk(c) for c in chunks])
        await repo.s.commit()

    elapsed = time.perf_counter() - t0
    cost = cost_usd(model, usage)
    print(
        f"{pack_id} gloss | words={len(words)} chunks={len(chunks)} | "
        f"ok={stats['ok']} warn={stats['warn']} err={stats['err']} | "
        f"{fmt_cost(cost)} | {elapsed:.0f}s"
    )
    return {"pack_id": pack_id, "words": len(words), "cost": cost, **stats}


async def cmd_step3(args: argparse.Namespace) -> None:
    model = _openai_model(args)
    effort = _reasoning_effort(model, args.reasoning_effort)
    packs = [args.pack_id] if args.pack_id else ALL_STEP3_PACKS
    print(f"Step3 backfill | model={model} effort={effort} packs={packs}")
    for pack_id in packs:
        await _run_pack_step3(pack_id, args, model=model, effort=effort)


async def cmd_gloss(args: argparse.Namespace) -> None:
    model = _openai_model(args)
    effort = _reasoning_effort(model, args.reasoning_effort)
    packs = [args.pack_id] if args.pack_id else ALL_STEP3_PACKS
    print(f"Gloss+synonyms backfill | model={model} effort={effort} packs={packs}")
    for pack_id in packs:
        await _run_pack_gloss(pack_id, args, model=model, effort=effort)


async def cmd_mcq(args: argparse.Namespace) -> None:
    model = _openai_model(args)
    effort = _reasoning_effort(model, args.reasoning_effort)
    packs = [args.pack_id] if args.pack_id else BAND_PACKS
    print(f"MCQ backfill | model={model} effort={effort} packs={packs}")
    for pack_id in packs:
        await _run_pack_mcq(pack_id, args, model=model, effort=effort)


async def cmd_all(args: argparse.Namespace) -> None:
    if not args.mcq_only:
        await cmd_step3(args)
    if not args.step3_only:
        await cmd_mcq(args)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill band 4–9 LLM step3 + MCQ (OpenAI)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--pack-id", default=None, help="Single pack; default all band 4–9"
    )
    common.add_argument("--limit", type=int, default=None)
    common.add_argument("--model", default=None)
    common.add_argument("--concurrency", type=int, default=6)
    common.add_argument(
        "--words-per-request",
        type=int,
        default=5,
        help="Step3 only: words per API call",
    )
    common.add_argument(
        "--force", action="store_true", help="Regenerate even if already filled"
    )
    common.add_argument("--allow-invalid", action="store_true")
    common.add_argument(
        "--reasoning-effort",
        default="none",
        choices=("none", "minimal", "low", "medium", "high"),
    )

    p_trial = sub.add_parser(
        "trial", parents=[common], help="Spot-check prompts (no DB)"
    )
    p_trial.add_argument("--sample", type=int, default=2)
    p_trial.set_defaults(func=lambda a: run_async(cmd_trial(a)))

    p_s3 = sub.add_parser(
        "step3", parents=[common], help="Backfill vi_translate + topic"
    )
    p_s3.set_defaults(func=lambda a: run_async(cmd_step3(a)))

    p_gl = sub.add_parser(
        "gloss",
        parents=[common],
        help="Backfill vi_gloss + synonyms from definition_en",
    )
    p_gl.set_defaults(func=lambda a: run_async(cmd_gloss(a)))

    p_mcq = sub.add_parser("mcq", parents=[common], help="Backfill meaning_mcq + cloze")
    p_mcq.add_argument("--approve", action="store_true", help="status=validated")
    p_mcq.set_defaults(func=lambda a: run_async(cmd_mcq(a)))

    p_all = sub.add_parser("all", parents=[common], help="step3 then mcq")
    p_all.add_argument("--step3-only", action="store_true")
    p_all.add_argument("--mcq-only", action="store_true")
    p_all.add_argument("--approve", action="store_true")
    p_all.set_defaults(func=lambda a: run_async(cmd_all(a)))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
