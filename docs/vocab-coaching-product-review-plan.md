# Vocab Coaching — Product Review & Improvement Plan

**Owner:** Product / Engineering  
**Date:** 2026-06-05  
**Scope:** FE `vocab-coaching` + BE `vocab_coaching_*` + Reading Coach  
**Status:** Sprint 1 implemented in this session (see §8)

---

## 1. Executive summary

Vocab Coaching is a **3-step daily loop** (Recall → Reading Challenge → Vocab Focus) backed by Postgres plans/workouts and a **Reading Coach** side panel (LLM + Postgres cache). Core learning flow works; main gaps are **request duplication**, **silent failures**, **locale inconsistency**, and **partial i18n**.

| Area | Grade | Notes |
|------|-------|-------|
| Core day flow | B+ | Step gating, workspace persist, focus adaptation solid |
| Reading Coach | B | Cache helps; FE debounce + dedup needed (fixed Sprint 1) |
| UI polish | B- | Mobile stack OK; some EN-only strings |
| Performance | B- | Duplicate helper-note calls, `flushEvents` before each note |
| Reliability | B | Silent catch on coach errors (fixed Sprint 1) |

---

## 2. User journey

```
Landing /vocab-coaching
  → Auth gate
  → Quick vocab check (once)
  → 31-day timeline
  → Open Day N
      → Recall (yesterday words)
      → Reading (passage + Reading Coach + comprehension Qs)
      → Vocab Focus (adaptive 12–20 words)
  → Complete day → notes + next preview
```

**Friction points**
- Reading Coach empty state requires double-click discovery (acceptable; could add 1-line animated hint).
- Mobile: coach panel below passage (correct) but long scroll to see cards.
- Focus step locked until reading Qs + focus plan built — logic correct but under-explained in UI.

---

## 3. UI/UX audit

### 3.1 Strengths
- Split reading layout (resizable xl+, stacked mobile).
- Sidebar auto-collapse on day open.
- Reading Coach feed with hide, EN/VI toggle, scroll-to-existing card on re-select.
- Word double-click lookup + phrase translate/explain popovers.

### 3.2 Issues

| ID | Severity | Issue | Location |
|----|----------|-------|----------|
| UX-01 | P1 | Day flow strings hardcoded English | `CoachingDayFlow.tsx` |
| UX-02 | P1 | Reading pane "Continue" not i18n | `CoachingReadingPane.tsx` |
| UX-03 | P2 | No feedback when Reading Coach API fails | `ReadingCoachPanel.tsx` |
| UX-04 | P2 | EN/VI toggle leaves cards in previous locale | `ReadingCoachPanel` + hook |
| UX-05 | P3 | Thinking loader on every fetch even cache hit path | `ReadingCoachPanel.tsx` |
| UX-06 | P3 | Step order label "Memory recall" vs vi "Ôn từ" inconsistent sitewide | `CoachingDayFlow.tsx` |

### 3.3 Accessibility
- Reading split separator has `aria-*` ✓
- Coach locale buttons have `aria-pressed` ✓
- Passage selection relies on double/triple click — document in empty state ✓

---

## 4. Bug register

| ID | P | Bug | Root cause | Fix |
|----|---|-----|------------|-----|
| BUG-01 | P0 | Duplicate parallel `helper-note` requests | `useEffect` deps include unstable `coaching` object | Narrow deps + in-flight key dedup |
| BUG-02 | P0 | AbortController not wired to fetch | `fetchReadingCoachNote` ignores signal | Pass `signal` to `authFetch` |
| BUG-03 | P1 | Coach errors swallowed | `catch {}` silent | Quiet error banner + auto-clear |
| BUG-04 | P1 | Locale switch shows wrong-language cards | Feed not cleared on toggle | Clear feed on `setHelperLocale` |
| BUG-05 | P2 | `scrollToTop` on pending before navigate | Effect sets pending before navigate check | Navigate-first path already partial; dedup reduces noise |
| BUG-06 | P2 | `schedulePersistProgress` fires on every `readingCoachFeed` change | Broad `useEffect` deps in hook | Acceptable (debounced persist); monitor payload size |
| BUG-07 | P3 | Mock LLM placeholder text in prod if OpenAI fails | BE fallback | Already fixed temperature retry; monitor logs |

---

## 5. Performance audit

| Hot path | Issue | Recommendation | Sprint |
|----------|-------|----------------|--------|
| `helper-note` | 2s debounce + duplicate calls | In-flight dedup by `locale+selection` | 1 ✓ |
| `flushEventsNow` before note | Extra round-trip | Keep (events needed for focus plan); optional skip if queue empty | 2 |
| `persistProgress` | Includes full `readingCoachFeed` in workspace | Cap feed at 8 cards ✓; consider strip heavy fields | 2 |
| `lookupWord` | Parallel lookup + translate | OK | — |
| BE cache | Postgres `reading_coach_note_cache` | Hit path ~ms; first click ~LLM latency | Monitor |

---

## 6. Logic review (key functions)

### FE

| Module | Function | Verdict |
|--------|----------|---------|
| `coachingDayUtils` | `buildAdaptiveFocusWords` | ✓ Forgot + interaction + reading signals |
| `coachingDayProgress` | `pickResumeStep` | ✓ Resume step sane |
| `coachingReadingState` | `evaluateReadingCoachNoteTrigger` | ✓ Selection-only trigger |
| `readingCoachFeedMatch` | `findReadingCoachCardForSelection` | ✓ Word+sentence key |
| `ReadingCoachPanel` | `syncLearningNote` | ⚠ Fixed: dedup, abort, errors |
| `CoachingReadingPane` | `applySelection` | ✓ Dedup via `lastSelectionSignalRef` |
| `useVocabCoaching` | `enqueueEvent` / `flushEvents` | ✓ 1.2s batch flush |
| `useVocabCoaching` | `openDay` | ✓ Cache merge remote workspace |

### BE

| Module | Function | Verdict |
|--------|----------|---------|
| `vocab_coaching_service` | `generate_helper_note` | ✓ Cache lookup → LLM → upsert |
| `reading_coach_cache` | `cache_key_from_selection` | ✓ Context-bound key |
| `vocab_coaching.py` | `coaching_helper_note` | ✓ Pydantic v3 models |

---

## 7. Feature backlog (post Sprint 1)

1. **Coach hint animation** — first visit tooltip on double-click (1 session).
2. **Prefetch on hover** — optional debounced prewarm for difficult words in passage.
3. **Focus plan preview** — show why each word was picked (source chips already in data).
4. **Offline workspace** — expand localStorage cache conflict resolution UI.
5. **Reading progress** — paragraph-level progress bar on long passages.

---

## 8. Sprint 1 — implemented now

- [x] Plan document (this file)
- [x] BUG-01: In-flight dedup `locale + selectionKey`
- [x] BUG-02: AbortSignal on `fetchReadingCoachNote`
- [x] BUG-03: Quiet coach error message
- [x] BUG-04: Clear feed on helper locale change
- [x] UX-01/02: i18n for day flow loading + reading Continue
- [x] Narrow `ReadingCoachPanel` effect dependencies
- [x] BE test: `readingCoachFeedMatch` parity tests in `test_vocab_coaching.py`
- [x] `npm run build` + `pytest tests/test_vocab_coaching.py`
- [x] Commit + push FE `main`, BE `release-main`

---

## 9. Test plan

| Test | Command | Pass criteria |
|------|---------|---------------|
| FE build | `npm run build` | No TS/eslint errors |
| BE unit | `pytest tests/test_vocab_coaching.py` | All green |
| Manual | Login → Day 1 → double-click word | One network `helper-note` per selection |
| Manual | Click same word again | Scroll to card, no new request |
| Manual | Toggle VI → select word | Card in Vietnamese |
| Manual | Airplane mode click | Error hint visible |

---

## 10. Deploy

- FE: `main` → Vercel `primevocab.com`
- BE: `release-main` → Railway (tests only this sprint; no API break)
