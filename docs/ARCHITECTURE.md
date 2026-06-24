# Architecture

How gloss is structured and how data flows through it. For the *why* behind these
decisions see [DESIGN.md](DESIGN.md); for a fresh-checkout runbook see
[STARTUP_GUIDE.md](STARTUP_GUIDE.md).

## Engine vs instance

gloss splits into a corpus-agnostic **engine** and per-book **instances**.

- **Engine** — `src/gloss/`. Contains no document-specific knobs. Fonts, page
  thresholds, chapter/section regexes, and the taxonomy all arrive through a
  `Profile` and instance files, never hardcoded in engine logic
  (`src/gloss/profile.py:1`).
- **Instance** — `corpora/<name>/`. One directory per source book. Only `aposd`
  exists today. Four files:
  | file | what it configures |
  | --- | --- |
  | `profile.py` | the `Profile` (PDF path, fonts, sizes, regexes, appendices) |
  | `taxonomy.yaml` | the two-facet controlled vocabulary (principles + topics) |
  | `prompt.md` | the enrichment system prompt + user template |
  | `cases.yaml` | eval cases (query -> expected section/principle) |

The engine consumes an instance by path (`run_build(..., instance=Path("corpora/aposd"))`);
adding a book means adding a `corpora/<name>/` directory, not touching `src/gloss/`.

## Two phases and the dependency boundary

gloss runs in two phases with very different dependency footprints:

1. **Build (offline, once per book/model)** — parse the PDF, segment into units,
   LLM-enrich each unit, write a SQLite/FTS5 db. Needs the `build` extra
   (pymupdf, langchain, langchain-ollama, pydantic, pyyaml) plus a running Ollama
   model.
2. **Query (online, repeated)** — open the db and run lexical search. **Stdlib
   only.** The db is portable; it runs anywhere Python runs with nothing
   installed.

The boundary is enforced in code, not just convention:

- `pyproject.toml` declares `dependencies = []`; all build deps live under the
  `build` optional-extra (`pyproject.toml:8`).
- `src/gloss/store.py` (the query hot path) imports only `re` and `sqlite3`
  (`src/gloss/store.py:6`).
- `src/gloss/cli.py` imports `store.search` at module top, but **lazily** imports
  `build.run_build` and `evalrun.run_eval` inside the command handlers
  (`src/gloss/cli.py:31`, `src/gloss/cli.py:38`). So `gloss retrieve` never pulls
  in langchain/pymupdf even if they aren't installed.
- `src/gloss/extract.py` has no top-level langchain/ollama import; the provider
  SDK is imported inside `OllamaExtractor._chat_model` (`src/gloss/extract.py:64`),
  so importing the `StructuredExtractor` protocol never drags in a provider.

## Data-flow walkthrough

```
                          corpora/aposd/  (instance: profile, taxonomy, prompt)
                                 │
                                 ▼
  PDF ──parse_pdf──► [Element]  ──segment──► ([RawUnit], section_texts)
       (parse.py)    headings/   (segment.py)  verbatim text FIXED here
                     paras/code/                       │
                     figures                           ▼
                                          enrich_units (enrich.py)
                                          per unit: build_prompt(card, section, passage)
                                                   │  StructuredExtractor.extract
                                                   ▼  (extract.py: Ollama or stub)
                                          checkpoint rows (build/ch<id>/units.jsonl)
                                          row = verbatim text + Enrichment fields
                                                + needs_enrich flag
                                                   │
                                 build.py overrides row["principle"] from taxonomy
                                                   ▼
                                          build_db (store.py)
                                          units table + units_fts mirror + trigger
                                                   │
                                                   ▼
                                          search (store.py)  ◄── gloss retrieve (cli.py)
                                          BM25 over FTS5, returns verbatim passages
```

`build.py:run_build` wires the stages for one chapter (or the whole book)
(`src/gloss/build.py:47`).

### Stage shapes

**parse** — `parse_pdf(path, first_page, last_page, profile) -> list[Element]`
(`src/gloss/parse.py:59`). Walks PyMuPDF's block/line/span structure (already in
reading order) and emits ordered `Element` objects. `Element` is
`(kind, text, page, level)` where `kind ∈ {heading, para, code, figure}` and
`level` is heading depth (1=chapter, 2=section, 0 otherwise)
(`src/gloss/parse.py:21`). Font classification is substring-based because PDFs use
subset prefixes like `AAAAAE+LucidaSans-Typewriter` (`src/gloss/parse.py:38`).
Contiguous code-font lines accumulate into one `code` Element; figures below
`profile.figure_min_area` are dropped as icons.

**segment** — `segment(elements, profile, chapter) -> (list[RawUnit], dict[str,str])`
(`src/gloss/segment.py:68`). Groups Elements into retrieval units and returns a
`(units, section_texts)` tuple:
- `RawUnit` = `(text, chapter, section, page, is_code)` — verbatim text, plus
  provenance (`src/gloss/segment.py:48`). A unit is one contiguous prose run within
  a section, or a single code block. **This is where verbatim text is fixed.**
- `section_texts` maps section id -> the section's full concatenated text
  (headings excluded), used as situating context during enrichment.

`split_chapters(elements, profile)` slices whole-document Elements into per-chapter
spans by `profile.chapter_re` markers, dropping front matter
(`src/gloss/segment.py:15`).

**enrich** — `enrich_units(units, section_texts, extractor, ...) -> list[dict]`
(`src/gloss/enrich.py:94`). For each not-yet-checkpointed unit, renders the prompt
(`card` + `section` + `passage`) and calls `extractor.extract(prompt, Enrichment)`.
Each returned **row** is a dict combining verbatim text + provenance + the generated
`Enrichment` fields + a `needs_enrich` flag (`src/gloss/enrich.py:89`):
- `Enrichment` fields: `principle`, `type`, `context_line`, `applies_when`,
  `key_terms[]`, `questions[]` (`src/gloss/enrich.py:23`).
- `needs_enrich = 1` when the extractor fails after retries; the row keeps its
  verbatim text but empty generated fields. Code units are forced to
  `type="code"` (`src/gloss/enrich.py:88`).

Rows are checkpointed to JSONL as they complete (last-write-wins per key on
read-back), so an interrupted run resumes by key (`src/gloss/enrich.py:130`).

**store** — `build_db(rows, db_path)` (re)builds the SQLite/FTS5 store, overwriting
the file (`src/gloss/store.py:45`). `search(db_path, query, k, principles, types)`
returns up to k rows ranked by BM25 with optional metadata filters, in one SQL
statement (`src/gloss/store.py:68`).

## Module reference

| module | single responsibility |
| --- | --- |
| `__init__.py` | package docstring + `__version__` (`src/gloss/__init__.py:11`); engine-vs-instance framing. No logic. |
| `profile.py` | `Profile` frozen dataclass: the per-corpus knobs (fonts, sizes, regexes, appendices). |
| `parse.py` | font-aware PDF -> ordered structural `Element`s in reading order. |
| `segment.py` | deterministic unit boundaries: `Element`s -> `RawUnit`s + `section_texts`. Verbatim text fixed here. |
| `extract.py` | the LLM seam: `StructuredExtractor` protocol + `OllamaExtractor` + `StubExtractor`. |
| `enrich.py` | per-unit LLM enrichment + JSONL checkpoint/resume; defines the `Enrichment` schema. |
| `taxonomy.py` | load taxonomy.yaml; map chapter -> principle; render a per-principle card. |
| `store.py` | SQLite/FTS5 store: DDL, `build_db`, `to_match_query`, `search`. **Stdlib only.** |
| `build.py` | orchestrate parse->segment->enrich->store for one chapter or the whole book; size `num_ctx`. |
| `evalrun.py` | score retrieval against cases.yaml (top-k hit-rate). |
| `cli.py` | argparse CLI: `retrieve` (stdlib) / `build` / `eval` (lazy-imported). |

## Key seams

### StructuredExtractor (extract.py)

The build pipeline depends on exactly one method:
`extract(prompt, schema, *, system) -> BaseModel` (`src/gloss/extract.py:13`).
Three implementations:

- `StubExtractor` — deterministic test double; returns `schema(**payload)`. No
  provider (`src/gloss/extract.py:23`).
- `OllamaExtractor` — backed by Ollama via langchain-ollama
  (`src/gloss/extract.py:33`). It **auto-discovers which structured-output method
  the model honors and pins it**: it tries `_METHODS = ("json_schema",
  "function_calling")` in order via `chat.with_structured_output(schema,
  method=..., include_raw=True)`. The first method that returns a parsed result
  with no `parsing_error` is cached in `self._method` so subsequent calls skip
  discovery (`src/gloss/extract.py:76`). `json_schema` (grammar-constrained) is
  tried first; `function_calling` (tool-calling) is the fallback for models that
  ignore `format=` (e.g. minimax). If every method fails it raises `ValueError`.

The pipeline (`build.py`, `enrich.py`) never imports a provider — it accepts any
object satisfying the protocol, which is how tests inject a stub.

### Profile (profile.py)

`Profile` is a frozen dataclass (`src/gloss/profile.py:11`) carrying all
document-specific configuration:

| field | type | configures |
| --- | --- | --- |
| `corpus_path` | `Path` | source PDF |
| `code_font` | `str` | substring identifying the code/monospace font |
| `head_font` | `str` | substring identifying the heading font |
| `chapter_size` | `float` | min span pt for a level-1 (chapter) heading |
| `section_size` | `float` | min span pt for a level-2 (section) heading |
| `figure_min_area` | `int` | min image bbox area (pt²) to count as a figure (drops icons) |
| `section_re` | `str` | regex; group 1 = section id in a level-2 heading |
| `chapter_re` | `str` (default `""`) | regex; group 1 = chapter id in a level-1 heading. Empty disables dynamic detection. |
| `chapter_pages` | `dict[str,(int,int)]` | optional explicit chapter->page-range override (used when `chapter_re` unavailable) |
| `appendices` | `dict[str,(int,int)]` | extra non-chapter spans to index, enriched with no principle card |

The `aposd` instance sets `code_font="Typewriter"`, `head_font="NimbusSanL-Bol"`,
`chapter_size=20.0`, `section_size=16.0`, `chapter_re=r"^Chapter\s+(\d+)"`, and two
appendix pages (`corpora/aposd/profile.py:7`).

### Taxonomy two-facet vocab (taxonomy.py + taxonomy.yaml)

The taxonomy is a **two-facet controlled vocabulary** (`corpora/aposd/taxonomy.yaml:1`):

- **principle** (COARSE) — a closed set of 6 slugs (`complexity`, `deep-modules`,
  `information-hiding`, `general-purpose`, `comments`, `strategic-programming`).
  This is what callers filter on (`--principle`). Each principle entry carries
  `slug`, `name`, `vocabulary`, `diagnostics`, `red_flags`.
- **topic** (FINE) — the book's 21 chapters. Every chapter maps to exactly one
  coarse principle or `null` (no clean home; some are loose-fit "GAP" mappings
  flagged inline).

`taxonomy.py` exposes three functions:
- `load_taxonomy(path)` -> dict with `principles` + `topics` keys.
- `principle_for_chapter(taxonomy, chapter)` -> the coarse slug for a chapter, or
  `None` (`src/gloss/taxonomy.py:18`).
- `card_for(taxonomy, principle)` -> a compact card (name + vocabulary +
  diagnostics + red_flags) for one principle, raising `KeyError` if absent
  (`src/gloss/taxonomy.py:26`).

Enrichment feeds the LLM **only the relevant principle's card**, never the whole
taxonomy (`corpora/aposd/taxonomy.yaml:1`).

## Invariants

1. **Verbatim determinism.** A unit's text is fixed at segmentation
   (`RawUnit.text`) and is never seen-and-rewritten by the LLM — the enrichment
   prompt states the passage "is stored separately and untouched"
   (`corpora/aposd/prompt.md:11`), and `build_db` stores `r["text"]` straight from
   the row. The LLM produces only retrieval *metadata*.
2. **Coarse `principle` comes from the taxonomy, not the LLM.** Although the
   `Enrichment` schema has the LLM emit a `principle`, `build.py` overwrites
   `row["principle"]` with `principle_for_chapter`'s result (or `""`) after
   enrichment (`src/gloss/build.py:118`). This keeps the facet a closed set (the 6
   slugs or empty) and stops null-principle chapters from inventing slugs. Applied
   to read-back rows too, so a `--resume` regenerates the db correctly with no
   re-enrichment.
3. **`num_ctx` sized from real prompts.** `estimate_num_ctx` measures the longest
   actual `system + prompt`, uses `chars // 3` (deliberately *over*-counting
   tokens, since undercounting would truncate), adds `headroom`, clamps to
   `[floor=8192, cap=32768]`, and **warns** rather than silently exceeding the cap
   (`src/gloss/build.py:35`). The pipeline never guesses a constant context size.
4. **Appendix handling.** When building the whole book (`chapter is None`),
   `profile.appendices` ranges are parsed and appended to the chapter specs, then
   enriched with **no principle card** (empty card, `principle=""`)
   (`src/gloss/build.py:80`).

## SQLite schema

Defined in `_DDL` (`src/gloss/store.py:10`); verified against a built db
(`sqlite3 build/minimax.db ".schema"`).

```sql
CREATE TABLE units (
  id INTEGER PRIMARY KEY,
  principle TEXT, chapter TEXT, section TEXT, type TEXT, page INTEGER,
  text TEXT, context_line TEXT, applies_when TEXT, key_terms TEXT, questions TEXT,
  enrich_model TEXT, needs_enrich INTEGER DEFAULT 0,
  CHECK (type IN ('definition','rationale','example','code','red_flag'))
);
CREATE VIRTUAL TABLE units_fts USING fts5(
  text, context_line, applies_when, key_terms, questions,
  content='units', content_rowid='id',
  tokenize="porter unicode61 tokenchars '_'"
);
CREATE TRIGGER units_ai AFTER INSERT ON units BEGIN
  INSERT INTO units_fts(rowid, text, context_line, applies_when, key_terms, questions)
  VALUES (new.id, new.text, new.context_line, new.applies_when, new.key_terms, new.questions);
END;
```

Notes:
- **`units` is the source of truth; `units_fts` is only an index** — all data
  lives in `units` (`src/gloss/store.py:1`). `units_fts` is an external-content
  FTS5 table (`content='units', content_rowid='id'`) mirroring the 5 searchable
  columns.
- The `units_ai` AFTER INSERT trigger keeps the FTS mirror in sync on insert.
  `build_db` also runs `INSERT INTO units_fts(units_fts) VALUES ('optimize')`
  after load (`src/gloss/store.py:62`).
- `key_terms` and `questions` are lists in the row but stored as space-joined
  strings (`src/gloss/store.py:58`).
- Custom tokenizer: `porter unicode61 tokenchars '_'` — Porter stemming, with `_`
  treated as a token char so snake_case identifiers survive.
- BM25 column weights `_WEIGHTS = (10.0, 4.0, 5.0, 8.0, 4.0)` for
  `(text, context_line, applies_when, key_terms, questions)` — verbatim text
  weighted highest (`src/gloss/store.py:31`).
- A built `aposd` db has 257 rows across the 6 non-null principle slugs (plus
  empty-principle units) (`sqlite3 build/minimax.db "SELECT count(*) FROM units"`).
