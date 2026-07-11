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

### 2. Lexical (BM25) core, hybrid vector channel added on evidence (2026-07-10)

Retrieval began as BM25 + metadata filter only, with vectors reserved as an optional backend "added
only if eval shows lexical recall is insufficient" (spec §2, §15). That evidence arrived: live
probing (an external session querying the corpus cold) reproduced the predicted failure class — a
situation-phrased query ("wrap every call in try/catch… messy") whose correct passage (§10.7
exception aggregation) ranked #3 behind a common-word collision on "messy", unfilterably. The eval
set was extended to 31 rank-sensitive cases first; lexical scored hit@5=0.84 / hit@1=0.55 / MRR=0.66
on the fixed-segmentation corpus.

The channel that was added differs from the reserved design in one important way: **parallel
channels fused with RRF, not a rerank hook** — a reranker over BM25's candidates can't recover a
passage BM25 never surfaced (a recall failure), only reorder ranking failures. Shape:

- `gloss embed` post-pass: per-unit vectors (a *gist* of the generated metadata, one vector **per
  generated question** — doc2query made dense, so a query matches a question by meaning rather than
  shared tokens — and chunked verbatim text) stored as float32 BLOBs in the **same** `.db`
  (embeddinggemma via local Ollama, 768-dim, ~1.5k vectors, ~16s, +5 MB).
- Query time: BM25 and max-cosine run as independent channels; reciprocal rank fusion promotes
  units both like, with **rrf_k=20** rather than the literature's 60 — that constant was sized for
  web-scale lists, and on a ~200-unit corpus it over-smooths: a common-word query gives dozens of
  units mediocre dual-channel ranks that sum past one channel's emphatic #1 (observed with ch11's
  unit at semantic #1, 0.69-vs-0.59 cliff, diluted out of top-5 at rrf_k=60).
- Every hybrid hit carries per-channel ranks (`via lex#1+sem#2`). The channels' errors are
  decorrelated (BM25 misses vocabulary, vectors miss terse exact-term queries), so agreement is a
  *visible* confidence signal — this directly answers the live-probe critique that the tool was
  "unreliable in a way that's invisible at query time".

Result: hybrid hit@5=0.94 / hit@1=0.71 / MRR=0.80 (vs 0.77/0.52/0.60 where the session started;
the hit@1 jump from 0.61 came from mean-centering the vectors — see the experiment log below).
What the original decision protected survives intact: packages stay stdlib-only; the *lexical* path
still needs nothing installed or running; the portability tax is confined to an optional mode that
degrades back to lexical (silently for a vector-less db, with a stderr note when vectors exist but
the embedder is down); and vectors travel inside the one portable file. Deliberately refused:
query-time LLM rerank, ANN/vector-db dependencies (brute-force `math.sumprod` over ~1.5k vectors is
milliseconds), and query expansion. (spec §2, §15; `store.py`, `vectors.py`; eval numbers in
BUILDS.md)

### 3. Deterministic segmentation; the LLM only generates retrieval metadata

Unit boundaries and verbatim `text` are set deterministically by the parser/segmenter; the LLM never
touches the returned text. It only *classifies* (`type`, originally `principle`) and *generates*
retrieval fields (`context_line`, `questions`, `key_terms`, `applies_when`). Consequence:
what comes back is always the source's own words — provenance and zero hallucination on what's shown —
and a weak model degrades *recall*, not the correctness of returned text. (spec §5, §6.2, §9; README "How it works")

> **Boundary rule amended 2026-07-10: code travels with its lead-in prose.** Originally every code
> block was its own unit, which returned examples stripped of the sentence that explains them — a
> book's code is not usable in isolation — and let inline code spans the parser misread as blocks
> shatter sentences into fragments (a real unit whose entire text was "The"). Now a code block merges
> into the prose run that introduces it (prose after the block starts fresh), and a one-line
> code element with no code punctuation folds back into the sentence. 257 units became 197; the
> checkpoint keys (`sha1(section|is_code|text)`) meant only the 59 changed units re-enriched.
> Boundaries remain fully deterministic. (`segment.py`; census + eval delta in BUILDS.md)

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

> Since the hybrid channel (2026-07-10), the same generated fields do double duty: the gist
> (`context_line` + `applies_when` + `key_terms`) is embedded as one vector and each question as its
> own vector — live probing showed a generated question lexically anticipating a query was the
> single strongest situation-matcher, and per-question embeddings generalize exactly that mechanism
> to paraphrases with zero shared tokens. `questions` is stored newline-joined so the boundaries
> survive. (`vectors.py:_unit_jobs`)

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
skills/subagents, and is portable into any repo (any agent can shell out). The skill is wired
(2026-07-10): its "Consulting the Source" section queries `gloss retrieve --compact` when the corpus
db exists and expands runner-up previews with `gloss show <id>`, falling back to the bundled
references when it doesn't. The steering (symptom phrasing, channel-tag trust rules, never answering
from a preview paraphrase) is encoded once in the skill instead of per-use by the user. (spec §12)

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

## Retrieval experiment log (2026-07-10)

Every knob change goes through `corpora/aposd/cases.yaml` (n=31) and, since this session,
`gloss eval --mode A --vs B` — a paired sign-flip randomization test on per-case reciprocal rank
(`evalrun.py:paired_sign_flip`). House rule: **one case ≈ 3 points, so a delta under ~2 cases is
noise no matter how good the story is** — adopt on p-value, or on measured mechanism + zero
regressions, and record which.

Measured diagnostics on the embedded corpus (1,493 × 768 vectors):

- **Anisotropy is large.** Corpus mean-vector norm 0.67; mean pairwise doc-doc cosine 0.45. One
  book plus a shared instruction prefix puts a big common direction under every similarity,
  compressing contrast.
- **Question vectors carry the semantic channel**: they win the per-unit max-sim for ~80% of
  top-5 hits (gist ~8%, text chunks ~12%) — dense doc2query is the load-bearing piece.
- **Hubness and max-sim multiplicity are mild**: the worst hub unit appears in 4/31 top-5s;
  #vectors-per-unit vs top-5 appearances correlates at r=0.15. No correction warranted at this
  corpus size.

**Adopted:**

| change | eval effect | verdict basis |
|---|---|---|
| Mean-centering ("all-but-the-top" k=1): docs centered+renormalized at embed, mean stored in `vectors_meta.center_vec`, query centered identically | hybrid hit@1 0.61→0.71, MRR 0.74→0.80; centered-vs-raw is 3 better / **0 worse** / 28 unchanged (p=0.25 alone); pushed hybrid-vs-lexical from p=0.27 to **p=0.037** | measured mechanism + strictly monotone improvement; standard practice (*all-but-the-top*, Mu & Viswanath 2018) |
| rrf_k=20 (vs the literature's 60) | +1 case hit@5, nothing worse | mechanism (short-list dilution, §2), explicitly *not* a p-value |
| Opt-in gated LLM rerank (`--rerank`, `rerank.py`): listwise judgment of the top-5, **skipped when fusion's #1 is dual-backed** (lexical #1 + semantic ≤5) | with `gemma4:31b-cloud`: hit@1 0.71→**0.84**, MRR 0.80→**0.88** (p=0.13), 4 promotions / **0 demotions-from-#1**, ~0.9s per gated call (~⅓ of queries skip the call); local-default `gemma4:e2b` gated: 24/31, 0 demotions | mechanism: every reranker tried (gemma 2B/31B, minimax, glm) demoted the *same* dual-backed #1s while its wins came from weakly-backed ones — two agreeing channels beat one model's read, so the gate lets the model judge only the uncertain cases. Gate policy was *selected* on the 31-case set (5 candidates compared) → **revalidate on the expanded eval**. Prompt is corpus-agnostic with a keep-given-order clause (damps near-tie churn) and a `--rerank-prompt` per-corpus override; follow-up idea on record: store a corpus-tuned rerank prompt in the db at build time. Fallback contract: any failure keeps the original order. |

**Rejected after measurement:**

| change | eval effect | note |
|---|---|---|
| Stopword filter in the lexical OR-expansion | Δmrr +0.004, p=1.00 (one case 5→3) | BM25's IDF already neutralizes function words; a hardcoded English stoplist in a corpus-agnostic engine is a smell anyway. And never strip stopwords before *embedding* — transformer embedders want natural sentences. |

**Ruled out as wrong-scale** (don't relitigate without a much bigger corpus): ANN / graph-ANN /
HNSW / fuzzy kNN — approximations of the exact brute-force kNN we already do in ~3 ms over 1,493
vectors, so they can only add error; vectorized beam search (nothing to prune); HashingVectorizer
(approximate TF; real BM25 already here); word mover's distance (superseded by sentence
embeddings, heavy); hyperbolic/Poincaré embeddings (no pretrained text encoder to drop in; built
for hierarchy learning); full Mahalanobis (needs n ≫ 768 for the covariance — its useful low-rank
cousin *is* the centering above); numpy/scikit-learn on the query path (`math.sumprod` is already
C-speed; stdlib-only portability is a core invariant).

**Future paths, roughly in order:**

1. **Eval-set expansion — the keystone.** 31 → ~150–300 cases (curated + synthetic situation
   paraphrases generated from unit *text* by an LLM that never sees the stored `questions`;
   keep the synthetic set separately labeled — its generator bias correlates with our
   enrichment). Everything below is underpowered until this lands. Even hybrid-vs-lexical
   only reached p=0.037 after centering; per-knob deltas mostly can't be certified at n=31.
2. **Interface pack.** `--match raw` passthrough to FTS5's native query syntax (AND/OR/NOT,
   "phrases", NEAR(), `key_terms:` column filters, `&&`/`||` sugar); `--explain` per hit
   (winning channel, vector kind — gist/question #i/text chunk — and the matched question
   text); `gloss facets` vocabulary dump; result filters `--cliff <frac-of-top>`,
   `--require-agreement`, `--min-sem <cos>` (no absolute RRF threshold — fused scores aren't
   comparable across queries). Doubles as the query-log source for #1.
3. **Learned fusion, only after #1.** Tiny learning-to-rank: logistic regression / coordinate
   ascent over (bm25 rank, cosine, channel agreement, unit length) with leave-one-out CV.
   ~8 parameters want ~200+ cases. Not LambdaMART/boosting — capacity must match data.
4. **Deeper isotropy work if evidence demands**: remove top 2–5 principal components (power
   iteration, stdlib) instead of k=1; CSLS-style local scaling if hubness grows with corpus
   size.
5. **Agreement-gated pseudo-relevance feedback** — query expansion only when both channels
   agree on #1. Unguarded PRF drifts on the ~30% of queries whose #1 is wrong, and the current
   headroom is precision-at-#1, which expansion doesn't fix. Low priority.
6. **Watch item (was the known miss):** "boolean flag for one specific caller" ranked the
   §14.3 boolean-*naming* distractor above the §9.5 special-general-mixture red flag until
   centering; post-centering §9.5 is #1 with the distractors at #2–3, so the surface-similarity
   pressure is still there. If it regresses, the legitimate fix is broader enrichment coverage,
   **not** hand-editing §9.5's metadata to beat the eval case (Goodhart).

### Track D: eval-set expansion — candidates drafted, pending vetting (2026-07-10)

Future-path #1 executed to the candidate stage: **235 candidate cases** in
`corpora/aposd/cases-candidates.yaml`, in five tranches:

- **Dev-voice (145)**: situation-phrased first-person symptom queries, drafted from 82
  stratified source units (every chapter ≥1 case, chs 1–20 ≥3; all six principles plus the
  null-principle chapters; all five unit types) while reading **only** verbatim `text` +
  chapter/section — never the stored metadata.
- **Agent-voice (42)**: queries phrased the way the consuming agent actually issues them in
  the two intended workflows — design iron-out with the skill (prescriptive decision
  questions, alternatives side by side) and refactor review (third-person descriptions of
  observed code, concrete identifiers, python/asyncio vocabulary). Drafted **forward**
  (workflow simulation → query → pin located by reading unit text), which is the realistic
  direction. Includes 8 deliberate voice-pairs of dev-voice cases (marked) to measure voice
  robustness.
- **Vocab (20)**: term-driven queries in two flavors — *folk synonyms* the book never uses
  verbatim ("leaky abstraction", "god class", "YAGNI": pure folk-term → book-concept
  bridging) and *variant forms* of book terms ("passthrough params", "push complexity down
  the stack", "errors defined away": tokenization/morphology robustness). These skew
  lexically easy-to-collide rather than easy-to-rank: the corpus mentions the root words
  everywhere, so the test is whether the *canonical* passage outranks passages that merely
  use the term (the reranker's regression net). A third flavor — exact book terms needing a
  screen exemption ("shallow module" is in `key_terms` by design) — was considered and
  **declined** to keep the metadata-overlap guarantee absolute; the query-log path (#2)
  will capture those with genuine provenance.
- **Rough (22)**: realistic query shapes, calibrated to the *actual* primary querier —
  the consuming agent with the skill loaded. The skill instructs symptom phrasing, and
  the agent generalizes before searching (it knows the corpus is book passages: no
  framework names, no project identifiers, no typos or slang — early drafts with those
  were culled as unrealistic). Roughness = terse fragments ("big function split?"),
  multi-concern enumerations, compressed relayed situations, scope-calibration asks
  ("how much surrounding cleanup is justified while making a small change"). Rationale:
  the tranches above are all well-formed prose, the same *genre* as the LLM-generated
  enrichment questions even at zero n-gram overlap — a stylistic alignment the string
  screen cannot catch, so clean-tranche scores likely overestimate real use. This slice
  estimates the realistic floor; knob changes should not regress it even when they help
  the clean slices. Every case keeps a defensible pin; unanswerable fragments ("is this
  bad") were dropped as inadmissible.
- **Audit (6)**: artifact-property sweeps — reviewing a tool's option surface / API /
  error modes against the book, asking the *conditional* judgment ("are config options a
  smell or sometimes legitimate"). Phrased generalized: the querying agent strips
  project-specific nouns before searching.

All tranches leakage-screened by `corpora/aposd/screen_candidates.py` (stdlib): zero
word-4-gram overlap with `questions`/`key_terms`/`context_line`/`applies_when`, plus
warnings for quoting source text, echoing an existing case, or colliding with another
candidate (all cleared; exact in-file duplicates are a hard fail). Candidates were **not**
filtered by what the current system retrieves (anti-Goodhart); admissibility was
well-posedness only. A simulated merge scores well-formed: 266 cases, 6/6 principles, no
duplicate queries. One instrument gap noted for later: the skill recommends narrowing
with `--principle <slug>` when the principle is known, but the eval schema only scores
free-text queries — facet-narrowed retrieval has no cases yet (needs a small evalrun
extension to pass the filter through).

**Not merged into `cases.yaml`** — human vetting first; approved cases land under a
`# --- synthetic set ... ---` marker so curated-vs-synthetic stays separable. After the
merge: re-baseline all three modes here (keep the n=31 history above, labeled) and re-run
every per-track `--vs` comparison — n=31 numbers do not transfer.

---

## Status & known limitations (from README + handoffs)

gloss is **early** and a **working prototype, not a finished product**. The full APOSD corpus builds
end-to-end, hybrid retrieval scores hit@5=0.94 / hit@1=0.71 / MRR=0.80 on the eval set, but:

- The eval set is **31 cases** (16 original + 15 situation-phrased, including three regression
  anchors from live probing). Better than 16, still small — one case ≈ 3 points, so treat deltas
  under ~2 cases as noise. Never copy a stored `questions` string into a case (that grades the
  index on its own training data).
- Real-world usefulness **hasn't been battle-tested** beyond one external live-probe session.
- BM25 `_WEIGHTS` remain untuned defaults; the RRF constant and pool were swept once (rrf_k 20/60/100
  × pool 30/50/100 — flat except the rrf_k=20 top-rank effect, see §2).
- Known ranking miss: "boolean flag for one specific caller" surfaces a §14.3 boolean-*naming*
  passage above the §9.5 special-general-mixture red flag (both channels like the distractor's
  surface). In top-3, not #1.
- Chapters with no level-2 headings still collapse to one prose unit (ch11 = one 6.9k-char unit).
  Chunked text vectors make it *findable* now, but it returns as a wall of text; paragraph-aware
  splitting (the parser emits per-PDF-line paras, so true paragraph boundaries need indent
  detection) is the natural next boundary improvement.
- The redundant LLM `principle` field should be dropped (generated then always overridden, §8 above).
- The FTS trigger is insert-only (assumes wholesale rebuild); incremental writes would need
  UPDATE/DELETE triggers.
- Vectors don't survive `gloss build` (the db file is overwritten) — re-run `gloss embed`. An
  embed-if-vectors-existed convenience is a possible follow-up.

(README status line; `notes/2026-06-10-full-book-build-handoff.md` "Known gaps")

---

## Explicit non-goals (YAGNI)

From spec §2 — gloss is deliberately **not**: a vector database as the core (vectors are an
optional second channel in the same SQLite file — never the required path, never a separate store,
no ANN library); a conversational RAG Q&A bot; an OCR pipeline (the full book has a clean text
layer; the 20-page vector-outline extract is out of scope); an agentic build harness (the build is a
deterministic structured-output pass).
