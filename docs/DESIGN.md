# Design — Decisions & Tradeoffs

The *why* behind gloss. Each entry: the decision, the rationale, and a pointer to the
spec/plan/note section with the full argument. This is a curated index, not a re-derivation —
read the linked source for the complete reasoning.

Sources distilled (all under `docs/superpowers/`):

| Path | What it is |
|---|---|
| `specs/2026-06-10-aposd-embedded-design.md` | the authoritative design spec (§1–§15) |
| `plans/2026-06-10-aposd-embedded.md` | Ch.6-slice implementation plan (Tasks 0–10) |
| `plans/2026-06-10-aposd-full-book-build.md` | full-book build plan (Tasks 1–7) |
| `notes/2026-06-10-naming.md` | engine name decision + alternatives |
| `notes/2026-06-10-taxonomy-reconciliation.md` | two-facet vocabulary + chapter gap list |
| `notes/2026-06-10-corpus-generation-prompts.md` | reusable taxonomy/enrichment prompts |
| `notes/2026-06-10-session-handoff.md` | Ch.6 slice handoff (model findings) |
| `notes/2026-06-10-full-book-build-handoff.md` | full-book handoff (A/B result, artifacts) |

---

## Framing

**The retriever is itself an APOSD-shaped tool, by design.** gloss is the *deep module* (the whole
book's wisdom) behind a one-line interface (`gloss retrieve`), lazily surfaced by a *shallow* skill.
We are building an APOSD-shaped tool out of APOSD — the spec uses the book's own vocabulary to
justify the architecture. (spec §1)

**Almost every decision follows from one constraint: keep query-time portable and
dependency-free.** The system splits into build-time (offline, once, expensive, best model) and
query-time (repeated, cheap, zero-dependency). When a tradeoff arises, query-time portability wins.
(spec §1, §2)

---

## Core decisions

### 1. Single-file SQLite/FTS5 artifact + stdlib-only query path

The corpus is one self-contained `.db` (SQLite + FTS5). Copy it anywhere; it opens with the Python
stdlib `sqlite3` — no model, no server, nothing to install or keep warm. "Drop it into another repo"
literally means copy the `.db` + one script, zero `pip install`. A `.db` is byte-portable across OS /
CPU arch / SQLite version, so deploying = copy the file; it's built once, never at deploy.
(spec §4, §7, §11)

> Why FTS5 specifically: it is the only candidate that is *both* zero-dependency *and* supports
> metadata filtering in the same query. Pure-Python BM25 libraries force combinatorial
> pre-partitioning or lossy post-filtering. FTS5 is in ~all CPython SQLite builds; if ever absent, a
> pure-Python BM25 over the same `units` table is a drop-in fallback — FTS5 is only an index, all
> data lives in the plain `units` table. (spec §11, §14)

### 2. Lexical (BM25) first, not embeddings

Retrieval is BM25 + metadata filter, not a vector database. At one-book scale exact search is
sub-millisecond anyway; the embedder — not the math — is the cost and the *portability tax* (it would
break the zero-install query path). Vectors are kept as an *optional, pluggable* rerank backend
(precomputed unit vectors could live as a BLOB in the same `.db`, behind a lazily-imported
`rerank(query, candidates)` hook defaulting to identity), added only if eval shows lexical recall is
insufficient. (spec §2, §15)

### 3. Deterministic segmentation; the LLM only generates retrieval metadata

Unit boundaries and verbatim `text` are set deterministically by the parser/segmenter; the LLM never
touches the returned text. It only *classifies* (`type`, originally `principle`) and *generates*
retrieval fields (`context_line`, `questions`, `key_terms`, `applies_when`). Consequence:
what comes back is always the source's own words — provenance and zero hallucination on what's shown —
and a weak model degrades *recall*, not the correctness of returned text. (spec §5, §6.2, §9; README "How it works")

### 4. Generated fields exist to lift *lexical* recall, indexed separately

The generated metadata targets known BM25 weaknesses, grounded in retrieval research:
- `context_line` — Anthropic Contextual-BM25: a prepended context line injects the principle name +
  situation tokens a bare passage lost. Called the biggest single recall lever.
- `questions` (the spec calls it `questions_this_answers`) — doc2query: symptom-phrased questions
  bridge the query↔document vocabulary gap.
- `key_terms` — canonical term + everyday synonyms, since BM25 has no semantic layer.

They are indexed in **separate FTS5 columns** so they raise recall without diluting the verbatim
`text`'s ranking. Deliberately **not** propositionized (per *Dense X Retrieval*: proposition gains
are factoid-specific and break on the multi-hop reasoning that design arguments require), and kept
**short** (BM25 term-frequency saturation + length normalization penalize keyword stuffing).
(spec §5, §14; note `corpus-generation-prompts.md` §2)

### 5. The `StructuredExtractor` seam — provider decoupling + testability

The build pipeline depends only on a one-method Protocol (`extract(prompt, schema, *, system)`); no
pipeline module imports LangChain or Ollama. Exactly **one** adapter exists today (`OllamaExtractor`).
Three payoffs: (a) the whole enrichment pipeline is unit-testable against a deterministic
`StubExtractor` with no model calls; (b) no bet that `with_structured_output` is universal — a future
provider is a new adapter, not a refactor; (c) swap/add a provider = one new file, zero pipeline
change. APOSD §6 applied to ourselves: a somewhat-general interface over a special-purpose (Ollama)
implementation — one adapter, not a provider zoo (YAGNI). (spec §6 "Extraction interface", §13 #8)

### 6. Auto-discover the structured-output method (`json_schema` → `function_calling`), then pin it

`OllamaExtractor` tries `json_schema` (Ollama's grammar-constrained `format=`), falls back to
`function_calling` if the model ignores the schema, and pins whatever works. This was found
empirically and is load-bearing: `minimax-m3:cloud` **ignores** Ollama `format=` and emits prose, so
it only works via tool-calling; local `gpt-oss:20b` honors `json_schema`. Auto-discovery removes
per-model lore and brittle `:cloud` heuristics — unknown models self-resolve, at the cost of one
wasted probe on the first unit. (spec §6, §14; `extract.py:48`)

> Hard-won model lore (see handoffs): `devstral-small-2:24b-cloud` works via `function_calling`
> (cheap, good quality — built the corpus); `minimax-m3:cloud` works via `function_calling` (quality
> candidate); `gpt-oss:20b-cloud` **fails both methods** and must not be used — the cloud variant
> behaves differently from local `gpt-oss:20b`. (`notes/2026-06-10-session-handoff.md`)

### 7. Two-facet controlled vocabulary: coarse `principle` (closed set) vs fine `topic`/chapter

Every unit carries two facets so metadata filters work *and* nothing in the book is lost:
- **`principle`** — COARSE, a *closed set* of the skill's 6 principles. What callers filter on
  (`--principle deep-modules`); the vocabulary the index aligns to; the tie-back to the skill.
- **`topic`/`chapter`/`section`** — FINE, the book's actual 21-chapter structure. The book covers far
  more than the skill distills, so the fine facet preserves everything the coarse facet has no clean
  home for. The retriever can *extend* the skill, not just mirror it.

A one-time **reconciliation** diffs the skill card against the parsed structure and flags the gaps:
9 of 19 design chapters map cleanly; 3 are folded (ch.7→info-hiding, ch.8→general-purpose,
ch.9→deep-modules); 7 have no coarse home and are indexed `principle: null` (still searchable by
text/topic). The closed-set decision means "fold" never adds a 7th principle. Promotion candidates if
ever relaxed: *Choosing Names*, *Define Errors Out Of Existence*, *Design it Twice*.
(spec §5; `notes/2026-06-10-taxonomy-reconciliation.md`)

### 8. Coarse `principle` set from the taxonomy at build time, never trusted from the LLM

The LLM is asked for a `principle` slug, but `run_build` **overrides** every row's principle with the
chapter's taxonomy slug (or `""` for null) — `build.py:118`. This fixes a real pollution: for
`null`-principle chapters the empty card let the LLM invent ~35 one-off slugs, breaking the closed
set. The coarse facet is now guaranteed closed: the 6 slugs + empty. (Consequence: the LLM
`Enrichment.principle` field is now dead weight — generated then always discarded — and its removal is
a tracked follow-up.) (`notes/2026-06-10-full-book-build-handoff.md` task 7 + gap 2; `enrich.py:25`)

### 9. Enrichment discipline: passage-only facts, card as palette, measured `num_ctx`

Each unit is enriched by one structured-output call situated in its **section** (a bounded coherent
slice, not the whole chapter) plus the compact principle card. Strict separation: substantive content
comes *only* from the passage (`temperature=0`, "use only information present; if unknown, omit"); the
card supplies *which principle bucket and preferred phrasing*, never facts. The card's
vocabulary/red-flags are a **palette** — prefer where it fits, do not stuff every term (BM25
saturation penalizes padding). `num_ctx` is **measured** from the real prompts (conservative
`chars//3` + headroom, floored and capped with a warning, real `prompt_eval_count` logged) — never a
guessed constant — because Ollama silently truncates otherwise. It's a local-model guardrail; cloud
models manage their own context. (spec §6; `notes/2026-06-10-corpus-generation-prompts.md` §2)

### 10. One model per build — no silent fallback

Mixing models within a build bakes inconsistent quality into a permanent artifact (some units great,
some mediocre, no way to tell which) — the opposite of quality-in/quality-out. The cloud quota is
plan-level (caps *all* `:cloud` models at once), so on a cap the build **checkpoints and stops** with
a resume hint; you wait for the reset and `--resume` with the *same* model. A one-time offline build
can afford to wait; a permanent artifact can't afford mixed quality. Local models stay an explicit
`--model` choice for dev/iteration, never an automatic mid-run downgrade. Removing the fallback also
removes a murky auto-policy (APOSD §4: a config knob papering over an undecided behavior — here, we
decided). (spec §6, §9, §13 #6)

### 11. Checkpoint / resume + bounded concurrency

Each completed unit is persisted to per-chapter JSONL immediately, with a key derived from
section + is-code + text prefix. A cloud cap, rate-limit, or network blip resumes from the last unit,
not a restart — a full run can span multiple usage resets. Resume excludes `needs_enrich=1` rows and
dedups by key (last-wins), so a cap that wrote empty data is cleanly re-enriched on `--resume` instead
of being baked in. Concurrency (`--workers`, a thread pool) was added *after* a correct serial build
existed: the method is pinned by a serial warmup before the pool, checkpoint writes are lock-guarded,
and `max_workers=1` is the exact serial path (backward compatible). The minimax full build used <7% of
the 5-hour budget at `--workers 8`. (spec §6, §9; `plans/2026-06-10-aposd-full-book-build.md` task 6;
`notes/2026-06-10-full-book-build-handoff.md`)

> Per-unit failure (refusal / malformed after retries) → log + skip: the unit keeps its deterministic
> verbatim `text` with empty generated fields, flagged `needs_enrich=1` for a targeted re-run.
> Pydantic + `include_raw` means malformed output is logged and never written to the db. (spec §9)
>
> Operational detail (checkpoint format, paths, `--build-dir` naming, the 0-byte-db gotcha) lives in
> docs/BUILDS.md — not repeated here.

### 12. Eval-driven model selection

Extraction quality is a one-time build cost but a *permanent retrieval ceiling*, so it's measured with
an eval set (top-k hit-rate), not vibes. Cases are seeded from the skill's Quick-Diagnostic and
Common-Mistakes tables — a ready-made bank of the real queries the skill will issue. Eval runs after
every build to (a) answer "is the expensive model worth it" with numbers, (b) tune BM25 weights, and
(c) catch bad extraction *before Claude ever sees it*. (spec §2, §8)

> **A/B result (historical handoff figures; not reproducible from the current tree):** the handoff
> recorded devstral 0.75 (12/16) vs minimax 0.81 (13/16) — minimax fixing the systematic
> abstract-principle (complexity) recall gap but regressing 2 concrete cases, +1 net on 16, within
> noise. **Caveat:** the in-repo `build/minimax.db` evals to **0.75 (12/16)** today, *not* 0.81 — the
> 0.81 was scored against a since-superseded/renamed db, and devstral's db (`build/aposd.db`) is now a
> 0-byte stub, so the +1/16 lead can't be reproduced from what's checked in. minimax is the leaning
> primary (and the CLI default), but the lock is deferred until the eval set is strengthened and both
> dbs are rebuilt and re-scored. (`notes/2026-06-10-full-book-build-handoff.md`)

### 13. Engine/instance split — corpus-agnostic engine, per-book instances

The engine (`src/gloss/`) is corpus-agnostic; everything APOSD-specific lives in `corpora/aposd/`
(`profile.py`, `taxonomy.yaml`, `prompt.md`, `cases.yaml`, the source PDF). Profile / taxonomy /
prompt / corpus-path are *loaded inputs*, never hardcoded in engine logic. A second corpus is a new
directory, not a refactor. Reinforced by dynamic chapter detection: `Profile.chapter_re`
(`r"^Chapter\s+(\d+)"`) + `split_chapters()` replaced hardcoded page ranges, so onboarding a new
clean-fonted book needs no page-measuring — `chapter_pages` is demoted to an optional override.
Generality budget cap: one regex default + one override escape hatch, no speculative multi-strategy
detection. (spec §13 #10; `plans/2026-06-10-aposd-full-book-build.md` task 1 + "Decisions locked")

### 14. CLI over MCP for skill integration

The query interface is a CLI invoked by shelling out, not an MCP server. A CLI costs zero standing
context (MCP loads tool schemas into every session and needs a running process), composes with
skills/subagents, and is portable into any repo (any agent can shell out). The skill is *designed-for*
but **not edited yet** — wiring it to call `gloss retrieve --json` is a deferred task. (spec §12)

### 15. The PDF and the built `.db` are not distributed

Corpus PDFs are large + copyrighted; built dbs are regenerable. Both are gitignored
(`resources/*.pdf`, `build/`, `*.jsonl`). The engine and tooling are MIT-licensed; the taxonomy and
the skill are distillations of Ousterhout's book kept as development input. Building a corpus requires
your own copy of the source. Whether to ship a prebuilt db as package data (`importlib.resources`,
`uvx gloss retrieve`) is a deferred distribution decision. (spec §11; README "Provenance & license";
`.gitignore`)

### 16. The name "gloss" is a working title

A *gloss* is an explanatory note attached to a passage — exactly what the engine adds to verbatim
passages (`context_line`, `applies_when`, `key_terms`). It may change. Alternatives kept on record:
*florilegium* (most precise for the artifact, but long), *concordance*, *lectern*, *vade*, *cite*. The
APOSD instance stays `corpora/aposd/` and its artifact `aposd.db` regardless.
(`notes/2026-06-10-naming.md`; README status line)

---

## Status & known limitations (from README + handoffs)

gloss is **early** and a **working prototype, not a finished product**. The full APOSD corpus builds
end-to-end and retrieval returns sensible cited passages, but:

- The eval set is **16 cases** — too thin to confidently lock a build model. Strengthening it
  (section-level, cross-principle, disambiguated cases) is the top follow-up.
- Real-world usefulness **hasn't been battle-tested**.
- BM25 column weights (`_WEIGHTS` in `store.py`) are untuned defaults, to be tuned on a stronger eval.
- The redundant LLM `principle` field should be dropped (generated then always overridden, §8 above).
- The FTS trigger is insert-only (assumes wholesale rebuild); incremental writes would need
  UPDATE/DELETE triggers.
- Chapters with no level-2 headings collapse to one prose unit (text still searchable; sub-splitting
  long runs is a deliberate-for-now non-goal).

(README status line; `notes/2026-06-10-full-book-build-handoff.md` "Known gaps")

---

## Explicit non-goals (YAGNI)

From spec §2 — gloss is deliberately **not**: a vector database as the core (vectors are optional
rerank only); a conversational RAG Q&A bot; an OCR pipeline (the full book has a clean text layer; the
20-page vector-outline extract is out of scope); an agentic build harness (the build is a
deterministic structured-output pass).
