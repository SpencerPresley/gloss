# docq

Turn a source text into a small, portable, **cited** corpus you can search by free-text query and
metadata — and get back the source's *actual passages*, with citations, instead of a paraphrase.
Retrieval is hybrid: FTS5/BM25 plus an optional local-embedding channel, fused by reciprocal rank
fusion, with each hit tagged by which channels ranked it (two agreeing channels = a hit you can
trust).

`docq` splits into a corpus-agnostic **engine** (`src/docq/`) and per-book **instances**
(`corpora/<name>/`). The first — and so far only — instance is John Ousterhout's
*A Philosophy of Software Design* (APOSD): a deep, searchable backing for a software-design
skill, surfacing the book's own words and examples on demand.

> **Status:** early. It builds the full APOSD corpus end-to-end and
> hybrid retrieval scores hit@5=0.94 / hit@1=0.71 / MRR=0.80 on a 31-case eval, but real-world
> usefulness hasn't been battle-tested. Treat it as a working prototype, not a finished product.

## How it works

Two phases (plus an optional embed pass), one portable artifact:

- **Build** (offline, once, needs a model): PyMuPDF font-aware parse → deterministic unit
  segmentation (verbatim text is fixed here, never LLM-rewritten; code blocks travel with the
  prose that introduces them) → per-unit LLM enrichment (retrieval metadata: a context line,
  symptom-phrased questions, key terms) → a single SQLite/FTS5 `.db`. Checkpointed, resumable,
  and concurrent.
- **Embed** (offline, optional, ~16s): `docq embed` writes per-unit vectors — a metadata gist,
  one vector per generated question, chunked verbatim text — into the *same* `.db`, via a local
  Ollama embedding model (`embeddinggemma`).
- **Query** (repeated): BM25 and vector max-similarity as independent channels, fused with
  reciprocal rank fusion; every hit shows its per-channel ranks (`via lex#1+sem#2`).
  **Stdlib-only packages** — lexical mode needs literally nothing installed or running; hybrid
  needs only the local Ollama service, and degrades back to lexical without it.

Boundaries and verbatim text are deterministic; the LLM only classifies and generates retrieval
fields, so what comes back is always the source's own words.

## Quickstart

```bash
# Install persistently or run ephemerally:
uv tool install docq
uvx docq --help

# Include corpus-building dependencies when this machine builds databases:
uv tool install 'docq[build]'

# Tests (no model, no corpus needed):
uv run --extra build pytest -q

# Build a corpus (needs the source PDF locally + an Ollama model — neither is included):
uv run --extra build docq build --model <model> --workers 8 --build-dir build/<model>

# Embed it for hybrid retrieval (stdlib-only; needs local Ollama + embeddinggemma):
uv run docq embed

# Retrieve (stdlib-only — no extras; hybrid when embedded, lexical otherwise):
uv run docq retrieve "should I make this API general purpose" -k 3 \
    [--principle general-purpose] [--type red_flag] [--json] [--mode lexical|hybrid|semantic]

# Eval — hit@k / hit@1 / MRR over corpora/<name>/cases.yaml:
uv run --extra build docq eval --mode hybrid
```

This checkout includes [`.docq/config.json`](.docq/config.json), which supplies its APOSD paths.
An installed copy instead reads `$XDG_CONFIG_HOME/docq/config.json` (or
`~/.config/docq/config.json`) and then `<git-root>/.docq/config.json`; project values override
user values, and CLI flags override both. An explicit `--config FILE` uses only that file plus
CLI flags. Run `docq config` to see the effective values and files considered.

The SQLite corpus is deliberately not included in the wheel. A typical personal configuration is:

```json
{
  "db": "~/.local/share/docq/corpora/aposd.db",
  "retrieve": {
    "rerank-model": "gemma4:e2b"
  }
}
```

## Layout

```
src/docq/        engine — corpus-agnostic: parse, segment, enrich, store, cli, taxonomy
corpora/aposd/    APOSD instance — profile, taxonomy, enrichment prompt, eval cases
docs/             design spec, implementation plans, session notes
tests/
```

## Provenance & license

The `docq` engine and tooling are MIT-licensed (see [`LICENSE`](LICENSE)).

The `software-design-philosophy` skill (`.claude/skills/`) and the APOSD taxonomy
(`corpora/aposd/taxonomy.yaml`) are **distillations of John Ousterhout's _A Philosophy of Software
Design_**, kept here as the development input that seeded the corpus — derived summaries, not
original work. The book itself (the source PDF) and any built corpus database are **not**
distributed (they're gitignored); building a corpus requires your own copy of the source.
