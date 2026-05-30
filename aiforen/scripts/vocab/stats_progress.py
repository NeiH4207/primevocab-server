"""Print vocab pack enrichment progress — one detailed coverage table."""

from __future__ import annotations

import os

os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("PG_HOST", "127.0.0.1")
os.environ.setdefault("PG_PORT", "55432")

from sqlalchemy import text

from aiforen.scripts.vocab._common import pg_session, run_async

PACK_STATS_SQL = """
WITH pack_lex AS (
  SELECT
    p.pack_id,
    p.pack_family,
    i.lexeme_id,
    s.id AS sense_id,
    length(trim(coalesce(s.definition_en, ''))) >= 20 AS has_def,
    length(trim(coalesce(s.vi_gloss, ''))) > 2 AS has_vi,
    length(trim(coalesce(s.phonetic, ''))) > 1 AS has_ipa,
    length(trim(coalesce(s.audio_url, ''))) > 8 AS has_audio,
    length(trim(coalesce(s.ielts_example, s.gre_example, ''))) > 20 AS has_ex,
    (
      length(trim(coalesce(s.vi_translate_prompt, ''))) >= 20
      AND coalesce(s.vi_translate_prompt, '') NOT LIKE 'Dịch sang tiếng Anh (dùng từ%'
      AND coalesce(s.vi_translate_prompt, '') NOT LIKE '%Nhiều người cho rằng cuộc sống%'
    ) AS has_translate,
    (
      length(trim(coalesce(s.topic_prompt, ''))) >= 12
      AND coalesce(s.topic_prompt, '') NOT LIKE 'Dịch sang tiếng Anh (dùng từ%'
      AND coalesce(s.topic_prompt, '') NOT ILIKE '%ngữ cảnh IELTS%'
    ) AS has_topic,
    EXISTS (
      SELECT 1 FROM vocab_questions q
      WHERE q.lexeme_id = i.lexeme_id AND q.type = 'meaning_mcq'
    ) AS has_mcq,
    EXISTS (
      SELECT 1 FROM vocab_questions q
      WHERE q.lexeme_id = i.lexeme_id AND q.type = 'meaning_mcq'
        AND q.generator_meta->>'source' = 'llm_mcq_openai'
    ) AS has_mcq_oai,
    EXISTS (
      SELECT 1 FROM vocab_questions q
      WHERE q.lexeme_id = i.lexeme_id AND q.type = 'meaning_mcq'
        AND q.generator_meta->>'source' = 'llm_mcq_batch'
    ) AS has_mcq_batch,
    EXISTS (
      SELECT 1 FROM vocab_questions q
      WHERE q.lexeme_id = i.lexeme_id AND q.type = 'cloze'
    ) AS has_cloze,
    EXISTS (
      SELECT 1 FROM vocab_questions q
      WHERE q.lexeme_id = i.lexeme_id AND q.type = 'cloze'
        AND q.generator_meta->>'source' = 'llm_mcq_openai'
    ) AS has_cloze_oai,
    EXISTS (
      SELECT 1 FROM vocab_questions q
      WHERE q.lexeme_id = i.lexeme_id AND q.type = 'cloze'
        AND q.generator_meta->>'source' = 'llm_mcq_batch'
    ) AS has_cloze_batch,
    (SELECT count(*) FROM vocab_senses vs WHERE vs.lexeme_id = i.lexeme_id) AS sense_cnt
  FROM vocab_packs p
  JOIN vocab_pack_items i ON i.pack_id = p.pack_id
  JOIN vocab_senses s ON s.lexeme_id = i.lexeme_id AND s.sense_order = 1
  WHERE p.is_active = true
)
SELECT
  pack_id,
  pack_family,
  count(*) AS words,
  count(*) FILTER (WHERE sense_cnt >= 1) AS w_sense1,
  sum(sense_cnt) AS senses_total,
  count(*) FILTER (WHERE has_def) AS def,
  count(*) FILTER (WHERE has_vi) AS vi,
  count(*) FILTER (WHERE has_ipa) AS ipa,
  count(*) FILTER (WHERE has_audio) AS audio,
  count(*) FILTER (WHERE has_ex) AS ex,
  count(*) FILTER (WHERE has_translate) AS trans,
  count(*) FILTER (WHERE has_topic) AS topic,
  count(*) FILTER (WHERE has_mcq) AS mcq_any,
  count(*) FILTER (WHERE has_mcq_oai) AS mcq_oai,
  count(*) FILTER (WHERE has_mcq_batch) AS mcq_batch,
  count(*) FILTER (WHERE has_cloze) AS cloze_any,
  count(*) FILTER (WHERE has_cloze_oai) AS cloze_oai,
  count(*) FILTER (WHERE has_cloze_batch) AS cloze_batch
FROM pack_lex
GROUP BY pack_id, pack_family
ORDER BY pack_family, pack_id
"""


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


async def main() -> None:
    async for repo in pg_session():
        s = repo.s
        rows = (await s.execute(text(PACK_STATS_SQL))).all()

        def fmt_row(
            pid: str,
            words: int,
            senses_tot: int,
            def_n: int,
            vi: int,
            ipa: int,
            audio: int,
            ex: int,
            trans: int,
            topic: int,
            mcq_any: int,
            mcq_oai: int,
            mcq_batch: int,
            cloze_any: int,
            cloze_oai: int,
            cloze_batch: int,
        ) -> str:
            return (
                f"{pid:20} {words:5} {senses_tot:6} "
                f"{def_n:5}{pct(def_n, words):>5} "
                f"{vi:5}{pct(vi, words):>5} "
                f"{ipa:5}{pct(ipa, words):>5} "
                f"{audio:5}{pct(audio, words):>5} "
                f"{ex:5}{pct(ex, words):>5} "
                f"{trans:5}{pct(trans, words):>5} "
                f"{topic:5}{pct(topic, words):>5} "
                f"{mcq_any:5}{pct(mcq_any, words):>5} "
                f"{mcq_oai:5}{pct(mcq_oai, words):>5} "
                f"{mcq_batch:5}{pct(mcq_batch, words):>5} "
                f"{cloze_any:5}{pct(cloze_any, words):>5} "
                f"{cloze_oai:5}{pct(cloze_oai, words):>5} "
                f"{cloze_batch:5}{pct(cloze_batch, words):>5}"
            )

        hdr = (
            f"{'pack':20} {'words':>5} {'senses':>6} "
            f"{'def':>5}{'%':>5} {'vi':>5}{'%':>5} {'IPA':>5}{'%':>5} "
            f"{'audio':>5}{'%':>5} {'ex':>5}{'%':>5} "
            f"{'trans':>5}{'%':>5} {'topic':>5}{'%':>5} "
            f"{'MCQ':>5}{'%':>5} {'oai':>5}{'%':>5} {'batch':>5}{'%':>5} "
            f"{'clz':>5}{'%':>5} {'c·oai':>5}{'%':>5} {'c·bat':>5}{'%':>5}"
        )
        print("=" * len(hdr))
        print("VOCAB DB FILL PROGRESS (per pack, primary sense)")
        print("=" * len(hdr))
        print(hdr)
        print("-" * len(hdr))

        fam_acc: dict[str, list[int]] = {}
        grand = [0] * 15

        for row in rows:
            (
                pid,
                fam,
                words,
                _w_s1,
                senses_tot,
                def_n,
                vi,
                ipa,
                audio,
                ex,
                trans,
                topic,
                mcq_any,
                mcq_oai,
                mcq_batch,
                cloze_any,
                cloze_oai,
                cloze_batch,
            ) = row
            print(
                fmt_row(
                    pid,
                    words,
                    senses_tot,
                    def_n,
                    vi,
                    ipa,
                    audio,
                    ex,
                    trans,
                    topic,
                    mcq_any,
                    mcq_oai,
                    mcq_batch,
                    cloze_any,
                    cloze_oai,
                    cloze_batch,
                )
            )
            acc = fam_acc.setdefault(fam, [0] * 15)
            nums = [
                words,
                senses_tot,
                def_n,
                vi,
                ipa,
                audio,
                ex,
                trans,
                topic,
                mcq_any,
                mcq_oai,
                mcq_batch,
                cloze_any,
                cloze_oai,
                cloze_batch,
            ]
            for i, v in enumerate(nums):
                acc[i] += v
                grand[i] += v

        print("-" * len(hdr))
        for fam in sorted(fam_acc):
            a = fam_acc[fam]
            print(
                fmt_row(
                    f"{fam} Σ",
                    a[0],
                    a[1],
                    a[2],
                    a[3],
                    a[4],
                    a[5],
                    a[6],
                    a[7],
                    a[8],
                    a[9],
                    a[10],
                    a[11],
                    a[12],
                    a[13],
                    a[14],
                )
            )
        print(
            fmt_row(
                "ALL Σ",
                grand[0],
                grand[1],
                grand[2],
                grand[3],
                grand[4],
                grand[5],
                grand[6],
                grand[7],
                grand[8],
                grand[9],
                grand[10],
                grand[11],
                grand[12],
                grand[13],
                grand[14],
            )
        )

        print()
        print(
            "Legend: def=definition_en | vi=vi_gloss | translate/topic=non-template Step3 prompts"
        )
        print(
            "        MCQ·oai=llm_mcq_openai | MCQ·batch=llm_mcq_batch (Anthropic batch GRE)"
        )

        print()
        print("=" * 72)
        print("QUESTIONS BY source × status × type")
        print("=" * 72)
        for row in (
            await s.execute(
                text(
                    """
          SELECT coalesce(generator_meta->>'source','(none)'), status, type, count(*)
          FROM vocab_questions GROUP BY 1,2,3 ORDER BY 1,2,3
        """
                )
            )
        ).all():
            print(f"  {row[0]:22} {row[1]:12} {row[2]:14} {row[3]:6,}")

        # MCQ backfill in progress hint
        oai_band = (
            await s.execute(
                text(
                    """
          SELECT count(DISTINCT i.lexeme_id)
          FROM vocab_pack_items i
          JOIN vocab_packs p ON p.pack_id = i.pack_id AND p.pack_family = 'band'
          JOIN vocab_questions q ON q.lexeme_id = i.lexeme_id
            AND q.type = 'meaning_mcq' AND q.generator_meta->>'source' = 'llm_mcq_openai'
        """
                )
            )
        ).scalar()
        print()
        print(f"Band packs with llm_mcq_openai (meaning): {oai_band:,}")


if __name__ == "__main__":
    run_async(main())
