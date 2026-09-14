# Architecture

How docq is structured and how data flows through it. For the *why* behind these
decisions see [DESIGN.md](DESIGN.md); for a fresh-checkout runbook see
[STARTUP_GUIDE.md](STARTUP_GUIDE.md).

## Engine vs instance

docq splits into a corpus-agnostic **engine** and per-book **instances**.

- **Engine** — `src/docq/`. Contains no document-specific knobs. Fonts, page
  thresholds, chapter/section regexes, and the taxonomy all arrive through a
  `Profile` and instance files, never hardcoded in engine logic
  (`src/docq/profile.py:1`).
- **Instance** — `corpora/<name>/`. One directory per source book. Only `aposd`
  exists today. Four files:
  | file | what it configures |
  | --- | --- |
  | `profile.py` | the `Profile` (PDF path, fonts, sizes, regexes, appendices) |
  | `taxonomy.yaml` | the two-facet controlled vocabulary (principles + topics) |
  | `prompt.md` | the enrichment system prompt + user template |
  | `cases.yaml` | eval cases (query -> expected section/principle) |

The engine consumes an instance by path (`run_build(..., instance=Path("corpora/aposd"))`);
adding a book means adding a `corpora/<name>/` directory, not touching `src/docq/`.

## Two phases and the dependency boundary

docq runs in two phases (plus one optional post-pass) with very different
dependency footprints:

1. **Build (offline, once per book/model)** — parse the PDF, segment into units,
   LLM-enrich each unit, write a SQLite/FTS5 db. Needs the `build` extra
   (pymupdf, langchain, langchain-ollama, pydantic, pyyaml) plus a running Ollama
   model.
2. **Embed (offline, optional, once per built db)** — `docq embed` reads the
   finished db's units and writes per-unit vectors into a `vectors` table in the
   **same** file (`src/docq/vectors.py`). Stdlib packages only, but needs a
   running local Ollama serving an embedding model (`embeddinggemma`). Must be
   re-run after any rebuild — `build_db` overwrites the file.
3. **Query (online, repeated)** — open the db and search. **Stdlib packages
   only.** Lexical mode needs literally nothing installed or running; hybrid
   mode (BM25 + vectors, the default when vectors exist) additionally needs the
   local Ollama *service* for one query-embedding call, and degrades back to
   lexical with a stderr note when it's unreachable. The db is portable; vectors
   travel inside it.

The boundary is enforced in code, not just convention:

- `pyproject.toml` declares `dependencies = []`; all build deps live under the
  `build` optional-extra (`pyproject.toml:8`).
- `src/docq/store.py` (the query hot path) imports only `re` and `sqlite3`
  (`src/docq/store.py:6`).
- `src/docq/vectors.py` (the semantic channel) imports only stdlib (`urllib`,
  `array`, `math`, `sqlite3`, …) — its Ollama dependency is an HTTP service, not
  a package, and only the non-lexical modes touch it.
- `src/docq/cli.py` imports `store.search` at module top, but **lazily** imports
  `build.run_build` and `evalrun.run_eval` inside the command handlers
  (`src/docq/cli.py:31`, `src/docq/cli.py:38`). So `docq retrieve` never pulls
  in langchain/pymupdf even if they aren't installed.
- `src/docq/extract.py` has no top-level langchain/ollama import; the provider
  SDK is imported inside `OllamaExtractor._chat_model` (`src/docq/extract.py:64`),
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
                              docq embed (vectors.py, optional post-pass)
                              per-unit gist/question/text vectors -> vectors table
                              in the SAME .db, via local Ollama /api/embed
                                                   │
                                                   ▼
                       search (store.py, BM25)  ─┬─►  docq retrieve (cli.py)
                       search_hybrid (vectors.py)┘    lexical | hybrid (RRF fusion,
                                                      per-channel rank tags) | semantic
```

`build.py:run_build` wires the stages for one chapter (or the whole book)
(`src/docq/build.py:47`).

### Stage shapes

**parse** — `parse_pdf(path, first_page, last_page, profile) -> list[Element]`
(`src/docq/parse.py:59`). Walks PyMuPDF's block/line/span structure (already in
reading order) and emits ordered `Element` objects. `Element` is
`(kind, text, page, level)` where `kind ∈ {heading, para, code, figure}` and
`level` is heading depth (1=chapter, 2=section, 0 otherwise)
(`src/docq/parse.py:21`). Font classification is substring-based because PDFs use
subset prefixes like `AAAAAE+LucidaSans-Typewriter` (`src/docq/parse.py:38`).
Contiguous code-font lines accumulate into one `code` Element; figures below
`profile.figure_min_area` are dropped as icons.

**segment** — `segment(elements, profile, chapter) -> (list[RawUnit], dict[str,str])`
(`src/docq/segment.py:68`). Groups Elements into retrieval units and returns a
`(units, section_texts)` tuple:
- `RawUnit` = `(text, chapter, section, page, is_code)` — verbatim text, plus
  provenance (`src/docq/segment.py:48`). A unit is one contiguous prose run within
  a section — **including any code block the run introduces** (an example is not
  usable without its lead-in sentence, so they travel as one unit; prose after the
  block starts a fresh unit). A one-line code element with no code punctuation
  arriving mid-run is an inline span the parser misread as a block and folds back
  into the prose (`_is_inline_fragment`) — this healed real shattered sentences
  (e.g. "The" / "NetworkErrorLogger" / "class contained…" as three units). Code
  with no prose in progress stays a standalone `is_code` unit. **This is where
  verbatim text is fixed.**
- `section_texts` maps section id -> the section's full concatenated text
  (headings excluded), used as situating context during enrichment.

`split_chapters(elements, profile)` slices whole-document Elements into per-chapter
spans by `profile.chapter_re` markers, dropping front matter
(`src/docq/segment.py:15`).

**enrich** — `enrich_units(units, section_texts, extractor, ...) -> list[dict]`
(`src/docq/enrich.py:94`). For each not-yet-checkpointed unit, renders the prompt
(`card` + `section` + `passage`) and calls `extractor.extract(prompt, Enrichment)`.
Each returned **row** is a dict combining verbatim text + provenance + the generated
`Enrichment` fields + a `needs_enrich` flag (`src/docq/enrich.py:89`):
- `Enrichment` fields: `principle`, `type`, `context_line`, `applies_when`,
  `key_terms[]`, `questions[]` (`src/docq/enrich.py:23`).
- `needs_enrich = 1` when the extractor fails after retries; the row keeps its
  verbatim text but empty generated fields. Code units are forced to
  `type="code"` (`src/docq/enrich.py:88`).

Rows are checkpointed to JSONL as they complete (last-write-wins per key on
read-back), so an interrupted run resumes by key (`src/docq/enrich.py:130`).

**store** — `build_db(rows, db_path)` (re)builds the SQLite/FTS5 store, overwriting
the file (`src/docq/store.py:45`). `search(db_path, query, k, principles, types)`
returns up to k rows ranked by BM25 with optional metadata filters, in one SQL
statement (`src/docq/store.py:68`).

## Module reference

| module | single responsibility |
| --- | --- |
| `__init__.py` | package docstring + `__version__` (`src/docq/__init__.py:11`); engine-vs-instance framing. No logic. |
| `profile.py` | `Profile` frozen dataclass: the per-corpus knobs (fonts, sizes, regexes, appendices). |
| `parse.py` | font-aware PDF -> ordered structural `Element`s in reading order. |
| `segment.py` | deterministic unit boundaries: `Element`s -> `RawUnit`s + `section_texts`. Verbatim text fixed here. |
| `extract.py` | the LLM seam: `StructuredExtractor` protocol + `OllamaExtractor` + `StubExtractor`. |
| `enrich.py` | per-unit LLM enrichment + JSONL checkpoint/resume; defines the `Enrichment` schema. |
| `taxonomy.py` | load taxonomy.yaml; map chapter -> principle; render a per-principle card. |
| `store.py` | SQLite/FTS5 store: DDL, `build_db`, `to_match_query`, `search`. **Stdlib only.** |
| `vectors.py` | optional semantic channel: `embed_corpus` (vectors into the same db via Ollama `/api/embed`), `search_semantic`, `search_hybrid` (RRF fusion + per-channel rank tags), `search_auto` (graceful degrade). **Stdlib only**; Ollama is a service dependency. |
| `rerank.py` | opt-in LLM rerank of the top candidates via Ollama `/api/chat`, gated by `fusion_trusts_top1` (dual-backed #1s are never overridden); reorder-only contract with fallback-to-unchanged on any failure; injectable `chat_fn`. **Stdlib only.** |
| `build.py` | orchestrate parse->segment->enrich->store for one chapter or the whole book; size `num_ctx`. |
| `evalrun.py` | score retrieval against cases.yaml: hit@k, hit@1, MRR; injectable `search_fn` for scoring any mode; `paired_sign_flip` significance test for A/B-ing modes or knobs. |
| `cli.py` | argparse CLI: `retrieve`/`embed` (stdlib) / `build` / `eval` (lazy-imported). |

## Key seams

### StructuredExtractor (extract.py)

The build pipeline depends on exactly one method:
`extract(prompt, schema, *, system) -> BaseModel` (`src/docq/extract.py:13`).
Three implementations:

- `StubExtractor` — deterministic test double; returns `schema(**payload)`. No
  provider (`src/docq/extract.py:23`).
- `OllamaExtractor` — backed by Ollama via langchain-ollama
  (`src/docq/extract.py:33`). It **auto-discovers which structured-output method
  the model honors and pins it**: it tries `_METHODS = ("json_schema",
  "function_calling")` in order via `chat.with_structured_output(schema,
  method=..., include_raw=True)`. The first method that returns a parsed result
  with no `parsing_error` is cached in `self._method` so subsequent calls skip
  discovery (`src/docq/extract.py:76`). `json_schema` (grammar-constrained) is
  tried first; `function_calling` (tool-calling) is the fallback for models that
  ignore `format=` (e.g. minimax). If every method fails it raises `ValueError`.

The pipeline (`build.py`, `enrich.py`) never imports a provider — it accepts any
object satisfying the protocol, which is how tests inject a stub.

### embed_fn (vectors.py)

The vector channel has the same shape of seam one level down: every entry point
(`embed_corpus`, `search_semantic`, `search_hybrid`, `search_auto`) takes an
optional `embed_fn(texts) -> list[vector]`. When `None`, a closure over
`ollama_embed` (stdlib `urllib` against `/api/embed`) is used; tests inject a
deterministic keyword→axis fake, so the whole channel — packing, chunking,
scoring, fusion, fallback — is tested with zero network (`tests/test_vectors.py`).

### Profile (profile.py)

`Profile` is a frozen dataclass (`src/docq/profile.py:11`) carrying all
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
  `None` (`src/docq/taxonomy.py:18`).
- `card_for(taxonomy, principle)` -> a compact card (name + vocabulary +
  diagnostics + red_flags) for one principle, raising `KeyError` if absent
  (`src/docq/taxonomy.py:26`).

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
   enrichment (`src/docq/build.py:118`). This keeps the facet a closed set (the 6
   slugs or empty) and stops null-principle chapters from inventing slugs. Applied
   to read-back rows too, so a `--resume` regenerates the db correctly with no
   re-enrichment.
3. **`num_ctx` sized from real prompts.** `estimate_num_ctx` measures the longest
   actual `system + prompt`, uses `chars // 3` (deliberately *over*-counting
   tokens, since undercounting would truncate), adds `headroom`, clamps to
   `[floor=8192, cap=32768]`, and **warns** rather than silently exceeding the cap
   (`src/docq/build.py:35`). The pipeline never guesses a constant context size.
4. **Appendix handling.** When building the whole book (`chapter is None`),
   `profile.appendices` ranges are parsed and appended to the chapter specs, then
   enriched with **no principle card** (empty card, `principle=""`)
   (`src/docq/build.py:80`).
5. **Query and corpus are embedded the same way.** EmbeddingGemma is prompt-tuned
   (documents vs queries take different instruction prefixes); the prefixes, model,
   and centering mean used at embed time are recorded in `vectors_meta`, and the
   query side reads them back rather than assuming
   (`src/docq/vectors.py:_query_vector`). An index/query mismatch in prefix or
   centering would silently degrade similarity.
6. **The semantic channel never blocks retrieval.** Explicit `--mode hybrid` /
   `semantic` fail loudly; the default `auto` mode degrades to lexical — silently
   for a db that simply has no vectors, with a stderr note when vectors exist but
   the embedder is down. Checkpoint rows from since-changed unit boundaries are
   likewise never shipped: `enrich_units` filters read-back rows to the current
   unit set (`src/docq/enrich.py`).

## SQLite schema

Defined in `_DDL` (`src/docq/store.py:10`); verified against a built db
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

After `docq embed`, the same db additionally carries the semantic channel
(`src/docq/vectors.py:_DDL`):

```sql
CREATE TABLE vectors (
  unit_id INTEGER NOT NULL,
  kind TEXT NOT NULL,           -- 'gist' | 'question' | 'text'
  seq INTEGER NOT NULL,         -- question index / text-chunk index within kind
  vec BLOB NOT NULL,            -- float32, unit-normalized
  PRIMARY KEY (unit_id, kind, seq)
);
CREATE TABLE vectors_meta (
  model TEXT NOT NULL, dim INTEGER NOT NULL,
  doc_prefix TEXT NOT NULL, query_prefix TEXT NOT NULL,
  center_vec BLOB,              -- corpus mean removed from every vector; NULL = uncentered index
  created TEXT NOT NULL
);
```

Stored vectors are **mean-centered + renormalized** ("all-but-the-top": the corpus
common direction — large here, one book + a shared instruction prefix — is removed);
`center_vec` carries the mean so queries get the identical correction. A pre-centering
index (`center_vec` NULL) still queries, just uncentered.

Notes:
- **`units` is the source of truth; `units_fts` is only an index** — all data
  lives in `units` (`src/docq/store.py:1`). `units_fts` is an external-content
  FTS5 table (`content='units', content_rowid='id'`) mirroring the 5 searchable
  columns.
- The `units_ai` AFTER INSERT trigger keeps the FTS mirror in sync on insert.
  `build_db` also runs `INSERT INTO units_fts(units_fts) VALUES ('optimize')`
  after load (`src/docq/store.py:62`).
- `key_terms` is stored space-joined; `questions` is stored **newline-joined** so
  per-question boundaries survive for the vector channel (one vector per
  question). FTS tokenization is separator-agnostic (`src/docq/store.py:58`).
- Custom tokenizer: `porter unicode61 tokenchars '_'` — Porter stemming, with `_`
  treated as a token char so snake_case identifiers survive.
- BM25 column weights `_WEIGHTS = (10.0, 4.0, 5.0, 8.0, 4.0)` for
  `(text, context_line, applies_when, key_terms, questions)` — verbatim text
  weighted highest (`src/docq/store.py:31`).
- A built `aposd` db has **197 rows** under code-attach segmentation (257 before
  it) across the 6 non-null principle slugs plus empty-principle units; embedded,
  it carries ~1,500 vectors (197 gist + ~1,100 question + ~200 text-chunk) and is
  ~7 MB (`sqlite3 build/minimax-v2.db "SELECT count(*) FROM units"`).
