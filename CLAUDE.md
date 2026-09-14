# CLAUDE.md

Thin index for working in this repo. It carries only the project identity, a map to the
detailed docs, and the three facts that bite most often. Open the topic doc for anything
deeper — don't infer from this page.

## What it is

`docq` turns a source text into a portable, **cited** SQLite/FTS5 corpus you search by
free-text query + metadata, getting back the source's *actual passages* (not a paraphrase).
Retrieval is hybrid — BM25 + an optional vector channel (RRF-fused, per-channel rank tags
on every hit) — falling back to pure lexical when the db has no vectors or Ollama is down.
A corpus-agnostic **engine** (`src/docq/`) + per-book **instances** (`corpora/<name>/`).
Only instance so far: `aposd` (Ousterhout's *A Philosophy of Software Design*). Query-time
is stdlib-packages-only (hybrid additionally wants a local Ollama *service*); build-time
needs the `build` extra + an Ollama model.

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
# query (stdlib-only; hybrid when the db is embedded, lexical otherwise) — flags in docs/CLI.md
uv run docq retrieve "<query>" --db build/minimax-v2.db -k 3

# embed the semantic channel into the db (stdlib-only; needs local Ollama + embeddinggemma)
uv run docq embed --db build/minimax-v2.db

# build (needs `build` extra + Ollama model + source PDF) — internals in docs/BUILDS.md
uv run --extra build docq build --model minimax-m3:cloud --workers 8 \
    --db build/minimax-v2.db --build-dir build/minimax-v2

# tests (no model, no corpus needed)
uv run --extra build pytest -q
```

## Three facts that bite

1. **The live db is configured and model-named.** This checkout's `.docq/config.json`
   selects **`build/minimax-v2.db`** (197 units); `build/minimax.db` is the stale
   pre-seg-fix 257-unit build. `--db` overrides configuration. A stale 0-byte
   `build/aposd.db` left by an old build will open but
   error → `OperationalError: no such table: units_fts`; verify with
   `sqlite3 <db> "SELECT COUNT(*) FROM units;"`.
2. **Verbatim text and the coarse `principle` are not the LLM's.** Unit boundaries + text
   are fixed deterministically at segmentation (code blocks travel with their lead-in
   prose); `principle` is set from the taxonomy. The LLM only writes *retrieval metadata*
   (context line, symptom questions, key terms). So retrieval always returns the source's
   own words. (Details in `docs/ARCHITECTURE.md`.)
3. **Vectors don't survive a rebuild.** `docq build` overwrites the db file, wiping the
   `vectors` table — re-run `docq embed --db <db>` (~16s) after every build, or hybrid
   silently degrades to lexical (no `via` tags on hits). Check with
   `sqlite3 <db> "SELECT COUNT(*) FROM vectors;"`.
