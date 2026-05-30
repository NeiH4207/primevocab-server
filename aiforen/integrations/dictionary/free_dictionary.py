"""Free Dictionary API (dictionaryapi.dev) — definitions, examples, phonetics."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import httpx

_API_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"


async def fetch_entry(word: str) -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_API_URL.format(word=word.lower()))
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data[0] if isinstance(data, list) and data else None
    except Exception:
        return None


def _pos_matches(mpos: str, pos_hint: str) -> bool:
    if not pos_hint or not mpos:
        return True
    return pos_hint in mpos or mpos in pos_hint


def pick_best_definition(
    entry: Dict[str, Any],
    pos_hint: str,
    *,
    min_len: int = 20,
) -> tuple[str, str, List[str]]:
    """Pick a full dictionary definition (>= min_len when possible), preferring POS match."""
    meanings = entry.get("meanings") or []
    pos_hint = (pos_hint or "").lower()
    ranked: List[Tuple[int, str, str, List[str]]] = []

    for meaning in meanings:
        mpos = (meaning.get("partOfSpeech") or "").lower()
        pos_ok = _pos_matches(mpos, pos_hint)
        for d in meaning.get("definitions") or []:
            defn = str(d.get("definition") or "").strip()
            if len(defn) < 8:
                continue
            example = str(d.get("example") or "").strip()
            syns = (d.get("synonyms") or [])[:4]
            score = (
                (2 if pos_ok else 0)
                + (1 if len(defn) >= min_len else 0)
                + (1 if example else 0),
                len(defn),
            )
            ranked.append((score, defn, example, syns))

    if ranked:
        ranked.sort(key=lambda x: x[0], reverse=True)
        _, defn, example, syns = ranked[0]
        if len(defn) >= min_len:
            return defn, example, syns
        # POS hint may be wrong (e.g. sale tagged verb) — take longest definition available.
        longest = max(ranked, key=lambda x: x[0][1])
        _, defn, example, syns = longest
        if len(defn) >= min_len:
            return defn, example, syns
    return pick_definition(entry, pos_hint)


def pick_definition(entry: Dict[str, Any], pos_hint: str) -> tuple[str, str, List[str]]:
    """Prefer a definition that includes an example sentence when available."""
    meanings = entry.get("meanings") or []
    pos_hint = (pos_hint or "").lower()

    best_pos_no_ex = ("", "", [])
    best_any_ex = ("", "", [])
    best_pos_with_ex = ("", "", [])

    for meaning in meanings:
        mpos = (meaning.get("partOfSpeech") or "").lower()
        pos_ok = _pos_matches(mpos, pos_hint)
        for d in meaning.get("definitions") or []:
            defn = str(d.get("definition") or "").strip()
            if len(defn) < 8:
                continue
            example = str(d.get("example") or "").strip()
            syns = (d.get("synonyms") or [])[:4]
            if example and pos_ok and not best_pos_with_ex[0]:
                best_pos_with_ex = (defn, example, syns)
            if example and not best_any_ex[0]:
                best_any_ex = (defn, example, syns)
            if pos_ok and not best_pos_no_ex[0]:
                best_pos_no_ex = (defn, example, syns)

    if best_pos_with_ex[0]:
        return best_pos_with_ex
    if best_any_ex[0]:
        return best_any_ex
    if best_pos_no_ex[0]:
        return best_pos_no_ex
    for meaning in meanings:
        for d in meaning.get("definitions") or []:
            defn = str(d.get("definition") or "").strip()
            if defn:
                return defn, str(d.get("example") or "").strip(), []
    return "", "", []


def pick_phonetic_audio(entry: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    phonetic = entry.get("phonetic")
    phonetics = entry.get("phonetics") or []
    if not phonetic:
        phonetic = next((p.get("text") for p in phonetics if p.get("text")), None)
    audio = next((p.get("audio") for p in phonetics if p.get("audio")), None)
    return (
        str(phonetic).strip() if phonetic else None,
        str(audio).strip() if audio else None,
    )
