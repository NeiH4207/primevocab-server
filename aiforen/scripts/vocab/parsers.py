"""Parse downloaded NGSL / NAWL CSV files."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple


@dataclass
class LemmaRow:
    lemma: str
    pos: str = "noun"
    frequency_rank: Optional[int] = None
    sfi: Optional[float] = None
    word_forms: List[str] = field(default_factory=list)
    source: str = "NGSL"
    sublist: Optional[int] = None
    definition_en: Optional[str] = None


def _guess_pos(lemma: str, forms: List[str]) -> str:
    if forms:
        f0 = forms[0].lower()
        if f0.endswith("ly"):
            return "adv"
        if f0.endswith("ing") or f0.endswith("ed") or f0.endswith("es"):
            return "verb"
        if f0.endswith("tion") or f0.endswith("ment") or f0.endswith("ness"):
            return "noun"
        if f0.endswith("ive") or f0.endswith("ous") or f0.endswith("al"):
            return "adj"
    if lemma.endswith("ly"):
        return "adv"
    if lemma in ("the", "a", "an", "of", "to", "in", "on", "at", "for", "with"):
        return "det" if lemma in ("the", "a", "an") else "prep"
    return "noun"


def _skip_function_word(lemma: str, pos: str) -> bool:
    if pos in ("det", "prep", "conj", "pron"):
        return True
    if lemma in {
        "the",
        "a",
        "an",
        "be",
        "and",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "as",
        "by",
        "or",
        "if",
        "but",
        "not",
        "no",
        "so",
        "than",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "he",
        "she",
        "they",
        "we",
        "you",
        "i",
        "my",
        "your",
        "his",
        "her",
        "their",
        "our",
    }:
        return True
    return False


def parse_ngsl_stats(path: Path) -> Iterator[LemmaRow]:
    """NGSL 1.2 stats: Lemma,SFI Rank,SFI,Adjusted Frequency per Million."""

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lemma = (row.get("Lemma") or row.get("lemma") or "").strip().lower()
            if not lemma or lemma.startswith("#"):
                continue
            try:
                rank = int(row.get("SFI Rank") or row.get("rank") or 0)
            except ValueError:
                rank = 0
            sfi_raw = row.get("SFI") or ""
            try:
                sfi = float(sfi_raw) if sfi_raw else None
            except ValueError:
                sfi = None
            pos = _guess_pos(lemma, [])
            if _skip_function_word(lemma, pos):
                continue
            yield LemmaRow(
                lemma=lemma,
                pos=pos,
                frequency_rank=rank,
                sfi=sfi,
                source="NGSL",
            )


def _read_text_lines(path: Path) -> List[str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").splitlines()


def parse_lemmatized_research(path: Path, *, source: str) -> Dict[str, List[str]]:
    """Lemma,family forms — returns lemma -> inflected forms."""

    forms_map: Dict[str, List[str]] = {}
    for line in _read_text_lines(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",", 1)
        lemma = parts[0].strip().lower()
        if not lemma:
            continue
        extra = parts[1].split(",") if len(parts) > 1 else []
        forms_map[lemma] = [x.strip().lower() for x in extra if x.strip()]
    return forms_map


def parse_nawl_research(path: Path) -> Iterator[LemmaRow]:
    forms_map = parse_lemmatized_research(path, source="NAWL")
    for lemma, forms in forms_map.items():
        pos = _guess_pos(lemma, forms)
        if _skip_function_word(lemma, pos):
            continue
        yield LemmaRow(
            lemma=lemma,
            pos=pos,
            word_forms=forms,
            source="NAWL",
        )


def merge_forms_into_rows(
    rows: List[LemmaRow], forms_map: Dict[str, List[str]]
) -> List[LemmaRow]:
    out: List[LemmaRow] = []
    for row in rows:
        forms = forms_map.get(row.lemma, [])
        pos = _guess_pos(row.lemma, forms) if forms else row.pos
        out.append(
            LemmaRow(
                lemma=row.lemma,
                pos=pos,
                frequency_rank=row.frequency_rank,
                sfi=row.sfi,
                word_forms=forms,
                source=row.source,
                sublist=row.sublist,
                definition_en=row.definition_en,
            )
        )
    return out


def band_from_ngsl_rank(rank: int) -> Tuple[float, float]:
    if rank <= 300:
        return 4.0, 5.0
    if rank <= 800:
        return 5.0, 6.0
    if rank <= 1500:
        return 6.0, 7.0
    if rank <= 2200:
        return 7.0, 8.0
    return 8.0, 9.0
