"""Extract vocabulary tables from '5000 từ vựng Oxford thông dụng - Thanh Tuấn.pdf'.

Output CSV columns: stt, lemma, english_raw, pos, phonetic, vi_gloss, level, page

Usage:
  python -m aiforen.scripts.vocab.extract_oxford_pdf
  python -m aiforen.scripts.vocab.extract_oxford_pdf --pdf /path/to/file.pdf --out data/out.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

import pdfplumber

LEVEL_RE = re.compile(r"TRÌNH ĐỘ\s+(A1|A2|B1|B2|C1)", re.I)
STT_RE = re.compile(r"^\d+\.?$")

DEFAULT_PDF = (
    Path(__file__).resolve().parents[4]
    / "5000 từ vựng Oxford thông dụng - Thanh Tuấn.pdf"
)
DEFAULT_OUT = Path(__file__).resolve().parents[3] / "data" / "oxford_5000_thanhtuan.csv"


def parse_english(cell: str) -> tuple[str, str]:
    if not cell:
        return "", ""
    cell = cell.strip()
    m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", cell)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(
        r"^(.+?)\s+((?:n|v|adj|adv|prep|conj|det|pron|modal|exclam|number|interj)[\w.,\s]*)$",
        cell,
        re.I,
    )
    if m:
        return m.group(1).strip().rstrip(","), m.group(2).strip()
    return cell, ""


def extract(pdf_path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_level = ""

    with pdfplumber.open(pdf_path) as doc:
        for pi, page in enumerate(doc.pages):
            text = page.extract_text() or ""
            for m in LEVEL_RE.finditer(text):
                current_level = m.group(1).upper()

            for table in page.extract_tables() or []:
                if not table or len(table[0]) < 4:
                    continue
                start = 1 if table[0][0] and "stt" in str(table[0][0]).lower() else 0
                for raw in table[start:]:
                    if not raw or len(raw) < 4:
                        continue
                    stt, en, ipa, vi = [
                        str(c or "").strip().replace("\n", " ") for c in raw[:4]
                    ]
                    if not en or "tiếng anh" in en.lower():
                        continue
                    if not STT_RE.match(stt.replace(" ", "")) and not re.match(
                        r"^\d+\.", stt
                    ):
                        continue
                    lemma, pos = parse_english(en)
                    rows.append(
                        {
                            "stt": stt.rstrip("."),
                            "lemma": lemma,
                            "english_raw": en,
                            "pos": pos,
                            "phonetic": ipa,
                            "vi_gloss": vi,
                            "level": current_level,
                            "page": str(pi + 1),
                        }
                    )

    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for r in rows:
        key = (r["lemma"].lower(), r["pos"], r["level"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Oxford 5000 PDF tables to CSV"
    )
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")

    rows = extract(args.pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "stt",
        "lemma",
        "english_raw",
        "pos",
        "phonetic",
        "vi_gloss",
        "level",
        "page",
    ]
    with args.out.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()
        csv.DictWriter(f, fieldnames=fields).writerows(rows)

    levels = Counter(r["level"] for r in rows)
    print(f"Wrote {len(rows)} rows → {args.out}")
    for lv in ("A1", "A2", "B1", "B2", "C1"):
        print(f"  {lv}: {levels.get(lv, 0)}")


if __name__ == "__main__":
    main()
