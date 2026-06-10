# Session handoff — 2026-06-10 (full-book build)

## Status: Whole book built, reviewed, merged to `main` ✅

Continues the Ch.6 slice (`2026-06-10-session-handoff.md`). This session turned `gloss` into a
**whole-book builder** and produced the full *A Philosophy of Software Design* corpus. Plan:
`docs/superpowers/plans/2026-06-10-aposd-full-book-build.md`. **43 tests pass**
(`uv run --extra build pytest -q`). All work merged to `main` (branch `impl/full-book-build` deleted).

## What was built (tasks 1–9 + a CLI safety add)

1. **Dynamic chapter detection** — `Profile.chapter_re` (`r"^Chapter\s+(\d+)"`) + `split_chapters()` in
   `segment.py`. Replaces hardcoded `chapter_pages` (now an optional override). Detects all 21 chapters
   from the parsed level-1 headings; front/back matter (no "Chapter N" marker) falls out automatically.
   **This is the agnostic-friendly fix:** onboarding a new clean-fonted book needs no page-measuring.
2. **Multi-chapter `run_build`** — detects chapters, enriches each with *its* taxonomy principle card,
   indexes appendices, accumulates into one `aposd.db`. Injectable `extractor`/`build_dir` for stub tests.
3. **Summary appendices** — p.185 "Summary of Design Principles" + p.186–187 "Summary of Red Flags"
   indexed as `principle: null` (the red-flag/principle names are captured in the unit text).
4. **Eval** — 16 cross-principle symptom-phrased cases in `corpora/aposd/cases.yaml`.
5. **Bounded concurrency** — `--workers` (thread pool, warmup-pinned extractor method, lock-guarded
   checkpoint writes). minimax full build used **<7% of the 5-hour budget** with `--workers 8`.
6. **Cap-resilient resume** — `_done_keys` excludes `needs_enrich=1`; `enrich_units` dedups its return by
   key (last-wins). A quota cap no longer bakes empty data: `--resume` re-enriches failed units cleanly.
7. **Principle from taxonomy** — `run_build` overrides each row's `principle` with the chapter's taxonomy
   slug (or `""` for null). Fixes the pollution where null chapters' empty card let the LLM invent ~35
   one-off slugs. The coarse facet is now a closed set: the 6 slugs + empty.
8. **`--build-dir` CLI flag** — so per-model builds don't clobber each other's `build/ch*` checkpoints.

## Artifacts (both gitignored under `build/`, regenerable)

- `build/aposd.db` — **devstral-small-2:24b-cloud**, 257 units, 0 failures, clean facet.
- `build/aposd-minimax.db` — **minimax-m3:cloud**, 257 units, 0 failures, clean facet
  (built with `--build-dir build/minimax --db build/aposd-minimax.db`, devstral untouched).
- Per-chapter checkpoints: devstral at `build/ch*/units.jsonl`, minimax at `build/minimax/ch*/units.jsonl`.
- Regenerate a db from checkpoints with **zero quota**: `gloss build --resume --db <path> [--build-dir <dir>]`
  (all units already enriched → no LLM calls; only re-applies the principle override + rebuilds the db).

## Model A/B result (Task 7) — minimax marginally ahead, not decisive

`gloss eval`: **devstral 0.75 (12/16), minimax 0.81 (13/16).** Per-case: minimax **fixed both complexity
cases** (the systematic abstract-principle recall gap) + 1 ambiguous general-purpose case, but **regressed
2 concrete cases** devstral got (classitis method-count → deep-modules; temporal setup-order → info-hiding).
The category-level complexity improvement looks real; the 2 regressions look like ranking jitter. **+1 net
on 16 cases is within noise — not a confident lock.** Leaning minimax as primary (quality candidate, clean,
cheap, fixed the systematic gap); CLI default is already `minimax-m3:cloud`. **Lock it properly only after
strengthening the eval.**

## Known gaps / follow-ups (prioritized)

1. **Strengthen the eval set** — 16 cases is too thin to lock the model. Add section-level + more complexity
   + disambiguated cases (the boolean-flag case is genuinely dual-homed; "named after one caller" is fuzzy).
   Then re-run the devstral/minimax compare and lock the winner.
2. **Drop the redundant LLM `principle` field** — `Enrichment.principle` is generated then always overridden
   by the taxonomy (Task 9). Dead field; remove from the schema + `prompt.md` to save tokens (two reviewers
   flagged it). Touches stub payloads in tests.
3. **Tune BM25 `_WEIGHTS`** (`store.py`, gap #3) on the strengthened eval — esp. to lift complexity recall.
4. **Circuit-breaker for true cap-stop** — current retry loop grinds through a cap (3s backoff/unit) marking
   failures; clean-resume (Task 8) already prevents permanent empty data, so this is efficiency-only. Doing
   it right in the concurrent path needs `cancel_futures`. Deferred.
5. **Granularity** — chapters with no level-2 headings collapse to 1 prose unit (ch11, ch21, appendices).
   Text is all searchable; sub-splitting long prose runs is a possible future refinement (spec chose
   deterministic prose-run units deliberately).
6. **Distribution** (prior plan Task 13, Spencer leads) — bundle a db as package data (`importlib.resources`),
   `uvx gloss retrieve`. Decide which model's db ships (see A/B above). No `*.db` gitignore rule — keep dbs
   under `build/`.
7. **Skill integration (deferred, do NOT edit the skill yet)** — wire `software-design-philosophy` to call
   `gloss retrieve --json` for primary-source passages.

## How to run

```bash
# Whole-book build (per-model dirs so artifacts coexist):
uv run --extra build gloss build --model minimax-m3:cloud --workers 8 \
    --db build/aposd-minimax.db --build-dir build/minimax
# Regenerate a db from checkpoints (no quota):
uv run --extra build gloss build --resume --db build/aposd-minimax.db --build-dir build/minimax
# Eval / retrieve (retrieve is STDLIB-ONLY):
uv run --extra build gloss eval --db build/aposd-minimax.db
uv run gloss retrieve "should I make this API general purpose" --db build/aposd-minimax.db -k 3 [--principle general-purpose]
uv run --extra build pytest -q
```
