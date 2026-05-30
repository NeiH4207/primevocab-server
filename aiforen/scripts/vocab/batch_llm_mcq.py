"""Batch + multi-word LLM MCQ generation via Anthropic Message Batches API (-50% cost).

Workflow:
  1. submit  — build multi-word requests, create batch job, save manifest
  2. status  — poll batch processing_status
  3. import  — download results JSONL, validate, upsert vocab_questions

Example:
  docker compose exec api python -m aiforen.scripts.vocab.batch_llm_mcq submit \\
    --pack-id pack_band_7 --words-per-request 5 --limit 50

  docker compose exec api python -m aiforen.scripts.vocab.batch_llm_mcq status

  docker compose exec api python -m aiforen.scripts.vocab.batch_llm_mcq import --approve-generated
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("PG_HOST", "127.0.0.1")
os.environ.setdefault("PG_PORT", "55432")

from loguru import logger

from aiforen.core.config import get_settings
from aiforen.scripts.vocab._common import pg_session, run_async
from aiforen.scripts.vocab.mcq_llm import (
    BATCH_DISCOUNT,
    McqChunk,
    TokenUsage,
    WordSample,
    build_multi_prompt,
    chunk_words,
    cost_usd,
    fetch_words_for_mcq,
    fmt_cost,
    max_tokens_for_chunk,
    parse_multi_response,
    usage_from_response,
    validate_mcq_block,
    validate_word_items,
)


def _batch_dir() -> Path:
    raw = os.environ.get("MCQ_BATCH_DIR", "").strip()
    if raw:
        return Path(raw)
    for candidate in (
        Path("/tmp/aiforen-mcq-batches"),
        Path(__file__).resolve().parent / "data" / "mcq_batches",
    ):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test = candidate / ".write_test"
            test.write_text("ok")
            test.unlink()
            return candidate
        except OSError:
            continue
    return Path("/tmp/aiforen-mcq-batches")


BATCH_DIR = _batch_dir()
LATEST_MANIFEST = BATCH_DIR / "latest.json"
SOURCE_TAG = "llm_mcq_batch"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_manifest(data: Dict[str, Any]) -> Path:
    batch_dir = _batch_dir()
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_id = data["batch_id"]
    path = batch_dir / f"{batch_id}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    latest = batch_dir / "latest.json"
    latest.write_text(json.dumps({"batch_id": batch_id, "path": str(path)}, indent=2))
    return path


def _load_manifest(batch_id: Optional[str] = None) -> Dict[str, Any]:
    batch_dir = _batch_dir()
    if batch_id:
        path = batch_dir / f"{batch_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No manifest for batch_id={batch_id}")
        return json.loads(path.read_text(encoding="utf-8"))
    latest_path = batch_dir / "latest.json"
    if not latest_path.exists():
        raise FileNotFoundError("No latest batch manifest — run submit first")
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    return json.loads(Path(latest["path"]).read_text(encoding="utf-8"))


def _estimate_batch_cost(chunks: List[McqChunk], model: str) -> Dict[str, Any]:
    """Rough estimate: ~520 input + ~580 output tokens per word in chunk."""
    n_words = sum(len(c.words) for c in chunks)
    n_requests = len(chunks)
    est_in = sum(180 + len(c.words) * 520 for c in chunks)
    est_out = sum(200 + len(c.words) * 580 for c in chunks)
    usage = TokenUsage(input_tokens=est_in, output_tokens=est_out)
    std = cost_usd(model, usage, batch=False)
    batched = cost_usd(model, usage, batch=True)
    return {
        "words": n_words,
        "requests": n_requests,
        "est_input_tokens": est_in,
        "est_output_tokens": est_out,
        "est_cost_standard": std,
        "est_cost_batch": batched,
        "est_per_word_batch": batched / n_words if n_words else 0,
    }


def _chunk_to_request(
    chunk: McqChunk, *, model: str, understanding: bool
) -> Dict[str, Any]:
    return {
        "custom_id": chunk.custom_id,
        "params": {
            "model": model,
            "max_tokens": max_tokens_for_chunk(len(chunk.words)),
            "temperature": 0.35,
            "messages": [
                {
                    "role": "user",
                    "content": build_multi_prompt(
                        chunk.words, understanding=understanding
                    ),
                }
            ],
        },
    }


async def cmd_submit(args: argparse.Namespace) -> None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY missing")

    model = args.model or settings.anthropic_model
    words: List[WordSample] = []

    async for repo in pg_session():
        words = await fetch_words_for_mcq(
            repo.s,
            pack_id=args.pack_id,
            limit=args.limit,
            skip_existing=not args.force,
        )

    if not words:
        logger.warning("No words to process (all have llm_mcq_batch questions?)")
        return

    chunks = chunk_words(words, max(1, args.words_per_request))
    est = _estimate_batch_cost(chunks, model)

    print(
        f"Words: {est['words']} | API requests: {est['requests']} "
        f"({args.words_per_request} words/request)"
    )
    print(
        f"Est. tokens in≈{est['est_input_tokens']:,} out≈{est['est_output_tokens']:,}"
    )
    print(
        f"Est. cost: standard {fmt_cost(est['est_cost_standard'])} → "
        f"batch -50% {fmt_cost(est['est_cost_batch'])} "
        f"({fmt_cost(est['est_per_word_batch'])}/word)"
    )

    understanding = not args.no_understanding
    requests = [
        _chunk_to_request(c, model=model, understanding=understanding) for c in chunks
    ]

    if args.dry_run:
        print(f"\n[DRY RUN] Would submit {len(requests)} batch requests.")
        print(
            f"Sample custom_id={chunks[0].custom_id} words={[w.display_word for w in chunks[0].words]}"
        )
        print(
            build_multi_prompt(chunks[0].words, understanding=understanding)[:800],
            "...",
        )
        return

    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    batch = client.messages.batches.create(requests=requests)

    manifest = {
        "batch_id": batch.id,
        "model": model,
        "understanding": understanding,
        "words_per_request": args.words_per_request,
        "pack_id": args.pack_id,
        "word_count": len(words),
        "request_count": len(chunks),
        "created_at": _utc_now(),
        "processing_status": batch.processing_status,
        "estimate": est,
        "chunks": [
            {
                "custom_id": c.custom_id,
                "words": [
                    {
                        "lexeme_id": str(w.lexeme_id),
                        "sense_id": str(w.sense_id),
                        "display_word": w.display_word,
                        "pos": w.pos,
                        "pack_id": w.pack_id,
                        "definition_en": w.definition_en,
                        "example": w.example,
                        "vi_gloss": w.vi_gloss,
                    }
                    for w in c.words
                ],
            }
            for c in chunks
        ],
    }
    path = _save_manifest(manifest)
    print(f"\nBatch submitted: {batch.id}")
    print(f"Status: {batch.processing_status}")
    print(f"Manifest: {path}")
    print("Poll: python -m aiforen.scripts.vocab.batch_llm_mcq status")
    print("Import when ended: python -m aiforen.scripts.vocab.batch_llm_mcq import")


def cmd_status(args: argparse.Namespace) -> None:
    settings = get_settings()
    manifest = _load_manifest(args.batch_id)
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    batch = client.messages.batches.retrieve(manifest["batch_id"])
    counts = getattr(batch, "request_counts", None)
    print(f"Batch: {batch.id}")
    print(f"Status: {batch.processing_status}")
    if counts:
        print(
            f"Requests: processing={getattr(counts,'processing',0)} succeeded="
            f"{getattr(counts,'succeeded',0)} errored={getattr(counts,'errored',0)} "
            f"expired={getattr(counts,'expired',0)} canceled={getattr(counts,'canceled',0)}"
        )
    if batch.processing_status == "ended":
        print("Ready to import.")
    elif batch.processing_status in ("in_progress", "validating"):
        print("Still running — check again in a few minutes.")


def _message_text(msg: Any) -> str:
    parts: List[str] = []
    for block in getattr(msg, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()


def _word_from_manifest(wm: Dict[str, Any]) -> WordSample:
    return WordSample(
        lexeme_id=uuid.UUID(wm["lexeme_id"]),
        sense_id=uuid.UUID(wm["sense_id"]),
        display_word=wm["display_word"],
        pos=wm.get("pos") or "word",
        pack_id=wm["pack_id"],
        definition_en=wm.get("definition_en") or wm["display_word"],
        example=wm.get("example") or "",
        vi_gloss=wm.get("vi_gloss") or "",
    )


async def cmd_import(args: argparse.Namespace) -> None:
    settings = get_settings()
    manifest = _load_manifest(args.batch_id)
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    batch = client.messages.batches.retrieve(manifest["batch_id"])
    if batch.processing_status != "ended":
        raise RuntimeError(f"Batch not finished: {batch.processing_status}")

    chunk_by_id = {c["custom_id"]: c for c in manifest["chunks"]}
    stats = {"ok": 0, "warn": 0, "err": 0, "questions": 0}
    usage = TokenUsage()
    status = "validated" if args.approve_generated else "generated"

    async for repo in pg_session():
        for line in client.messages.batches.results(manifest["batch_id"]):
            custom_id = line.custom_id
            chunk_meta = chunk_by_id.get(custom_id)
            if not chunk_meta:
                logger.warning("Unknown custom_id {}", custom_id)
                stats["err"] += 1
                continue

            result = line.result
            if result.type != "succeeded":
                logger.error("{} → {}", custom_id, result.type)
                stats["err"] += 1
                continue

            msg = result.message
            usage.add(usage_from_response(msg))
            raw = _message_text(msg)
            samples = [_word_from_manifest(wm) for wm in chunk_meta["words"]]

            try:
                by_word = parse_multi_response(raw, samples)
            except Exception as exc:
                logger.error("{} parse failed: {}", custom_id, exc)
                stats["err"] += 1
                continue

            for w in samples:
                item = by_word.get(w.display_word.lower()) or {}
                ok, issues = validate_word_items(item, w)
                if not ok and not args.allow_invalid:
                    logger.warning(
                        "{} {} issues: {}", custom_id, w.display_word, issues
                    )
                    stats["warn"] += 1
                    continue

                meta_base = {
                    "source": SOURCE_TAG,
                    "model": manifest["model"],
                    "batch_id": manifest["batch_id"],
                    "custom_id": custom_id,
                    "understanding": manifest.get("understanding", True),
                }
                if issues:
                    meta_base["validation_issues"] = issues

                for qtype in ("meaning_mcq", "cloze"):
                    block = item.get(qtype) or {}
                    v_issues, _ = validate_mcq_block(
                        block, word=w.display_word, example=w.example, qtype=qtype
                    )
                    if v_issues and not args.allow_invalid:
                        continue
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

                if ok:
                    stats["ok"] += 1
                else:
                    stats["warn"] += 1

        await repo.s.commit()

    model = manifest["model"]
    cost = cost_usd(model, usage, batch=True)
    print(
        f"\nImport done | words_ok={stats['ok']} warn={stats['warn']} err={stats['err']} "
        f"| questions={stats['questions']}"
    )
    print(
        f"Usage: in={usage.input_tokens:,} out={usage.output_tokens:,} | "
        f"batch cost ~{fmt_cost(cost)} (×{BATCH_DISCOUNT} discount)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Anthropic Batch API + multi-word MCQ")
    sub = parser.add_subparsers(dest="command", required=True)

    p_submit = sub.add_parser("submit", help="Create Message Batch job")
    p_submit.add_argument("--pack-id", default=None)
    p_submit.add_argument("--limit", type=int, default=None)
    p_submit.add_argument("--model", default=None)
    p_submit.add_argument("--words-per-request", type=int, default=5)
    p_submit.add_argument(
        "--no-understanding",
        action="store_true",
        help="Use shorter prompt (default: understanding-focused)",
    )
    p_submit.add_argument(
        "--force", action="store_true", help="Include words already batch-generated"
    )
    p_submit.add_argument("--dry-run", action="store_true")
    p_submit.set_defaults(func=lambda a: run_async(cmd_submit(a)))

    p_status = sub.add_parser("status", help="Poll batch job status")
    p_status.add_argument("--batch-id", default=None)
    p_status.set_defaults(func=cmd_status)

    p_import = sub.add_parser("import", help="Import succeeded batch results into DB")
    p_import.add_argument("--batch-id", default=None)
    p_import.add_argument(
        "--approve-generated",
        action="store_true",
        help="Set question status=validated (default: generated)",
    )
    p_import.add_argument(
        "--allow-invalid",
        action="store_true",
        help="Write questions even when validation fails",
    )
    p_import.set_defaults(func=lambda a: run_async(cmd_import(a)))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
