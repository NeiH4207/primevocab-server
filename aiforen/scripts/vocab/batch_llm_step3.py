"""Batch + multi-word Step 3 prompts (vi_translate + topic) via Anthropic Message Batches API.

Example (full GRE):
  docker compose exec api python -m aiforen.scripts.vocab.batch_llm_step3 submit --pack-id pack_gre
  docker compose exec api python -m aiforen.scripts.vocab.batch_llm_step3 status
  docker compose exec api python -m aiforen.scripts.vocab.batch_llm_step3 import
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("PG_HOST", "127.0.0.1")
os.environ.setdefault("PG_PORT", "55432")

from loguru import logger

from aiforen.core.config import get_settings
from aiforen.scripts.vocab._common import pg_session, run_async
from aiforen.scripts.vocab.mcq_llm import (
    BATCH_DISCOUNT,
    TokenUsage,
    cost_usd,
    fmt_cost,
    usage_from_response,
)
from aiforen.scripts.vocab.step3_llm import (
    Step3Chunk,
    Step3Word,
    build_multi_step3_prompt,
    chunk_step3_words,
    fetch_words_for_step3,
    max_tokens_step3_chunk,
    parse_multi_step3_response,
    validate_step3_item,
)

SOURCE_TAG = "llm_step3_batch"


def _batch_dir() -> Path:
    raw = os.environ.get("STEP3_BATCH_DIR", "").strip()
    if raw:
        return Path(raw)
    for candidate in (
        Path("/tmp/aiforen-step3-batches"),
        Path(__file__).resolve().parent / "data" / "step3_batches",
    ):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test = candidate / ".write_test"
            test.write_text("ok")
            test.unlink()
            return candidate
        except OSError:
            continue
    return Path("/tmp/aiforen-step3-batches")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_manifest(data: Dict[str, Any]) -> Path:
    batch_dir = _batch_dir()
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_id = data["batch_id"]
    path = batch_dir / f"{batch_id}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    (batch_dir / "latest.json").write_text(
        json.dumps(
            {"batch_id": batch_id, "path": str(path), "kind": "step3"}, indent=2
        ),
        encoding="utf-8",
    )
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
        raise FileNotFoundError("No latest step3 manifest — run submit first")
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    return json.loads(Path(latest["path"]).read_text(encoding="utf-8"))


def _estimate_cost(chunks: List[Step3Chunk], model: str) -> Dict[str, Any]:
    n_words = sum(len(c.words) for c in chunks)
    est_in = sum(200 + len(c.words) * 380 for c in chunks)
    est_out = sum(150 + len(c.words) * 280 for c in chunks)
    usage = TokenUsage(input_tokens=est_in, output_tokens=est_out)
    return {
        "words": n_words,
        "requests": len(chunks),
        "est_input_tokens": est_in,
        "est_output_tokens": est_out,
        "est_cost_standard": cost_usd(model, usage, batch=False),
        "est_cost_batch": cost_usd(model, usage, batch=True),
        "est_per_word_batch": (
            cost_usd(model, usage, batch=True) / n_words if n_words else 0
        ),
    }


def _chunk_request(chunk: Step3Chunk, *, model: str) -> Dict[str, Any]:
    fam = chunk.words[0].pack_family if chunk.words else "gre"
    return {
        "custom_id": chunk.custom_id,
        "params": {
            "model": model,
            "max_tokens": max_tokens_step3_chunk(len(chunk.words)),
            "temperature": 0.35,
            "messages": [
                {
                    "role": "user",
                    "content": build_multi_step3_prompt(chunk.words, pack_family=fam),
                }
            ],
        },
    }


def _word_manifest(w: Step3Word) -> Dict[str, Any]:
    return {
        "lexeme_id": str(w.lexeme_id),
        "sense_id": str(w.sense_id),
        "display_word": w.display_word,
        "pos": w.pos,
        "pack_id": w.pack_id,
        "pack_family": w.pack_family,
        "definition_en": w.definition_en,
        "example": w.example,
        "vi_gloss": w.vi_gloss,
    }


def _word_from_manifest(wm: Dict[str, Any]) -> Step3Word:
    return Step3Word(
        lexeme_id=uuid.UUID(wm["lexeme_id"]),
        sense_id=uuid.UUID(wm["sense_id"]),
        display_word=wm["display_word"],
        pos=wm.get("pos") or "word",
        pack_id=wm["pack_id"],
        pack_family=wm.get("pack_family") or "gre",
        definition_en=wm.get("definition_en") or wm["display_word"],
        example=wm.get("example") or "",
        vi_gloss=wm.get("vi_gloss") or "",
    )


def _message_text(msg: Any) -> str:
    parts: List[str] = []
    for block in getattr(msg, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "\n".join(parts).strip()


async def cmd_submit(args: argparse.Namespace) -> None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY missing")

    model = args.model or settings.anthropic_model
    words: List[Step3Word] = []
    async for repo in pg_session():
        words = await fetch_words_for_step3(
            repo.s,
            pack_id=args.pack_id,
            limit=args.limit,
            skip_existing=not args.force,
        )

    if not words:
        logger.warning("No words to process")
        return

    chunks = chunk_step3_words(words, max(1, args.words_per_request))
    est = _estimate_cost(chunks, model)
    print(
        f"Pack {args.pack_id} | words={est['words']} | requests={est['requests']} "
        f"({args.words_per_request}/req)"
    )
    print(
        f"Est. tokens in≈{est['est_input_tokens']:,} out≈{est['est_output_tokens']:,} | "
        f"batch ~{fmt_cost(est['est_cost_batch'])} ({fmt_cost(est['est_per_word_batch'])}/word)"
    )

    requests = [_chunk_request(c, model=model) for c in chunks]
    if args.dry_run:
        print(f"[DRY RUN] Would submit {len(requests)} requests")
        print(
            build_multi_step3_prompt(
                chunks[0].words, pack_family=chunks[0].words[0].pack_family
            )[:700],
            "...",
        )
        return

    from anthropic import Anthropic

    batch = Anthropic(api_key=settings.anthropic_api_key).messages.batches.create(
        requests=requests
    )
    manifest = {
        "kind": "step3",
        "batch_id": batch.id,
        "model": model,
        "pack_id": args.pack_id,
        "words_per_request": args.words_per_request,
        "word_count": len(words),
        "request_count": len(chunks),
        "created_at": _utc_now(),
        "processing_status": batch.processing_status,
        "estimate": est,
        "chunks": [
            {"custom_id": c.custom_id, "words": [_word_manifest(w) for w in c.words]}
            for c in chunks
        ],
    }
    path = _save_manifest(manifest)
    print(f"\nBatch submitted: {batch.id}")
    print(f"Manifest: {path}")


def cmd_status(args: argparse.Namespace) -> None:
    settings = get_settings()
    manifest = _load_manifest(args.batch_id)
    from anthropic import Anthropic

    batch = Anthropic(api_key=settings.anthropic_api_key).messages.batches.retrieve(
        manifest["batch_id"]
    )
    counts = getattr(batch, "request_counts", None)
    print(f"Batch: {batch.id} | kind=step3 | pack={manifest.get('pack_id')}")
    print(f"Status: {batch.processing_status}")
    if counts:
        print(
            f"processing={getattr(counts,'processing',0)} succeeded={getattr(counts,'succeeded',0)} "
            f"errored={getattr(counts,'errored',0)}"
        )
    if batch.processing_status == "ended":
        print("Ready: python -m aiforen.scripts.vocab.batch_llm_step3 import")


async def cmd_import(args: argparse.Namespace) -> None:
    settings = get_settings()
    manifest = _load_manifest(args.batch_id)
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    batch = client.messages.batches.retrieve(manifest["batch_id"])
    if batch.processing_status != "ended":
        raise RuntimeError(f"Batch not finished: {batch.processing_status}")

    chunk_by_id = {c["custom_id"]: c for c in manifest["chunks"]}
    stats = {"ok": 0, "warn": 0, "err": 0}
    usage = TokenUsage()

    async for repo in pg_session():
        for line in client.messages.batches.results(manifest["batch_id"]):
            cid = line.custom_id
            meta = chunk_by_id.get(cid)
            if not meta:
                stats["err"] += 1
                continue
            if line.result.type != "succeeded":
                logger.error("{} → {}", cid, line.result.type)
                stats["err"] += 1
                continue

            usage.add(usage_from_response(line.result.message))
            raw = _message_text(line.result.message)
            samples = [_word_from_manifest(wm) for wm in meta["words"]]
            try:
                by_word = parse_multi_step3_response(raw, samples)
            except Exception as exc:
                logger.error("{} parse: {}", cid, exc)
                stats["err"] += 1
                continue

            for w in samples:
                item = by_word.get(w.display_word.lower()) or {}
                ok, issues = validate_step3_item(item, w)
                if not ok and not args.allow_invalid:
                    logger.warning("{} {}: {}", cid, w.display_word, issues)
                    stats["warn"] += 1
                    continue
                trans = str(item.get("vi_translate_prompt", "")).strip()
                topic = str(item.get("topic_prompt", "")).strip()
                await repo.patch_primary_sense_gloss(
                    w.lexeme_id,
                    vi_gloss=w.vi_gloss,
                    vi_translate_prompt=trans,
                    topic_prompt=topic,
                )
                stats["ok"] += 1

        await repo.s.commit()

    cost = cost_usd(manifest["model"], usage, batch=True)
    print(
        f"\nImport done | ok={stats['ok']} warn={stats['warn']} err={stats['err']} | "
        f"cost ~{fmt_cost(cost)} (×{BATCH_DISCOUNT})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch Step 3 prompts (vi_translate + topic)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("submit")
    p.add_argument("--pack-id", default="pack_gre")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--words-per-request", type=int, default=5)
    p.add_argument(
        "--force", action="store_true", help="Regenerate even if not template"
    )
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=lambda a: run_async(cmd_submit(a)))

    p = sub.add_parser("status")
    p.add_argument("--batch-id", default=None)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("import")
    p.add_argument("--batch-id", default=None)
    p.add_argument("--allow-invalid", action="store_true")
    p.set_defaults(func=lambda a: run_async(cmd_import(a)))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
