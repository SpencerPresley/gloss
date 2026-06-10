# Session handoff — 2026-06-10 (gloss / aposd-embedded)

## Status: Chapter-6 slice complete and working ✅

The `gloss` engine + APOSD instance are built, reviewed, and **proven end-to-end on Chapter 6**.
Branch `impl/ch6-slice` (not merged to `main`). **28 tests pass** (`uv run --extra build pytest -q`).
The real Ch.6 build produced **21 units, 0 failures**, and retrieval returns the right primary-source
passages with citations (e.g. "separate backspace/delete methods" → §6.4 "the backspace method was a
false abstraction"). Plan: `docs/superpowers/plans/2026-06-10-aposd-embedded.md`. Spec:
`docs/superpowers/specs/2026-06-10-aposd-embedded-design.md`.

## Architecture (one paragraph)
`gloss` is a corpus-agnostic **engine** (`src/gloss/`); APOSD is an **instance** (`corpora/aposd/`).
Pipeline: `parse` (PyMuPDF font-aware → Elements) → `segment` (deterministic units + per-section text)
→ `enrich` (LLM metadata via the `StructuredExtractor` seam, checkpointed) → `store` (SQLite/FTS5,
BM25 + metadata filter, **stdlib-only**). `cli` exposes `retrieve`/`build`/`eval`; `build` orchestrates;
`taxonomy` renders the per-principle card; `evalrun` scores hit-rate. The **query path is stdlib-only**
(guarded by `tests/test_stdlib_contract.py`) so `gloss.db` + a script drop into any repo with nothing installed.

## How to run
```bash
# Build a chapter (build-time deps via --extra build). Checkpoints to build/ch<N>/units.jsonl.
uv run --extra build gloss build --chapter 6 --model devstral-small-2:24b-cloud --db build/ch6.db
#   --resume        continue from the checkpoint (a quota cap / crash resumes from the last unit)
#   (no --resume)   wipes the checkpoint and rebuilds fresh
# Eval (needs the build extra for pyyaml):
uv run --extra build gloss eval --db build/ch6.db
# Retrieve (STDLIB-ONLY — no extra needed):
uv run gloss retrieve "should I make this API general purpose" --db build/ch6.db -k 3 [--principle general-purpose] [--type red_flag] [--json]
# Tests:
uv run --extra build pytest -q
```

## Model findings (this reshapes the future A/B — read before picking a model)
The `StructuredExtractor` auto-discovers the method (tries `json_schema` → falls back to
`function_calling`, pins the winner). What we learned running real models:
- **`devstral-small-2:24b-cloud` ✅** — works via `function_calling`, cheap, good enrichment quality.
  This built Ch.6 (0 failures). **Recommended cheap build model.**
- **`minimax-m3:cloud` ✅** — works via `function_calling` (smoke-tested; not yet run on a full build).
  Likely highest quality; uses more quota. The quality candidate.
- **`gpt-oss:20b-cloud` ❌ — DO NOT USE.** Can't produce structured output through langchain-ollama
  (`json_schema` → empty/invalid JSON; `function_calling` → no valid tool call). The cloud variant
  behaves differently from local `gpt-oss:20b`.
- **`gpt-oss:120b-cloud` — UNTESTED**, may fail like the 20b-cloud variant. Diagnose with a 1-call
  check before any full run (see the pattern we used).
- **local `gpt-oss:20b` ✅** `json_schema` (per research) but **RAM-tight on the 32GB Mac** (~13GB
  weights → swapping). Spencer has a **128GB Mac** for local runs if a free/local build is wanted.

## Decisions locked this session
- **Taxonomy (full-book only):** coarse `principle` = the skill's 6; the 7 no-fit book chapters are
  `principle: null` (still retrievable by text/topic). Promote-candidates if extending: *Define Errors
  Out of Existence*, *Choosing Names*, *Design it Twice*. (Ch.6 = `general-purpose`, a clean fit.)
- **Engine package name:** `gloss` (working title; alternatives in `notes/2026-06-10-naming.md`).
- **Distribution:** deferred (bundle `aposd.db` as package data later; Spencer leads).

## Surprises / fixes this session
- **Ch.6 page range (50,58) bled into Ch.7.** Fixed `segment` to stop at the next chapter's level-1
  title once body content has begun (`d61415d`). **Lesson for the full book:** `chapter_pages` start
  bounds still matter per chapter; the *end* is now handled by segment, but verify each chapter's start.
- **gpt-oss:20b-cloud silently produced an all-empty build** — caught only by peeking at the
  checkpoint's `needs_enrich` count. Now `build` prints a `WARNING` + the failure count, so a
  broken-model build is loud, not silent.

## Known gaps / follow-ups (prioritized)
1. **`num_ctx` real-count logging is deferred** — needs `OllamaExtractor` to surface Ollama's
   `prompt_eval_count`. The conservative `chars//3` estimate prevents truncation meanwhile (Ch.6 hit
   the 8192 floor — prompts are modest).
2. **Eval is weak on a single-principle chapter** — `corpora/aposd/cases.yaml` is a general-purpose
   smoke set (hit-rate 1.00 is trivial here). Needs section-level + cross-principle cases for the full book.
3. **BM25 weights (`_WEIGHTS` in `store.py`) are untuned defaults** `(text=10, ctx=4, applies=5, key_terms=8, questions=4)` — tune on a real eval set at full-book scale.
4. **`store` FTS trigger is insert-only** — assumes wholesale rebuild. If incremental writes are ever
   added (e.g. targeted `needs_enrich` re-runs), add UPDATE/DELETE triggers or the FTS index desyncs.
5. **Built artifacts are gitignored** (`build/`, `*.jsonl`) — the `.db` is regenerable; decide later
   whether to ship it as package data (distribution task).

## Exact next steps
- **Task 11 — model A/B:** rebuild Ch.6 with `devstral-small-2:24b-cloud` vs `minimax-m3:cloud`
  (and a 1-call check of `gpt-oss:120b-cloud`); compare retrieval quality on a strengthened eval set. Lock the build model.
- **Task 12 — full-book build:** finalize the taxonomy gap (null vs promote), build all 188 pages with
  the chosen model, verify per-chapter `chapter_pages`, tune BM25 weights, add real eval cases.
  Checkpoint/resume across quota resets.
- **Task 13 — distribution:** bundle `aposd.db` as package data (`importlib.resources`), `uvx gloss`. (Spencer.)
- **Skill integration (deferred, do not edit the skill yet):** wire the design skill to call
  `gloss retrieve --json` for primary-source passages on a design situation.
