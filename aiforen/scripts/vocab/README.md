# Vocab data pipeline

## Pack model

| `pack_family` | UI | Example |
|---------------|-----|---------|
| `band` | **One pack per IELTS band** (4–9) | `pack_band_6` |
| `cefr` | **Oxford 5000 / CEFR** (A1–C1) | `pack_oxford_b1` |
| `gre` | One GRE pack | `pack_gre` |
| `writing` | *(future)* Task 1 / Task 2 — separate from band | `pack_writing_task1_…` |

`stat_labels` on each word (`education`, `health`, …) are **for analytics only** — not separate packs in the library.

## 1. Crawl open word lists (NGSL + NAWL, CC BY-SA)

```bash
# Download official CSVs → data/raw/
python -m aiforen.scripts.vocab.download_sources

# Download + import ~2800 NGSL + ~950 NAWL lemmas into Postgres
python -m aiforen.scripts.vocab.crawl_all
```

Raw files land in `aiforen/scripts/vocab/data/raw/`.

## 2. Fill packs to target counts (priority — words in DB first)

```bash
# All band + GRE packs: pack_items only (fast; vi_gloss/prompts/MCQ = None until enrich)
python -m aiforen.scripts.vocab.fill_packs --all

python -m aiforen.scripts.vocab.fill_packs pack_gre
python -m aiforen.scripts.vocab.fill_packs pack_band_6 --stub-sense  # optional slow path
```

Targets per pack: `pack_specs.PACK_TARGET_GOALS` (capped by NGSL/NAWL pool size).

Do **not** run `build_band_packs` after fill — it resets packs to ~10 core words.

## 3. Consolidate to unified band packs

```bash
python -m aiforen.scripts.vocab.consolidate_packs
```

## Pack membership (overlap / `source_packs`)

`vocab_full_table.json` rows list every pack in **`source_packs`**. Importers write **one `vocab_pack_items` row per (pack, lexeme)** so the frontend library matches membership counts (not only primary `pack_id`).

```bash
# Rebuild pack items only (fast)
DATABASE_URL=postgresql://... python -m aiforen.scripts.vocab.import_vocab_storage_bulk --packs-only

# Compare JSON membership vs DB
DATABASE_URL=postgresql://... python -m aiforen.scripts.vocab.verify_pack_membership
```

## 4. Backfill definition + phonetic + example (dictionaryapi.dev)

```bash
python -m aiforen.scripts.vocab.backfill_definitions pack_band_6 --batch-size 50 --sleep 0.25
python -m aiforen.scripts.vocab.backfill_definitions --all
```

Logs progress per batch (`ok` / `miss` / `skip` / `err`). Skips words that already have a real definition unless `--force`.

Fill **only** missing example / IPA (keeps `definition_en` and VI). Scans the pack once in DB, then calls the API **only for gap words** (~100–150 per band), not every pack item.

```bash
python -m aiforen.scripts.vocab.backfill_definitions --bands-only --gaps-only --sleep 0.25
python -m aiforen.scripts.vocab.backfill_definitions pack_band_6 --gaps-only
python -m aiforen.scripts.vocab.fill_gre_gaps   # GRE typos/phrases + manual IPA/example
python -m aiforen.scripts.vocab.backfill_definitions --oxford-only --batch-size 50 --sleep 0.25
```

## 5. Complete / enrich (later)

```bash
python -m aiforen.scripts.vocab.complete_pack pack_band_6
python -m aiforen.scripts.vocab.complete_pack --all   # all BAND_PACKS only

# Fast fill for UI (transipy VI gloss + template MCQ; no dictionary/LLM)
python -m aiforen.scripts.vocab.complete_pack pack_gre --skip-select --fast

# After backfill_definitions: VI gloss only (does not overwrite EN def/example)
python -m aiforen.scripts.vocab.complete_pack pack_band_6 --skip-select --gloss-only

# Re-enrich words already in pack (skip re-selection)
python -m aiforen.scripts.vocab.complete_pack pack_band7_argument --skip-select
```

- **VI meaning (`vi_gloss`)**: [transipy](https://github.com/NeiH4207/transipy) batch translate (`TRANSIPY_CHUNK_SIZE`, default 8).
- **Translate/topic prompts**: template now; LLM backfill later via `backfill_llm_prompts.py`.
- **Slow path** (dictionary + optional Anthropic): omit `--fast`.

## LLM MCQ (understanding + Batch API)

Trial (sync, prints cost):

```bash
docker compose exec api python -m aiforen.scripts.vocab.trial_llm_mcq -n 5 --understanding
```

**Batch + multi-word** (~5 words per API request, **−50%** Anthropic Batch pricing):

```bash
# 1) Preview
docker compose exec api python -m aiforen.scripts.vocab.batch_llm_mcq submit \
  --pack-id pack_band_7 --words-per-request 5 --limit 50 --dry-run

# 2) Submit job (async, up to ~24h; usually minutes)
docker compose exec api python -m aiforen.scripts.vocab.batch_llm_mcq submit \
  --pack-id pack_band_7 --words-per-request 5

# 3) Poll
docker compose exec api python -m aiforen.scripts.vocab.batch_llm_mcq status

# 4) Import into vocab_questions (status=generated; use --approve-generated when reviewed)
docker compose exec api python -m aiforen.scripts.vocab.batch_llm_mcq import
```

Manifests: `aiforen/scripts/vocab/data/mcq_batches/`. Skips lexemes that already have `meaning_mcq` with `generator_meta.source=llm_mcq_batch` unless `--force`.

Rough cost (Haiku, batch, 5 words/request): **~$0.0016/word** → full ~12.5k words **~$20**.

## Band 4–9 LLM backfill (Step 3 + MCQ, OpenAI gpt-5.4-nano)

Tiered MCQ difficulty: band 4–6 `easy`, band 7 `standard`, band 8–9 `hard` (see `infer_mcq_difficulty`).

```bash
# 1) Trial (no DB write)
docker compose exec api python -m aiforen.scripts.vocab.backfill_llm_bands trial

# 2) Full backfill (step3 then mcq, all band packs)
docker compose exec api python -m aiforen.scripts.vocab.backfill_llm_bands all --concurrency 8

# One pack / phase only
docker compose exec api python -m aiforen.scripts.vocab.backfill_llm_bands step3 --pack-id pack_band_6
docker compose exec api python -m aiforen.scripts.vocab.backfill_llm_bands mcq --pack-id pack_band_6 --approve
```

Rough cost: ~$0.0006/word MCQ + ~$0.0004/word Step3 → **~$6–8** for all ~6.3k band words.

## Step 3 prompts (vi_translate + topic) — batch

Trial one word:

```bash
docker compose exec api python -m aiforen.scripts.vocab.trial_llm_step3 --word abate
```

Full pack (GRE default, 5 words/request, −50% batch):

```bash
docker compose exec api python -m aiforen.scripts.vocab.batch_llm_step3 submit --pack-id pack_gre
docker compose exec api python -m aiforen.scripts.vocab.batch_llm_step3 status
docker compose exec api python -m aiforen.scripts.vocab.batch_llm_step3 import
```

Manifests: `/tmp/aiforen-step3-batches/` in the API container. Skips words that still have the old template (`Dịch sang tiếng Anh…` / `IELTS`); use `--force` to regenerate all.

## Oxford 5000 / CEFR packs (A1–C1)

Separate from IELTS band packs — does not replace `pack_band_*` or `pack_gre`.

```bash
# Extract PDF → CSV (once)
python -m aiforen.scripts.vocab.extract_oxford_pdf

# Import CSV → pack_oxford_a1 … pack_oxford_c1 (IPA + vi_gloss from book)
python -m aiforen.scripts.vocab.import_oxford_csv
```

## Seed safety (important)

- **`docker compose up api` does NOT run seed** — will not wipe filled packs.
- First-time DB: `docker compose run --rm seed`
- **Never** set `ALLOW_VOCAB_WIPE=1` unless you intend to delete all vocab senses/questions/packs.

## Import from `vocab_storage/` (7563 words + ~48k MCQ)

Curated JSON lives in the repo sibling folder `vocab_storage/` (`vocab_full_table.json`, `quiz_*_vocab.json`).

```bash
cd primevocab-server
# Local (Postgres up, env from .env or docker)
python -m aiforen.scripts.vocab.import_vocab_storage

# Production (Railway DATABASE_PUBLIC_URL)
export DATABASE_URL="$DATABASE_PUBLIC_URL"
python -m aiforen.scripts.vocab.import_vocab_storage

# Preview counts only
python -m aiforen.scripts.vocab.import_vocab_storage --dry-run
```

This updates lexemes/senses in place (matched by lemma+pos), rebuilds `vocab_pack_items` per pack, and imports MCQ rows into `vocab_questions` (skips rewrite/free-text tasks). Takes several minutes on a full run.

## 3. Docker

```bash
cd py-server
docker compose run --rm migrate          # includes 0003 pack status columns
docker compose run --rm seed             # first empty DB only; skips if packs filled
docker compose run --rm seed python -m aiforen.scripts.vocab.crawl_all
docker compose run --rm seed python -m aiforen.scripts.vocab.complete_pack pack_band4_daily
docker compose exec api python -m aiforen.scripts.vocab.import_oxford_csv
```

## Pack workflow statuses

`draft` → `selecting` → `filled` (words in pack) → `enriching` → `complete` (`vocab_packs.content_status`).
