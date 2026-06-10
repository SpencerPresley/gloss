# aposd-embedded — Design Spec

**Date:** 2026-06-10
**Status:** Draft (pending Spencer's review)
**Author:** Spencer + Claude (brainstorming session)

---

## 1. Summary

Build a **skill-complement retriever**: a tool that, given a software-design situation
(a code snippet, a review finding, a question like "is this module too shallow?"),
returns the most relevant primary-source passages from John Ousterhout's
*A Philosophy of Software Design* — his actual words and examples, with citations.

It complements the existing `software-design-philosophy` Claude Code skill. The skill is a
static distillation (six principles, a paragraph each); this retriever is the **deep
implementation** behind it that holds the whole book and surfaces depth on demand. In
APOSD's own terms: a deep module (the book's wisdom) behind a one-line interface
(`aposd retrieve`), lazily surfaced by the shallow skill. We are building an APOSD-shaped
tool out of APOSD.

The system splits cleanly into **build-time** (offline, once, best model, expensive) and
**query-time** (in Claude Code / atlascyber, repeated, cheap, zero-dependency). Almost
every design decision follows from keeping query-time portable and dependency-free.

## 2. Goals & non-goals

**Goals**
- Turn the book into a **structured, principle-anchored corpus** (not fixed-window chunks).
- Query-time retrieval that is **portable** (drop a folder into `~/work/atlascyber-main`
  and it works), **small** (KB–low-MB), **zero query-time model dependency**, and a
  **native Claude Code fit** (grep/read paradigm, invoked via CLI).
- Extraction quality measured by an **eval set**, not vibes — because extraction quality is
  a one-time build cost but a permanent retrieval ceiling.

**Non-goals (YAGNI)**
- Not a vector database as the core. (Vectors are an *optional, pluggable* rerank backend —
  see §15. At one-book scale, exact search is sub-millisecond anyway; the embedder, not the
  math, is the cost and the portability tax.)
- Not a conversational RAG Q&A bot.
- Not OCR. The full book has a clean text layer (the 20-page `aposd2ndEdExtract.pdf` does
  *not* — it's vector outlines, zero text layer — and is **out of scope**; we use the full
  book, which contains the same content cleanly).
- Not an agentic harness for the build. The build is a deterministic structured-output pass.

## 3. Corpus & the key constraint

- **Corpus:** `resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf`
  — 188 pages, calibre-produced, **real text layer** (~360k extractable chars), embedded
  Unicode fonts. Code is set in its own typeface (`LucidaSans-Typewriter`), so **code blocks
  are detectable by font**. Headings are `NimbusSanL-Bol`. No running headers / page numbers
  (calibre stripped them). Some pages carry figures as embedded JPEGs.
- **First build = Chapter 6** ("General-Purpose Modules are Deeper", PDF pages ~50–58, ~9
  pages) to validate parser + extraction + eval cheaply, **then** the full book with the
  eval-winning model. Ch.6 maps onto the skill's principle #4, so it's a coherent first slice.

## 4. Architecture

```
BUILD (offline, once, best model)               ARTIFACT             QUERY (repeated, zero deps)
─────────────────────────────────              ─────────            ───────────────────────────
PDF ─► PyMuPDF font-aware parse  ─┐                                  ┌─ aposd retrieve "<situation>"
        (deterministic)           │                                 │     [--principle …] [--type …] [-k N] [--json]
   ─► deterministic unit segments ├─► LLM enrich ──► aposd.db ───────┤
        (boundaries + verbatim     │   (minimax,     (one SQLite     └─► FTS5 BM25 + metadata filter
         text fixed here)          │    checkpointed) file, ~KB)          ─► top-k units + citations
   ─► (code units via font)       ─┘
```

**Three stages, one portable artifact.** The artifact is a single self-contained
`aposd.db` (SQLite + FTS5). Copy it anywhere; it opens with the Python stdlib `sqlite3` —
no model, no server, nothing to install or keep warm. That is the portability win, made literal.

## 5. Data model

### The unit

A retrieval unit is a coherent design point. **Boundaries and verbatim text are
deterministic** (set by the parser); the LLM only classifies and generates retrieval fields.

| Field | Source | Purpose |
|---|---|---|
| `text` | **verbatim** (PyMuPDF) | The primary-source passage we return, code fenced. Never LLM-rewritten → provenance + zero hallucination on what's shown. |
| `principle` | metadata (coarse facet) | One of the skill's 6 principles — the filter facet the skill uses. |
| `chapter`, `section`, `page` | metadata (PyMuPDF) | Fine structure + citation; lets us pull adjacent units. |
| `type` | code = deterministic (font); prose subtype = LLM | `definition` / `rationale` / `example` / `code` / `red_flag`. Hard pre-filter by query intent. |
| `context_line` | **LLM** | Situates the unit within its chapter (Anthropic Contextual-BM25). Injects the principle name + situation tokens a bare passage lost. Biggest single recall lever. |
| `questions_this_answers` | **LLM** (3–6, capped) | doc2query: symptom-phrased questions a developer would actually type. Bridges the query↔document vocabulary gap for BM25. |
| `key_terms` | **LLM** | Canonical term + everyday synonyms ("deep module" → "thin wrapper", "leaky abstraction"). BM25 has no semantic layer; we supply it. |
| `applies_when` | **LLM** | One/two-line symptom descriptor — "when this applies" in code-review language, not book language. |

The generated fields exist to boost **lexical** recall (we're BM25-first, not embeddings).
They are indexed in **separate FTS5 columns** so they raise recall without diluting the
verbatim text's ranking.

**Two-facet controlled vocabulary** (so metadata filters work *and* nothing in the book is lost):
- `principle` — **coarse facet, the skill's 6 principles.** What the skill filters on
  (`--principle deep-modules`) and the vocabulary the index aligns to — the tie-back to the skill.
- `topic` / `chapter` / `section` — **fine facet, the book's actual structure** (~21 chapters).
  The book covers far more than the skill distills (*Pull Complexity Downward*, *Different Layer
  Different Abstraction*, *Define Errors Out of Existence*, *Choosing Names*, *Consistency*,
  *Code Should be Obvious*, *Designing for Performance*, …); the fine facet keeps those. The
  retriever should be able to *extend* the skill, not just mirror it.

The coarse facet + the skill's query vocabulary come from a one-time **skill-distillation card**
(§6); the fine facet comes from the book's parsed structure. A **reconciliation** step diffs the
two and flags book topics the skill omits, so fold-into-a-principle vs. add-as-fine-`topic` is a
conscious decision (the plan's first task).

### SQLite / FTS5 schema (validated in research prototype)

```sql
CREATE TABLE units (
    id INTEGER PRIMARY KEY,
    principle TEXT, chapter TEXT, section TEXT, type TEXT, page INTEGER,
    text TEXT,                      -- verbatim primary source (never LLM-rewritten)
    context_line TEXT, applies_when TEXT, key_terms TEXT, questions TEXT,
    enrich_model TEXT,              -- provenance: which model enriched this unit
    needs_enrich INTEGER DEFAULT 0, -- 1 = enrichment skipped after failure; targeted re-run later
    CHECK (type IN ('definition','rationale','example','code','red_flag'))
);

-- External-content FTS5: metadata stays in `units` (single source of truth),
-- index holds no duplicated payload. Kept in sync by triggers.
CREATE VIRTUAL TABLE units_fts USING fts5(
    text, context_line, applies_when, key_terms, questions,
    content='units', content_rowid='id',
    tokenize = "porter unicode61 tokenchars '_'"
);
-- + AFTER INSERT/UPDATE/DELETE triggers; run ('optimize') after bulk load.
```

- **Tokenizer `porter unicode61 tokenchars '_'`**: porter stemming for conceptual recall
  (`configuring` ↔ `configuration`), `_` kept so `num_bytes` stays one token. Known edge:
  bare `config` doesn't stem-match `configuration` — covered by the CLI's prefix-glob rewrite
  (§7). Optional **second `trigram` index** later for partial-identifier matching
  (`change` → `changePosition`); secondary, not default (larger index, no stemming).
- **Ranking:** `bm25(units_fts, …)` with per-column weights — verbatim `text` highest,
  generated fields moderate. Weights tuned on the eval set.

## 6. Build pipeline

A one-time **skill-distillation** precedes the per-book pipeline: reduce `SKILL.md` + references
to a compact `skill_taxonomy` card (~1–2k tokens — the 6 principle slugs + one-line defs + each
principle's characteristic vocabulary, Quick-Diagnostic questions, red-flag phrasings). It defines
the coarse `principle` vocabulary and the *query vocabulary* the index aligns to. A reconciliation
pass diffs it against the book's parsed chapter/section structure and lists topics the skill omits
→ fold-vs-add decided explicitly (the plan's first task). Then, per book:

1. **Parse** — PyMuPDF `page.get_text("dict")`, font-aware. `"Typewriter" in span_font`
   (substring — fonts are subset-prefixed like `AAAAAE+LucidaSans-Typewriter`) → code:
   standalone line → fenced block, inline span → backtick. `NimbusSanL-Bol` + size
   (≥20 chapter, ≥16 section) → heading. Figures → `[FIGURE WxH ext]`, gated to pixel area
   ≥5000 to drop 28×28 decorative icons. No header/footer stripping needed. Deterministic, offline.
2. **Segment** — deterministic unit boundaries from headings + code-block grouping. **Verbatim
   `text` is fixed here.** Code blocks become their own `type='code'` units linked to the
   prose unit they illustrate.
3. **Enrich** — per unit, one structured-output LLM call **with the unit's full section as
   situating context** plus the compact `skill_taxonomy` card. Classifies `type` and the coarse
   `principle` (into the card's slugs); generates `context_line`, `questions_this_answers`,
   `key_terms`, `applies_when`, biased toward the skill's query vocabulary. **Strict separation:**
   substantive content comes *only* from the passage (`temperature=0`, "use only information
   present in the provided text; if unknown, omit"); the card supplies *which principle bucket and
   preferred phrasing*, never facts. The situating context is the unit's **section** (a bounded,
   coherent slice — not the whole chapter), and the call carries a real **system prompt** (role +
   the primary-source/no-paraphrase guardrail + symptom-vocabulary instruction). `num_ctx` is
   **measured** from the real prompts (`build.py`: conservative `chars//3` + headroom, capped + a
   warning, with Ollama's real `prompt_eval_count` logged) — never a guessed constant; it's a
   local-model guardrail since cloud models manage their own context. Each completed unit is
   checkpointed (JSONL) immediately.
4. **Load** — assemble `aposd.db` (units + FTS5 + triggers + optimize).

### Model strategy (`(model, method)`-configurable, eval-driven)

- **Stack:** `langchain 1.3.6`, `langchain-ollama 1.1.0`, `pydantic 2.13.4`, ollama 0.24.
  Use a **Pydantic** schema (auto-validation), `include_raw=True` (log token counts, catch
  parse failures without raising), set `num_ctx` explicitly for long sections (Ollama
  silently truncates otherwise).
- **Method branches by model** — this is load-bearing and was found empirically:
  | Model | Method | Notes |
  |---|---|---|
  | `minimax-m3:cloud` | `function_calling` | **Ignores** Ollama `format=`/json_schema (emits prose); works via tool-calling; best output in smoke test. 1M ctx, US-based, zero data retention. |
  | `gpt-oss:120b-cloud` | TBD (test at build) | Cloud variants untested for method support. Alternative **primary**, not a fallback. |
  | `gpt-oss:20b` (local) | `json_schema` | Default (honors `format=`), free, deterministic, schema-valid. **Dev/iteration model** — explicit `--model` choice, never an auto-fallback. |
  | `llama3.1:8b` (local) | `json_schema` | Faster, weaker. Optional. |
- **Primary = eval winner.** During the Ch.6 build, run candidate cloud primaries
  (`minimax-m3:cloud`, `gpt-oss:120b-cloud`) and compare retrieval quality on the eval set.
  Lock the winner for the full-book run.
- **One model per build — no silent fallback.** Mixing models within a corpus build bakes
  inconsistent quality into the artifact permanently (some units great, some mediocre, and you
  can't tell which) — the opposite of quality-in/quality-out. The cloud quota is plan-level
  (caps *all* `:cloud` models at once), so on a cap the build **checkpoints and stops** with a
  resume hint; you wait for the reset and `aposd build --resume` with the *same* model. A
  one-time offline build can afford to wait; a permanent artifact can't afford mixed quality.
  `gpt-oss:20b` stays an explicit `--model` choice for dev/iteration, never an automatic
  mid-run downgrade. (Removing the fallback also removes a murky auto-policy — APOSD §4: a
  config knob that papers over an undecided behavior. We decided: one model, checkpoint-and-wait.)

### Extraction interface (decoupled from Ollama)

The pipeline never imports LangChain or Ollama. It depends on a one-method seam:

```python
class StructuredExtractor(Protocol):
    def extract(self, prompt: str, schema: type[BaseModel], *, system: str | None = None) -> BaseModel: ...
```

Exactly **one** adapter is built now — `OllamaExtractor`. It **auto-discovers** the structured-output
method: tries `json_schema` (Ollama's grammar-constrained `format=`), falls back to `function_calling`
if the model ignores the schema (minimax emits prose), and **pins** whatever works. No per-model lore,
no brittle `:cloud` heuristic — unknown models self-resolve (cost: one wasted probe on the first unit,
then pinned). An optional `method=` arg skips the probe when the method is already known; `num_ctx` is
passed in (measured by the orchestrator). The pipeline calls `extractor.extract(...)` and knows nothing
about models. Near-zero cost (the adapter is code we write anyway), three payoffs:
- **Testability now:** unit-test the whole enrichment pipeline against a deterministic *stub*
  extractor — no model calls.
- **No bet on `with_structured_output`:** our interface promises structured extraction; *how* an
  adapter delivers it is its own business, so a future provider lacking LangChain's
  `with_structured_output` is a different adapter, not a refactor.
- **Swap/add a provider later = one new adapter file**, zero pipeline change.

APOSD §6 applied to ourselves: a somewhat-general interface over a special-purpose (Ollama)
implementation — we build one adapter, not a provider zoo (YAGNI).

## 7. Query CLI (`aposd`)

- `aposd retrieve "<situation>" [--principle P …] [--type T …] [-k N] [--json]`
- `aposd build [--chapter N] [--model M] [--resume]`
- `aposd eval [--cases eval/cases.yaml]`

**Critical query-rewrite (research-confirmed):** never feed raw natural language to FTS5
`MATCH` — it's an implicit AND of every token, so a real sentence returns **zero rows**.
The CLI tokenizes, drops ≤2-char tokens, and OR-joins with prefix globs:

```python
fts = " OR ".join(f"{t}*" for t in re.findall(r"[A-Za-z0-9_]+", text) if len(t) > 2)
```

Then BM25 + metadata filters in one SQL statement:

```sql
SELECT bm25(units_fts, /*weights*/) AS score, u.principle, u.section, u.type, u.page,
       u.text, snippet(units_fts, 0, '[', ']', ' … ', 8) AS hit
FROM units_fts JOIN units u ON u.id = units_fts.rowid
WHERE units_fts MATCH ? AND u.type IN (…) AND u.principle IN (…)
ORDER BY score LIMIT ?;
```

**Output:** ranked units, each with a citation (`principle §section p.N`), the verbatim
text, and a match snippet. `--json` emits structured results for the skill to consume.

**Query-time depends on the Python stdlib only** (`sqlite3`, `argparse`, `re`). "Drop it
into atlascyber" = copy `aposd.db` + one script, zero `pip install`. Build-time deps
(`pymupdf`, `langchain-ollama`) are dev-only and never imported on the query path.

## 8. Eval harness

- `eval/cases.yaml`: design situations → expected principle / section / unit.
- **Seed cases from the skill** — its Quick-Diagnostic questions and Common-Mistakes are a
  ready-made bank of the real queries the skill will issue; reuse them as eval cases.
- `aposd eval` reports **top-k hit-rate / MRR**.
- Run after every build. This is how we (a) answer "is minimax worth it over gpt-oss" with
  numbers, (b) tune BM25 column weights and tokenizer, and (c) catch bad extraction quality
  *before Claude ever sees it*. Anthropic's repeated advice, adopted: always run evals.

## 9. Error handling & resilience

- **Checkpoint / resume:** each unit persisted to JSONL as generated + a completed-sections
  manifest. A cloud cap, rate-limit, or network blip resumes from the last unit (not a
  restart). A full run can span multiple usage-resets. Re-runs only re-extract changed sections.
- **No automatic model fallback (uniform quality).** A quota cap → checkpoint + stop + `--resume`
  later with the *same* model. Local models are an explicit dev choice, never a silent mid-run downgrade.
- **Per-unit failure** (refusal / malformed after a few retries) → log + skip: the unit keeps its
  deterministic verbatim `text` with empty generated fields, flagged `needs_enrich` for a targeted re-run.
- **Schema validation:** Pydantic + `include_raw`; malformed output is logged and
  retried/skipped — never written to the db.
- **Provenance:** verbatim `text` is never LLM-rewritten, so a weak model degrades *recall*,
  not the correctness of what's returned.

## 10. Testing

- **Parser unit tests:** code/heading/figure detection on known pages (e.g. the
  `changePosition` / `text.delete` code on PDF p52–53; Ch.6 heading sizes).
- **Query-rewrite tests:** the implicit-AND bug — a raw sentence must return results after rewrite.
- **FTS5 build + filter tests:** BM25 ordering + metadata filter applied in-query.
- **Eval set:** retrieval quality (separate from correctness unit tests).

## 11. Project layout & tooling

- **uv** (not pip), workspace-style (not a published package). Build-time and query-time
  dependency groups kept separate so the query path stays stdlib-only.
- Sketch:
  ```
  aposd-embedded/
    pyproject.toml              # uv; [build] deps: pymupdf, langchain, langchain-ollama, pydantic, pyyaml
    src/aposd/                  # ENGINE — corpus-agnostic
      parse.py                  # PyMuPDF font-aware extraction; parse_pdf(path, ..., profile)
      segment.py                # deterministic units; segment(elements, profile)
      extract.py                # StructuredExtractor protocol + StubExtractor (provider-agnostic)
                                #   + OllamaExtractor adapter (Ollama lore encapsulated in the class)
      enrich.py                 # enrichment pipeline (depends only on the protocol; checkpointed)
      store.py                  # SQLite/FTS5 build + query (stdlib only)
      cli.py                    # argparse; retrieve / build / eval
    corpora/aposd/              # INSTANCE — everything APOSD-specific
      profile.py                # ParseProfile: fonts, sizes, figure-area, section regex, pages, corpus path
      taxonomy.yaml             # the 6 principles (coarse) + book topics (fine)
      prompt.md                 # enrichment system prompt + user template
      cases.yaml                # eval cases
      source.pdf                # the corpus (gitignored)
    tests/
    aposd.db                    # built artifact for this instance
  ```
- **git:** local repo, no remote yet. `.env` gitignored (no secrets expected; Ollama cloud
  auth is `ollama signin`). PDFs gitignored (large + copyrighted).
- **Distribution:** the db is **built once, never at deploy.** SQLite + FTS5 ship in the Python
  stdlib and a `.db` is byte-portable across OS / CPU arch / SQLite versions, so deploying = copy
  the file. For packaging, bundle the prebuilt `aposd.db` as **package data** (read via
  `importlib.resources`) with build deps behind an optional `[build]` extra → `uvx aposd retrieve
  "…"` runs with zero setup, index baked in. (FTS5 is in ~all CPython SQLite builds; if ever
  absent, a pure-Python BM25 over the same `units` table is a drop-in fallback — FTS5 is only an index.)

## 12. Skill integration (designed-for, deferred)

The skill is **not edited yet** (per Spencer). The CLI is built to be invoked by it later:
a SKILL.md instruction like *"for the primary-source passage on principle X, run
`aposd retrieve --json …`"*. CLI-over-MCP is deliberate — a CLI costs zero standing context
(MCP loads tool schemas into every session and needs a process), composes with
skills/subagents, and is portable into atlascyber (any agent shells out).

## 13. Decisions locked

1. **Deliverable:** skill-complement retriever (not bake-off, not Q&A bot, not extraction-only).
2. **Retrieval core:** structured text + CLI, lexical (BM25) + metadata. Vectors optional/pluggable.
3. **Build harness:** deterministic `ChatOllama.with_structured_output()`, section by section.
4. **First build:** Chapter 6, then full book.
5. **Segmentation:** deterministic boundaries; LLM only enriches; verbatim text never LLM-touched.
6. **Model:** eval-driven primary (`minimax-m3:cloud` vs `gpt-oss:120b-cloud`), method branched
   by model. **One model per build — no automatic fallback;** a quota cap → checkpoint + wait +
   `--resume` (uniform quality over convenience). Local `gpt-oss:20b` is an explicit dev/iteration model.
7. **Store:** SQLite FTS5, single portable file, stdlib-only at query time.
8. **Extraction interface:** one-method `StructuredExtractor` seam (+ optional `system`), kept free of
   provider imports; the sole `OllamaExtractor` **auto-discovers** the method (try `json_schema` → fall
   back to `function_calling`, pin the winner; optional override) with its method list as a private class
   attribute. Stub-testable; no bet on `with_structured_output` being universal.
9. **Distribution:** deferred to a future session (documented in §11, not built now).
10. **Engine vs instance:** corpus-agnostic engine (`src/aposd/`) vs the APOSD instance (`corpora/aposd/`:
    `ParseProfile`, taxonomy, prompt, cases, source PDF). Profile/taxonomy/prompt/corpus-path are loaded
    inputs, never hardcoded in engine logic. A second corpus = a new dir, not a refactor.
11. **Context sizing:** each unit is situated in its **section** (bounded); `num_ctx` is measured from real
    prompts (`chars//3` conservative, capped + warned, real `prompt_eval_count` logged), not guessed. Local-only.
12. **Docstrings:** Google-style on every public module/class/function (APOSD interface comments).

## 14. Research findings & sources (grounding)

- **PDF:** PyMuPDF `get_text("dict")` wins; font signals perfectly discriminative on this
  PDF. marker/docling rejected (heavy ML, no gain on a text-native PDF). pypdfium2 reports
  wrong font sizes; pdfplumber works but is slower.
- **LLM structured output:** `json_schema` is the `langchain-ollama 1.1.0` default and uses
  Ollama's grammar-constrained `format=`; reliable on local models, **ignored by
  `minimax-m3:cloud`** (use `function_calling` there). `function_calling` flaky on the local
  8B model.
- **Retrieval substrate:** SQLite FTS5 is the only candidate that is both zero-dependency and
  supports same-query metadata filtering; pure-Python BM25 libs force combinatorial
  pre-partitioning or lossy post-filtering.
- **Unit design (lexical-first):**
  - Anthropic, *Contextual Retrieval* — prepended context cuts retrieval failures, **including
    BM25** — https://www.anthropic.com/news/contextual-retrieval
  - doc2query/docT5query — generated "questions this answers" reweight key terms + add
    synonyms for lexical search —
    https://github.com/castorini/anserini/blob/master/docs/experiments-doc2query.md
  - *Dense X Retrieval* (EMNLP 2024) — **don't propositionize**; proposition gains are
    factoid-specific and break on multi-hop reasoning (design arguments are multi-hop) —
    https://arxiv.org/abs/2312.06648
  - Elastic, *Practical BM25 Part 2* — term-frequency saturation + length normalization →
    no keyword stuffing, keep generated fields short —
    https://www.elastic.co/blog/practical-bm25-part-2-the-bm25-algorithm-and-its-variables

## 15. Open questions / future work

- **Pluggable vector rerank:** keep the retriever interface `search(query, *, principles,
  types, k) -> [Unit]` unchanged; add reranking as an optional post-hook
  (`rerank(query, candidates)`, default identity) in a lazily-imported module. Precomputed
  unit vectors can live as a BLOB in the *same* `aposd.db`, so the artifact stays one file
  and the query hot-path never imports an embedder. Add only if the eval shows lexical recall
  is insufficient.
- **`trigram` secondary index** for partial-identifier matching — add if code-identifier
  queries underperform.
- **Taxonomy reconciliation** (the plan's first task) — diff the skill card vs. the book's parsed
  structure to lock the two-facet (`principle` coarse / `topic` fine) vocabulary and decide
  fold-vs-add for the book topics the skill omits.
- **Whether to commit the built `aposd.db`** as a shippable deliverable vs. always rebuild.
