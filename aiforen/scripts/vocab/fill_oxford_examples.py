"""Deprecated: use backfill_definitions --oxford-only (dictionaryapi.dev) instead."""

from __future__ import annotations

import sys

if __name__ == "__main__":
    print(
        "fill_oxford_examples is deprecated.\n"
        "Run: python -m aiforen.scripts.vocab.backfill_definitions --oxford-only",
        file=sys.stderr,
    )
    sys.exit(1)
