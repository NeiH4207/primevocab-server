"""Extract Cambridge reading passages from the Boost your vocabulary PDF.

Outputs ``aiforen/domain/vocab_coaching_readings_data.py`` with Day 1–12 passages.

Usage:
  python -m aiforen.scripts.vocab.extract_boost_vocab_readings
  python -m aiforen.scripts.vocab.extract_boost_vocab_readings --pdf /path/to/file.pdf
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

DEFAULT_PDF = (
    Path(__file__).resolve().parents[4]
    / "A&M IELTS - Cam 10- Boost your vocabulary.pdf"
)
DEFAULT_OUT = (
    Path(__file__).resolve().parents[2] / "domain" / "vocab_coaching_readings_data.py"
)

BOILER = re.compile(
    r"BOOST YOUR VOCABULARY|Tài liệu gốc|Biên tập|IELTS Family|"
    r"^\s*\d+\s*$|^TEST\s+\d|^Test\s+\d|^READING PASSAGE\s+\d",
    re.I,
)
READING_MARKER = re.compile(r"READING PASSAGE\s+\d", re.I)

READINGS_META: list[tuple[str, str, str]] = [
    (
        "cambridge10-test1-passage1",
        "The stepwells of India",
        "Cambridge IELTS 10 · Test 1 · Passage 1",
    ),
    (
        "cambridge10-test1-passage2",
        "European transport systems",
        "Cambridge IELTS 10 · Test 1 · Passage 2",
    ),
    (
        "cambridge10-test1-passage3",
        "Why are so few companies truly innovative?",
        "Cambridge IELTS 10 · Test 1 · Passage 3",
    ),
    (
        "cambridge10-test2-passage1",
        "Tea and the Industrial Revolution",
        "Cambridge IELTS 10 · Test 2 · Passage 1",
    ),
    (
        "cambridge10-test2-passage2",
        "Giftedness and intelligence",
        "Cambridge IELTS 10 · Test 2 · Passage 2",
    ),
    (
        "cambridge10-test2-passage3",
        "Museums of fine art",
        "Cambridge IELTS 10 · Test 2 · Passage 3",
    ),
    (
        "cambridge10-test3-passage1",
        "The history of travel",
        "Cambridge IELTS 10 · Test 3 · Passage 1",
    ),
    (
        "cambridge10-test3-passage2",
        "Why leaves turn red in the fall",
        "Cambridge IELTS 10 · Test 3 · Passage 2",
    ),
    (
        "cambridge10-test3-passage3",
        "Ancient Pacific voyagers",
        "Cambridge IELTS 10 · Test 3 · Passage 3",
    ),
    (
        "cambridge10-test4-passage1",
        "Wildfires in the western United States",
        "Cambridge IELTS 10 · Test 4 · Passage 1",
    ),
    (
        "cambridge10-test4-passage2",
        "Personality and temperament",
        "Cambridge IELTS 10 · Test 4 · Passage 2",
    ),
    (
        "cambridge10-test4-passage3",
        "Evolutionary throwbacks",
        "Cambridge IELTS 10 · Test 4 · Passage 3",
    ),
]


@dataclass
class ExtractedReading:
    day: int
    reading_id: str
    title: str
    source_label: str
    paragraphs: list[str]


def is_boiler(line: str) -> bool:
    return bool(BOILER.search(line.strip()))


def is_gloss_start(line: str) -> bool:
    s = line.strip()
    return bool(re.match(r"^[A-Za-z][A-Za-z'/\s-]{0,45}=\s*", s))


def is_gloss_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if re.match(r"^\(\d+\)$", s):
        return False
    if is_gloss_start(s):
        return True
    if s.endswith("…") and len(s) < 130:
        return True
    if len(s) < 55 and "=" in s:
        return True
    return False


def is_passage_resume(line: str) -> bool:
    s = line.strip()
    if len(s) >= 80:
        return True
    if len(s) >= 45 and re.match(r"^[A-Z(]", s) and not is_gloss_line(s):
        return True
    return False


def collect_passage_lines(page_texts: list[str]) -> list[str]:
    lines: list[str] = []
    gloss_mode = False
    for text in page_texts:
        for raw in text.split("\n"):
            s = raw.strip()
            if not s or is_boiler(s):
                gloss_mode = False
                continue
            if is_gloss_start(s) or is_gloss_line(s):
                gloss_mode = True
                continue
            if gloss_mode:
                if is_passage_resume(s):
                    gloss_mode = False
                else:
                    continue
            lines.append(s)
    return lines


def strip_section_prefix(text: str) -> str:
    return re.sub(r"^([A-G]|\(\d+\))\s+", "", text).strip()


def normalize_text(text: str) -> str:
    s = re.sub(r"\s+", " ", text).strip().replace("，", ", ")
    s = re.sub(r"\bLap it\b", "Lapita", s)
    s = re.sub(r"\btodays,\b", "today's", s)
    s = re.sub(r"\binrervisible\b", "intervisible", s)
    s = re.sub(r"\bselfregulatory\b", "self-regulatory", s)
    s = re.sub(r"\bobably\b", "probably", s)
    return s


def strip_embedded_title(title: str, paragraphs: list[str]) -> list[str]:
    if not paragraphs:
        return paragraphs
    first = paragraphs[0]
    if first.startswith(title):
        rest = first[len(title) :].strip()
        if rest:
            paragraphs[0] = rest
        else:
            paragraphs = paragraphs[1:]
    return paragraphs


def merge_wrapped(lines: list[str]) -> list[str]:
    out: list[str] = []
    buf = ""
    for ln in lines:
        if re.match(r"^\(\d+\)$", ln):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
            continue
        if re.match(r"^[A-G]\s", ln):
            if buf.strip():
                out.append(buf.strip())
            buf = ln
            continue
        if not buf:
            buf = ln
            continue
        if buf.endswith(("-", "–")):
            buf = buf[:-1] + ln
        elif ln and ln[0].islower():
            buf += " " + ln
        else:
            out.append(buf.strip())
            buf = ln
    if buf.strip():
        out.append(buf.strip())
    return out


def split_letter_paragraphs(blocks: list[str]) -> tuple[str, list[str]]:
    paras: list[str] = []
    title_lines: list[str] = []
    for block in blocks:
        match = re.match(r"^([A-G])\s+(.*)$", block, re.S)
        if match:
            paras.append(match.group(2).strip())
        elif not paras:
            title_lines.append(block)
        else:
            paras[-1] += " " + block
    title = re.sub(r"\s+", " ", " ".join(title_lines)).strip()
    return title, paras


def merge_short_paragraphs(
    paragraphs: list[str], *, max_count: int = 14, min_len: int = 200
) -> list[str]:
    paras = list(paragraphs)
    while len(paras) > max_count and len(paras) > 1:
        idx = min(range(len(paras)), key=lambda i: len(paras[i]))
        if idx == len(paras) - 1:
            paras[idx - 1] = paras[idx - 1] + " " + paras[idx]
            del paras[idx]
        else:
            paras[idx] = paras[idx] + " " + paras[idx + 1]
            del paras[idx + 1]
    while len(paras) > 1:
        idx = next((i for i, p in enumerate(paras) if len(p) < min_len), None)
        if idx is None:
            break
        if idx == len(paras) - 1:
            paras[idx - 1] += " " + paras[idx]
            del paras[idx]
        else:
            paras[idx] = paras[idx] + " " + paras[idx + 1]
            del paras[idx + 1]
    return paras


def extract_readings(pdf_path: Path) -> list[ExtractedReading]:
    doc = fitz.open(pdf_path)
    pages = [doc.load_page(i).get_text("text") for i in range(doc.page_count)]
    starts = [i for i, text in enumerate(pages) if READING_MARKER.search(text)]

    readings: list[ExtractedReading] = []
    for idx, start_page in enumerate(starts):
        end_page = starts[idx + 1] if idx + 1 < len(starts) else 49
        blocks = merge_wrapped(collect_passage_lines(pages[start_page:end_page]))
        has_letters = any(re.match(r"^[A-G]\s", block) for block in blocks)

        if has_letters:
            title, paragraphs = split_letter_paragraphs(blocks)
        elif idx == 8:
            title = "Ancient voyagers who settled the far-flung islands of the Pacific Ocean"
            paragraphs = blocks
        else:
            title_lines: list[str] = []
            paragraphs = []
            for block in blocks:
                if (
                    not paragraphs
                    and len(block) < 170
                    and (not block.endswith(".") or block.endswith("?"))
                ):
                    title_lines.append(block)
                else:
                    paragraphs.append(block)
            if not paragraphs:
                paragraphs = blocks
            title = " ".join(title_lines)

        reading_id, default_title, source_label = READINGS_META[idx]
        title = normalize_text(title)
        if (
            not title
            or len(title) > 220
            or "Cambridge" in title
            or title.endswith(" in")
            or title.endswith(" of t")
        ):
            title = default_title

        paragraphs = [
            normalize_text(strip_section_prefix(p))
            for p in paragraphs
            if len(normalize_text(strip_section_prefix(p))) >= 30
        ]
        paragraphs = merge_short_paragraphs(paragraphs)
        if idx == 8:
            paragraphs = strip_embedded_title(title, paragraphs)

        readings.append(
            ExtractedReading(
                day=idx + 1,
                reading_id=reading_id,
                title=title,
                source_label=source_label,
                paragraphs=paragraphs,
            )
        )
    return readings


def _py_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_python_module(readings: list[ExtractedReading]) -> str:
    from aiforen.domain.vocab_coaching_reading import STEPWELLS_PARAGRAPHS

    lines = [
        '"""Cambridge IELTS 10 reading passages for vocab coaching Days 1–12.',
        "",
        "Auto-generated by ``python -m aiforen.scripts.vocab.extract_boost_vocab_readings``.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any, Dict, List, TypedDict",
        "",
        "",
        "class ReadingPassageSeed(TypedDict):",
        "    day: int",
        "    id: str",
        "    title: str",
        "    source_label: str",
        "    estimated_minutes: int",
        "    paragraphs: List[str]",
        "",
        "",
        "READING_DAY_COUNT = 12",
        "",
        "",
        "def reading_day_index(day_number: int) -> int:",
        '    """Map plan day numbers onto the 12 seeded readings (cycles after Day 12)."""',
        "    if day_number < 1:",
        "        return 1",
        "    return ((day_number - 1) % READING_DAY_COUNT) + 1",
        "",
        "",
        "READINGS_BY_DAY: Dict[int, ReadingPassageSeed] = {",
    ]

    for reading in readings:
        paragraphs = reading.paragraphs
        if reading.day == 1:
            paragraphs = list(STEPWELLS_PARAGRAPHS)
            title = "The stepwells of India"
        else:
            title = reading.title

        lines.append(f"    {reading.day}: {{")
        lines.append(f'        "day": {reading.day},')
        lines.append(f'        "id": {_py_string(reading.reading_id)},')
        lines.append(f'        "title": {_py_string(title)},')
        lines.append(f'        "source_label": {_py_string(reading.source_label)},')
        lines.append('        "estimated_minutes": 8,')
        lines.append('        "paragraphs": [')
        for para in paragraphs:
            lines.append(f"            {_py_string(para)},")
        lines.append("        ],")
        lines.append("    },")

    lines.extend(
        [
            "}",
            "",
            "",
            "def get_reading_seed(day_number: int) -> ReadingPassageSeed:",
            "    return READINGS_BY_DAY[reading_day_index(day_number)]",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--json", type=Path, default=None, help="Optional JSON dump")
    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"PDF not found: {args.pdf}")

    readings = extract_readings(args.pdf)
    if len(readings) != 12:
        raise SystemExit(f"Expected 12 readings, got {len(readings)}")

    module_source = render_python_module(readings)
    args.out.write_text(module_source, encoding="utf-8")
    print(f"Wrote {args.out} ({len(readings)} readings)")

    if args.json:
        payload = [
            {
                "day": r.day,
                "id": r.reading_id,
                "title": r.title,
                "source_label": r.source_label,
                "paragraphs": r.paragraphs,
            }
            for r in readings
        ]
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
