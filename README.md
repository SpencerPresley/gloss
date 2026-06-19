# gloss

Turn a source text into a small, portable, **cited** corpus you can search by lexical query and
metadata — and get back the source's *actual passages*, with citations, instead of a paraphrase.

`gloss` splits into a corpus-agnostic **engine** (`src/gloss/`) and per-book **instances**
(`corpora/<name>/`). The first — and so far only — instance is John Ousterhout's
*A Philosophy of Software Design* (APOSD): a deep, searchable backing for a software-design
skill, surfacing the book's own words and examples on demand.

> **Status:** early; `gloss` is a working title. It builds the full APOSD corpus end-to-end and
> retrieval returns sensible cited passages, but the eval set is small (16 cases) and real-world
> usefulness hasn't been battle-tested. Treat it as a working prototype, not a finished product.

## How it works

Two phases, one portable artifact:

- **Build** (offline, once, needs a model): PyMuPDF font-aware parse → deterministic unit
  segmentation (verbatim text is fixed here, never LLM-rewritten) → per-unit LLM enrichment
  (retrieval metadata: a context line, symptom-phrased questions, key terms) → a single
  SQLite/FTS5 `.db`. Checkpointed, resumable, and concurrent.
- **Query** (repeated, zero dependencies): FTS5 BM25 + metadata filter over that file.
  **Stdlib-only** — copy the `.db` plus one script anywhere Python runs; nothing to install.

Boundaries and verbatim text are deterministic; the LLM only classifies and generates retrieval
fields, so what comes back is always the source's own words.

## Quickstart

```bash
# Tests (no model, no corpus needed):
uv run --extra build pytest -q

# Build a corpus (needs the source PDF locally + an Ollama model — neither is included):
uv run --extra build gloss build --model <model> --workers 8 --db build/aposd.db

# Retrieve (stdlib-only — no extras):
uv run gloss retrieve "should I make this API general purpose" --db build/aposd.db -k 3 \
    [--principle general-purpose] [--type red_flag] [--json]

# Eval — top-k hit-rate over corpora/<name>/cases.yaml:
uv run --extra build gloss eval --db build/aposd.db
```

## Layout

```
src/gloss/        engine — corpus-agnostic: parse, segment, enrich, store, cli, taxonomy
corpora/aposd/    APOSD instance — profile, taxonomy, enrichment prompt, eval cases
docs/             design spec, implementation plans, session notes
tests/
```

## Provenance & license

The `gloss` engine and tooling are MIT-licensed (see [`LICENSE`](LICENSE)).

The `software-design-philosophy` skill (`.claude/skills/`) and the APOSD taxonomy
(`corpora/aposd/taxonomy.yaml`) are **distillations of John Ousterhout's _A Philosophy of Software
Design_**, kept here as the development input that seeded the corpus — derived summaries, not
original work. The book itself (the source PDF) and any built corpus database are **not**
distributed (they're gitignored); building a corpus requires your own copy of the source.
