# Parallel retrieval tracks — shared context & protocol (2026-07-10)

Four independently-dispatchable work tracks to push retrieval quality, written so a
fresh agent session (usually in a worktree) can execute one track without further
briefing. **Read this file first, then your track's brief. Do only your track.**

| Track | Brief | Where to run |
| --- | --- | --- |
| A — compact output + `gloss show` + skill wiring | `2026-07-10-track-a-compact-skill.md` | worktree (no corpus needed) |
| B — LLM rerank of top-k | `2026-07-10-track-b-rerank.md` | **claimed by the main session 2026-07-10** — brief kept for handoff |
| C — question top-up enrichment | `2026-07-10-track-c-question-topup.md` | **main checkout** (needs PDF + checkpoints + minimax quota) |
| D — eval-set expansion | `2026-07-10-track-d-eval-expansion.md` | worktree (db copy, read-only) |

## Why (the objective)

The corpus is consumed by Claude inside a workflow: retrieval output goes straight
into an agent's context. The objective is **the #1 result being correct without
per-use steering** — hit@1 is the primary metric; token cost of the output is the
secondary one.

## Current state (verify, don't trust)

Live corpus: `build/minimax-v2.db` — 197 units, embedded (1,493 mean-centered
768-dim vectors). Hybrid retrieval = BM25 + vector max-sim fused with RRF (rrf_k=20),
per-channel rank tags on every hit. Verify:

```bash
sqlite3 build/minimax-v2.db "SELECT COUNT(*) FROM units;"      # 197
sqlite3 build/minimax-v2.db "SELECT COUNT(*) FROM vectors;"    # 1493
uv run --extra build gloss eval --db build/minimax-v2.db --mode hybrid
# hit@5=0.94 hit@1=0.71 mrr=0.80 n=31
```

Rank histogram on the 31 cases (hybrid): 22 at #1, 4 at #2, 2 at #4, 1 at #5,
2 misses. So: ~7 near-misses (reranker territory, ceiling hit@1=0.935) and 2 recall
misses (only enrichment coverage reaches them).

Read before coding: `CLAUDE.md` (three facts), `docs/ARCHITECTURE.md`,
`docs/DESIGN.md` (§2 + the "Retrieval experiment log"), `docs/CLI.md`.

## Ground rules (all tracks)

1. **Query path stays stdlib-packages-only** — `tests/test_stdlib_contract.py` must
   stay green. Ollama is allowed as a *service* (stdlib `urllib`), never a package.
2. **Verbatim invariant** — returned text is the source's own words, fixed at
   segmentation. Nothing may rewrite it.
3. **Every ranking-behavior change is eval-gated**:
   `uv run --extra build gloss eval --db build/minimax-v2.db --mode <A> --vs <B>`
   (paired sign-flip p-value). House rule: n=31 ⇒ one case ≈ 3.2 points; adopt on
   p-value, or on measured mechanism + zero regressions, and record which in
   DESIGN.md's experiment log.
4. **Anti-Goodhart** — never write index content (questions, prompts) or filter eval
   candidates by looking at what the current system gets right/wrong. Track briefs
   repeat the specific rule where it bites.
5. Tests: `uv run --extra build pytest -q` (59 pass with the PDF, 50 pass / 9 skip
   without it — a worktree without the PDF is fine).
6. Commit in logical groups, conventional-commit style, matching repo history.

## Worktree setup

`build/` and `resources/` are gitignored — a fresh worktree has neither. From the
main checkout (`/Users/spencer/code/gloss`):

```bash
mkdir -p build && cp /Users/spencer/code/gloss/build/minimax-v2.db build/   # tracks B, D
# PDF only for track C / the 9 corpus-gated tests:
mkdir -p resources && cp "/Users/spencer/code/gloss/resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf" resources/
```

Ollama is a shared local service (`http://localhost:11434`); concurrent use across
sessions is fine. `embeddinggemma:latest` is pulled.

## File ownership (conflict map)

| file | A | B | C | D |
| --- | --- | --- | --- | --- |
| `src/gloss/cli.py` | ✓ (format + `show`) | ✓ (`--rerank` flags) | ✓ (new subcommand) | — |
| `src/gloss/store.py` | ✓ (`get_unit`) | — | — | — |
| `src/gloss/rerank.py` (new) | — | ✓ | — | — |
| `src/gloss/enrich.py` / `corpora/aposd/prompt.md` | — | — | ✓ | — |
| `corpora/aposd/cases.yaml` | — | — | — | ✓ (owner) |
| `.claude/skills/software-design-philosophy/` | ✓ | — | — | — |
| docs | CLI.md, skill | CLI.md, DESIGN.md | BUILDS.md, DESIGN.md | DESIGN.md |

`cli.py` overlaps are small adjacent argparse blocks — expect trivial merge
conflicts, nothing structural.

## Merge protocol (semantic conflicts)

- **D changes the measuring stick.** Ideal landing order is D first. Any track that
  measured against n=31 re-runs its `--vs` comparison once after D merges and
  updates its recorded numbers.
- **C changes the corpus** (new questions → rebuild → re-embed → different vectors).
  After C merges, re-run the eval comparisons. C must end with
  `gloss embed --db build/minimax-v2.db` — a rebuild wipes the vectors table
  (CLAUDE.md fact #3).
- After all tracks land: refresh the numbers in BUILDS.md / STARTUP_GUIDE.md /
  README.md / DESIGN.md once, in a single docs commit.
