"""Map study packs to vocab quiz matrix track_id (vocab_questions.track_id)."""

from __future__ import annotations

from typing import Optional

# pack_id → track_id used in vocab_questions and quiz_*_vocab.json imports
PACK_TO_TRACK: dict[str, str] = {
    "pack_oxford_a1": "cefr:A1",
    "pack_oxford_a2": "cefr:A2",
    "pack_oxford_b1": "cefr:B1",
    "pack_oxford_b2": "cefr:B2",
    "pack_oxford_c1": "cefr:C1",
    "pack_band_4": "ielts:core",
    "pack_band_5": "ielts:core",
    "pack_band_6": "ielts:core",
    "pack_band_7": "ielts:core",
    "pack_band_8": "ielts:core",
    "pack_band_9": "ielts:core",
    "pack_gre": "gre:core",
}


def track_id_for_pack(pack_id: Optional[str]) -> Optional[str]:
    if not pack_id:
        return None
    return PACK_TO_TRACK.get(str(pack_id).strip())


def level_code_for_pack(pack_id: Optional[str]) -> Optional[str]:
    track = track_id_for_pack(pack_id)
    if not track:
        return None
    if track.startswith("cefr:"):
        return track.split(":", 1)[1]
    if track.startswith("ielts"):
        return "IELTS"
    if track.startswith("gre"):
        return "GRE"
    return None
