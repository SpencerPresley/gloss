# Track C — question top-up enrichment

Read `2026-07-10-retrieval-tracks-overview.md` first. Do only this track.
**Run in the main checkout** — needs the source PDF, the `build/minimax-v2/`
checkpoints, minimax cloud quota (~200 calls), and ends with a re-embed.

## Goal

The only move that reaches the 2 recall misses (eval cases whose passage neither
channel surfaces: "callers have to call setup in the right order" → temporal
decomposition; "two far-apart pieces of code have to agree" → hidden dependencies).
Mechanism: generated questions win ~80% of semantic matches (measured, see DESIGN.md
experiment log) — the semantic net is only as wide as the question set, and each unit
currently has 3–6 questions from one generation pass. Add ~4–6 *differently-angled*
questions per unit.

## Anti-Goodhart rule (hard)

Write the top-up prompt from the passage + principle card ONLY. **Never open
`corpora/aposd/cases.yaml` while designing the prompt or reviewing outputs.** The 2
misses above are named here as motivation; do not target their phrasings. If the
prompt is written to the eval, the eval stops measuring anything.

## Design

Persistence goes through checkpoints, not the db (a rebuild must not lose the
top-up). The checkpoint format already supports supersede-by-key (last-wins on
read-back, `enrich.py`), so:

1. New build-extra subcommand `docq enrich-questions --db … --build-dir … --model
   minimax-m3:cloud --workers 8`:
   for each row in each chapter's `units.jsonl` (deduped by key, `needs_enrich=0`
   only), call the extractor with a focused prompt — passage + its existing
   questions + the principle card — asking for 4–6 NEW questions with explicitly
   different angles (developer complaint mid-code; code-review comment; "is it OK
   to…" permission phrasing; consequence question "what goes wrong if…"), no
   paraphrases of existing ones. Append a row copy with `questions = old + new`
   (same key ⇒ last-wins). Checkpointed/resumable like enrichment; reuse
   `StructuredExtractor` (a `Questions(BaseModel)` with one `questions: list[str]`
   field) so the stub-testing seam works.
2. Rebuild + re-embed:
   ```bash
   uv run --extra build docq build --resume --db build/minimax-v2.db --build-dir build/minimax-v2
   uv run docq embed --db build/minimax-v2.db      # rebuild wiped the vectors
   ```
   The `--resume` does zero enrichment (all keys present) and ships the merged
   questions.

Prompt template: add a clearly-marked second template section to
`corpora/aposd/prompt.md` (keep the existing enrichment template untouched) or a
sibling `prompt-questions.md` — follow the existing `<!-- SYSTEM -->`/
`<!-- TEMPLATE -->` convention.

## Experiment protocol

Before: snapshot per-case ranks (hybrid) to a scratch JSON. After rebuild+re-embed:

1. `docq eval --db build/minimax-v2.db --mode hybrid` + paired comparison vs the
   snapshot (`evalrun.paired_sign_flip`). Gate: net non-negative, and specifically
   check whether previously-missing cases now appear (report which).
2. Re-run the multiplicity-bias probe (correlation of #vectors-per-unit vs top-5
   appearances — was r=0.15; more questions per unit could inflate it. If it climbs
   past ~0.4, flag it in the log; candidate correction is mean-of-top-2 scoring, do
   NOT implement without measuring).
3. Record adopted/rejected + numbers in DESIGN.md's experiment log; update BUILDS.md
   (new subcommand + the rebuild/re-embed dance) and CLI.md.

Rollback is free: checkpoints are append-only — the pre-top-up state is recoverable
from git-ignored history only if you back it up first, so **`cp -r build/minimax-v2
build/minimax-v2.pre-topup` before starting.**

## Files

`src/docq/enrich.py` (or new `topup.py` if cleaner), `src/docq/cli.py` (subcommand),
`corpora/aposd/prompt.md` or sibling, `tests/test_enrich.py` additions (stub-driven:
appends supersede-by-key; existing questions preserved; resumable; `needs_enrich=1`
rows skipped), `docs/BUILDS.md`, `docs/CLI.md`, `docs/DESIGN.md`.

Do not touch: `cases.yaml`, `vectors.py` scoring, `store.py`.
