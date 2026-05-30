"""Official open-licensed vocabulary list URLs (CC BY-SA / public domain)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
RAW_DIR = DATA_DIR / "raw"


@dataclass(frozen=True)
class SourceFile:
    name: str
    url: str
    filename: str
    license_id: str = "CC-BY-SA-4.0"

    @property
    def local_path(self) -> Path:
        return RAW_DIR / self.filename


SOURCES: tuple[SourceFile, ...] = (
    SourceFile(
        name="NGSL",
        url="https://www.newgeneralservicelist.com/s/NGSL_12_stats.csv",
        filename="ngsl_12_stats.csv",
    ),
    SourceFile(
        name="NGSL",
        url="https://www.newgeneralservicelist.com/s/NGSL_12_lemmatized_for_research.csv",
        filename="ngsl_12_lemmatized_research.csv",
    ),
    SourceFile(
        name="NAWL",
        url="https://www.newgeneralservicelist.com/s/NAWL_12_lemmatized_for_research.csv",
        filename="nawl_12_lemmatized_research.csv",
    ),
)
