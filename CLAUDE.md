# CLAUDE.md

Thin index for working in this repo. It carries only the project identity, a map to the
detailed docs, and the two facts that bite most often. Open the topic doc for anything
deeper — don't infer from this page.

## What it is

`gloss` turns a source text into a portable, **cited** SQLite/FTS5 corpus you search by
lexical query + metadata, getting back the source's *actual passages* (not a paraphrase).
A corpus-agnostic **engine** (`src/gloss/`) + per-book **instances** (`corpora/<name>/`).
Only instance so far: `aposd` (Ousterhout's *A Philosophy of Software Design*). Query-time
is stdlib-only; build-time needs the `build` extra + an Ollama model.

## Where to look

| You want to… | Read |
| --- | --- |
| Overview, status, provenance, license | [`README.md`](README.md) |
| Go fresh-checkout → queryable corpus (fetch PDF → build → verify) | [`docs/STARTUP_GUIDE.md`](docs/STARTUP_GUIDE.md) |
| Every CLI flag, default, output format, the controlled vocabulary | [`docs/CLI.md`](docs/CLI.md) |
| How the system is structured: modules, data flow, seams, invariants, schema | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Build internals: checkpoints, `--resume`, `--workers`, model A/B, troubleshooting | [`docs/BUILDS.md`](docs/BUILDS.md) |
| How tests are run/laid out, the extractor stub seam, coverage gaps | [`docs/TESTING.md`](docs/TESTING.md) |
| *Why* it's built this way — decisions & tradeoffs | [`docs/DESIGN.md`](docs/DESIGN.md) |
| Full design rationale (source-of-truth spec, plans, session notes) | `docs/superpowers/` |

## Day-to-day

```bash
# query (stdlib-only) — full flags in docs/CLI.md
uv run gloss retrieve "<query>" --db build/minimax.db -k 3

# build (needs `build` extra + Ollama model + source PDF) — internals in docs/BUILDS.md
uv run --extra build gloss build --model minimax-m3:cloud --workers 8 \
    --db build/minimax.db --build-dir build/minimax

# tests (no model, no corpus needed)
uv run --extra build pytest -q
```

## Two facts that bite

1. **The live db is model-named, not `aposd.db`.** Builds go to `build/<model>.db` (the
   real corpus today is **`build/minimax.db`**, 257 units). The CLI default `--db aposd.db`
   / the stray `build/aposd.db` is often a **0-byte stub** → `OperationalError: no such
   table: units_fts`. Verify before use: `sqlite3 <db> "SELECT COUNT(*) FROM units;"`.
2. **Verbatim text and the coarse `principle` are not the LLM's.** Unit boundaries + text
   are fixed deterministically at segmentation; `principle` is set from the taxonomy. The
   LLM only writes *retrieval metadata* (context line, symptom questions, key terms). So
   retrieval always returns the source's own words. (Details in `docs/ARCHITECTURE.md`.)
