# CLI Reference

The `gloss` console script ([`src/gloss/cli.py`](../src/gloss/cli.py)) has five subcommands: `retrieve`, `show`, and `embed` (query-side, stdlib packages only), `build`, and `eval`. A subcommand is **required** — running `gloss` with no args exits non-zero.

```
gloss [-h] {retrieve,show,embed,build,eval} ...
```

`retrieve`, `show`, and `embed` import only stdlib modules (`store` / `vectors`) — though `embed` and the hybrid retrieve modes additionally need a **running local Ollama** (a service, not a package). `build` and `eval` lazily import their build-only deps inside the command function, so `retrieve` never pulls them in. Run query-side commands with plain `uv run gloss ...`; run `build`/`eval` with `uv run --extra build gloss ...` (eval needs `pyyaml`; see [Errors](#error--exit-behavior)).

---

## `retrieve`

Print source passages matching a design situation. Default mode is `auto`: BM25 and
the vector channel fused with reciprocal rank fusion when the db has vectors and the
embedder is reachable, plain BM25 otherwise.

```
gloss retrieve [-h] --db DB [-k K] [--principle PRINCIPLE] [--type TYPE] [--json]
               [--compact] [--mode {auto,lexical,hybrid,semantic}] [--ollama-url URL]
               query
```

| Arg | Type | Default | Meaning |
|-----|------|---------|---------|
| `query` | positional, str | *(required)* | Free-text design situation. Tokenized to an FTS5 `MATCH` expr: tokens >2 chars are OR-joined with prefix globs ([store.py:34-42](../src/gloss/store.py#L34)). A query with no usable (>2-char) tokens matches nothing. |
| `--db` | str | **required** | Path to the SQLite/FTS5 corpus db. No default — pass it every time. |
| `-k` | int | `5` | Max number of hits to return. |
| `--principle` | str, repeatable | `None` | Filter to one or more coarse principle slugs. `action="append"` — pass the flag once per value ([cli.py:51](../src/gloss/cli.py#L51)). |
| `--type` | str, repeatable | `None` | Filter to one or more unit types. `action="append"` ([cli.py:52](../src/gloss/cli.py#L52)). |
| `--json` | flag | off | Emit the raw list of row dicts as indented JSON instead of formatted text. |
| `--compact` | flag | off | **Text mode only.** Hit #1 renders in full as usual; ranks 2..k render as one `more:` preview line each (see [Output: `--compact`](#output---compact)). With `--json` the flag is a no-op — JSON output is byte-identical with or without it. |
| `--mode` | choice | `auto` | `auto` = hybrid when the db has vectors and Ollama answers, else lexical (silently for a vector-less db, with a stderr note when vectors exist but the embedder is down). `lexical` = BM25 only, never touches vectors. `hybrid` = BM25 + vectors via RRF, **fails loudly** (`SystemExit`) if the channel can't run. `semantic` = vectors only (ablation/debugging). |
| `--ollama-url` | str | `http://localhost:11434` | Ollama base URL for query embedding (all modes except `lexical`). |
| `--rerank` | flag | off | LLM-rerank the top candidates (fetches ≥5 even at `-k 1`, returns top-k). **Gated**: when fusion's #1 is dual-backed (lexical #1 + semantic top-5) it is trusted and no model call happens — measured, every reranker's mistakes cluster on exactly those (`rerank.py:fusion_trusts_top1`). On any failure (model missing, bad reply) the original order is kept with a stderr note; reordered hits carry `reranked: true`. |
| `--rerank-model` | str | `gemma4:e2b` | Rerank model (safe local default). Measured best: `gemma4:31b-cloud` — hit@1 0.71→0.84 at ~0.9s/call; `minimax-m3:cloud` ties on quality. |
| `--rerank-prompt` | file | built-in | Corpus-specific rerank prompt template (`{query}`, `{candidates}`, `{n}` placeholders). The built-in is corpus-agnostic; a corpus-tuned instruction can do better. |

Filters are AND-combined across facets, OR-combined within a facet (`u.principle IN (...) AND u.type IN (...)`, [store.py:79-89](../src/gloss/store.py#L79)). Filters apply to both channels.

### Output: default (text)

Each hit renders as a one-line citation header followed by the verbatim passage and a trailing blank line ([`_format_hit`, cli.py](../src/gloss/cli.py)):

```
[<principle> §<section> p.<page>] (<type> via <channels>)
<verbatim text>
```

The `via` tag appears on hybrid/semantic hits and shows each channel's rank — it makes
reliability visible at a glance: `via lex#1+sem#2` means two independent signals agree
on this passage; `via sem#4` means only one channel surfaced it, so read it with more
care. Lexical-mode hits have no tag. If there are no hits, prints `(no matches)`.

```console
$ uv run gloss retrieve "deep module hides complexity" --db build/minimax-v2.db -k 1
[information-hiding §5.10 p.48] (definition via lex#1+sem#2)
Information hiding and deep modules are closely related. If a module hides a lot
of information, that tends to increase the amount of functionality provided by the
module ...
```

### Output: `--compact`

For agent consumption: k=3 shouldn't cost three full passages of tokens. Hit #1 renders
exactly as in default text mode (citation header + verbatim passage); each runner-up
becomes a single pointer line:

```
more: id=<id> [<citation>] (<type> via <tags>) — <context_line>
```

The preview body is the unit's generated `context_line` — a **paraphrase**, acceptable
only because it is labeled as a pointer and never presented as the passage (the
verbatim promise applies to passage bodies). It is whitespace-collapsed and truncated
at ~140 chars with a trailing `…`. To read a runner-up's actual text, expand it with
[`gloss show <id>`](#show) — never quote a preview line.

```console
$ uv run gloss retrieve "callers must call setup in the right order" --db build/minimax-v2.db -k 3 --compact
[deep-modules §9.6 p.76] (red_flag via lex#8+sem#2)
The NetworkErrorLogger class contained several methods ...

more: id=34 [information-hiding §5.7 p.46] (example via lex#3+sem#18) — Illustrates partial information hiding through defaults: forcing callers to specify values (like HTTP response version or Date) they should…
more: id=54 [information-hiding §7 p.56] (definition via lex#29+sem#3) — Opening definition: a well-designed layered system exposes a different abstraction at each layer, so that the concept changes with every me…
```

`--compact` never changes *which* units are returned or their order — it is purely an
output-shape concern. With no hits it still prints `(no matches)`; with one hit it is
identical to default text mode.

### Output: `--json`

Prints `json.dumps(hits, indent=2)` ([cli.py:24](../src/gloss/cli.py#L24)) — a JSON array of objects, one per hit. Keys come straight from the `units` row plus the BM25 `score` ([`search`, store.py:87-96](../src/gloss/store.py#L87)):

| Key | Type | Notes |
|-----|------|-------|
| `score` | float\|null | BM25 score; **more negative = more relevant**. In lexical mode results are ordered by it ascending. In hybrid mode it's informational only (order comes from `rrf`) and is `null` for a hit the lexical channel didn't rank. |
| `channels` | object | Hybrid/semantic modes only: per-channel 1-based rank, e.g. `{"lexical": 2, "semantic": 1}`. A missing key means that channel didn't rank the unit in its candidate pool. |
| `rrf` | float | Hybrid mode only: the fused reciprocal-rank score results are ordered by (higher = better). |
| `sem_score` | float | Semantic mode only: max cosine similarity over the unit's vectors. |
| `id` | int | `units.id` primary key. |
| `principle` | str\|null | Coarse facet slug (or empty/null for gap chapters). |
| `chapter` | str | Chapter id. |
| `section` | str | e.g. `5.10`. |
| `type` | str | One of the 5 type values. |
| `page` | int | Source page number. |
| `text` | str | Verbatim passage (fixed at segmentation, never LLM-rewritten). |
| `context_line` | str | LLM-generated one-line situating gloss. |
| `applies_when` | str | LLM-generated applicability note. |
| `key_terms` | str | Space-joined terms. |
| `questions` | str | **Newline**-joined questions the passage answers (one per line, so per-question boundaries survive for the vector channel). |
| `enrich_model` | str | Model that enriched the unit, e.g. `minimax-m3:cloud`. |
| `needs_enrich` | int | `0` = enriched, `1` = enrichment failed/pending. |

```console
$ uv run gloss retrieve "deep module hides complexity" --db build/minimax-v2.db -k 1 --json
[
  {
    "score": -8.607073516282574,
    "id": 44,
    "principle": "information-hiding",
    "chapter": "5",
    "section": "5.10",
    "type": "definition",
    "page": 48,
    "text": "Information hiding and deep modules are closely related. ...",
    "context_line": "Core statement of the information-hiding principle: ...",
    "applies_when": "When deciding how to split a system into modules ...",
    "key_terms": "information hiding deep modules shallow modules ...",
    "questions": "Why does hiding more information make a module deeper? ...",
    "enrich_model": "minimax-m3:cloud",
    "needs_enrich": 0
  }
]
```

`--json` always emits valid JSON; with no hits it prints `[]`.

### Examples

```console
# top 5 (k defaults to 5)
uv run gloss retrieve "my class just forwards calls and adds nothing" --db build/minimax-v2.db

# filter to one principle, more results
uv run gloss retrieve "callers must call setup in the right order" \
  --db build/minimax-v2.db --principle information-hiding -k 10

# repeat a flag for OR within a facet
uv run gloss retrieve "shallow class" --db build/minimax-v2.db \
  --type red_flag --type rationale

# combine facets (AND across, OR within)
uv run gloss retrieve "shallow helper manager" --db build/minimax-v2.db \
  --principle deep-modules --type red_flag

# force a mode
uv run gloss retrieve "boolean flag for one caller" --db build/minimax-v2.db --mode hybrid
uv run gloss retrieve "boolean flag for one caller" --db build/minimax-v2.db --mode lexical
```

---

## `show`

Print one unit in full by id ([`store.py:get_unit`](../src/gloss/store.py), one SELECT,
stdlib only). Same rendering as a retrieve hit — citation header + verbatim passage —
plus a trailing `applies when:` line (the LLM-generated applicability note, useful for
confirming fit). This is the expansion step for a `--compact` preview line: the preview
shows a paraphrase; `show` gives you the source's actual words.

```
gloss show [-h] --db DB id
```

| Arg | Type | Default | Meaning |
|-----|------|---------|---------|
| `id` | positional, int | *(required)* | `units.id` primary key, as printed in a `more: id=N …` preview line or the `id` field of `--json` output. |
| `--db` | str | **required** | Path to the SQLite/FTS5 corpus db. |

No `via` channel tag appears — a direct lookup has no retrieval channels. An unknown id
exits non-zero with `gloss show: no unit with id=N in <db>` on stderr.

```console
$ uv run gloss show 34 --db build/minimax-v2.db
[information-hiding §5.7 p.46] (example)
The HTTP projects also had to provide support for generating HTTP responses. ...
applies when: You're designing an API and the caller must pass a value that the system already knows ...
```

---

## `embed`

Precompute the semantic channel: one pass over an already-built db, embedding every
unit into a `vectors` table inside the **same** `.db` file ([`vectors.py:embed_corpus`](../src/gloss/vectors.py)).
Stdlib packages only, but needs a running local Ollama serving the embedding model
(`ollama pull embeddinggemma`).

```
gloss embed [-h] --db DB [--model MODEL] [--ollama-url URL] [--batch BATCH]
```

| Arg | Type | Default | Meaning |
|-----|------|---------|---------|
| `--db` | str | **required** | Corpus db to embed. Vectors are written into this file; prior vectors are replaced. |
| `--model` | str | `embeddinggemma:latest` | Ollama embedding model. Recorded in `vectors_meta` and reused automatically at query time. |
| `--ollama-url` | str | `http://localhost:11434` | Ollama base URL. |
| `--batch` | int | `32` | Texts per `/api/embed` request. |

Each unit gets several vectors: one *gist* (context_line + applies_when + key_terms),
one **per generated question** (dense doc2query), and one per verbatim-text chunk
(units over ~6000 chars are split at line boundaries to fit embeddinggemma's 2048-token
context). Vectors are stored **mean-centered + renormalized** (the corpus common
direction is removed and the mean recorded in `vectors_meta`, so queries get the
identical correction — see DESIGN.md's experiment log for why and the measured gain). Errors propagate loudly — an embed command must not silently degrade the way
queries do.

```console
$ uv run gloss embed --db build/minimax-v2.db
embedded 197 units -> 1493 vectors (dim=768, model=embeddinggemma:latest) in build/minimax-v2.db
```

Takes ~16s for the full APOSD corpus on a local `embeddinggemma` (307M). The db stays a
single portable file (~7 MB with vectors). **Re-run after every `gloss build`** — a
build overwrites the db file, so vectors don't survive it.

---

## `build`

Build the corpus db from the source document (parse → segment → enrich → store). Build-only deps are lazily imported ([cli.py:30-33](../src/gloss/cli.py#L30)); run with the `build` extra and a reachable Ollama model. Operational details (checkpoints, resume, workers, model A/B, troubleshooting) live in [BUILDS.md](BUILDS.md); the fresh-checkout runbook is in [STARTUP_GUIDE.md](STARTUP_GUIDE.md).

```
gloss build [-h] [--chapter CHAPTER] [--model MODEL] --db DB
            [--resume] [--workers WORKERS] [--build-dir BUILD_DIR]
```

| Arg | Type | Default | Meaning |
|-----|------|---------|---------|
| `--chapter` | str | `None` | Build a single chapter by id. `None` = build the whole book + appendices ([cli.py:57](../src/gloss/cli.py#L57), [build.py:80-86](../src/gloss/build.py#L80)). |
| `--model` | str | `minimax-m3:cloud` | Ollama model used for enrichment, recorded per-unit in `enrich_model`. |
| `--db` | str | **required** | Output db path (overwritten on build). No default. |
| `--resume` | flag | off | Keep existing per-chapter JSONL checkpoints instead of wiping them; re-enriches only failed units. |
| `--workers` | int | `1` | Concurrent enrichment requests per chapter (`1` = serial) ([cli.py:61](../src/gloss/cli.py#L61)). |
| `--build-dir` | str | `build` | Root dir for per-chapter JSONL checkpoints. **Use a distinct dir per model** (e.g. `build/minimax`) so concurrent/sequential model builds don't clobber each other's checkpoints ([cli.py:62-64](../src/gloss/cli.py#L62)). |

### Example

```console
$ uv run --extra build gloss build --chapter 1 \
    --model minimax-m3:cloud --db build/test-ch1.db --build-dir build/minimax
chapters=1 units=2 num_ctx=8192 model=minimax-m3:cloud
...
```

The startup line `chapters=N units=M num_ctx=C model=...` is printed before enrichment ([build.py:101](../src/gloss/build.py#L101)). If the largest prompt's estimated token count exceeds the `num_ctx` cap, a `WARNING: largest prompt ~N est tokens exceeds num_ctx cap C; trim situating context or raise the cap` is printed ([build.py:42-43](../src/gloss/build.py#L42)).

---

## `enrich-questions`

Second-pass **question top-up** over a build's checkpoints: for every enriched unit,
ask the model for 4–6 *new* retrieval questions from angles the first pass didn't
cover (mid-task complaint, code-review comment, "is it OK to…" permission phrasing,
"what goes wrong if…" consequence phrasing). Widens the semantic net — one vector per
question, and question vectors win ~80% of semantic matches. Build-only deps
(lazy-imported); prompt template is the instance's
[`prompt-questions.md`](../corpora/aposd/prompt-questions.md).

```
gloss enrich-questions [-h] --build-dir BUILD_DIR [--model MODEL] [--workers WORKERS]
```

| Arg | Type | Default | Meaning |
|-----|------|---------|---------|
| `--build-dir` | str | **required** | Checkpoint root of the build to top up (e.g. `build/minimax-v2`). Required — no default, so a typo can't silently top up nothing. |
| `--model` | str | `minimax-m3:cloud` | Ollama model for the top-up, recorded per-row in `topup_model`. |
| `--workers` | int | `1` | Concurrent top-up requests. |

Writes **checkpoints only, never the db**: each topped-up unit gets an appended row
copy with `questions = old + new` under the same key, superseding the original on
read-back (last-wins). Ship it with the zero-quota rebuild + re-embed dance:

```bash
uv run --extra build gloss enrich-questions --build-dir build/minimax-v2 \
    --model minimax-m3:cloud --workers 8
uv run --extra build gloss build --resume --db build/minimax-v2.db --build-dir build/minimax-v2
uv run gloss embed --db build/minimax-v2.db   # rebuild wiped the vectors
```

Resumable: rows whose current version carries `topup_model` are skipped; a failed
unit gets **no** appended row and is re-attempted on the next run. Failed-enrichment
rows (`needs_enrich=1`) are skipped — resume the build first. Stale checkpoint rows
(older segmentation rules) get topped up too — wasted calls but harmless, they never
ship.

---

## `eval`

Score retrieval against eval cases: hit@k, hit@1, and MRR — the last two are
rank-sensitive, so the eval can tell "right passage at #1" from "right passage at #5".
Lazily imports `evalrun`, which imports `pyyaml` — run with the `build` extra.

```
gloss eval [-h] --db DB [--cases CASES] [-k K] [-v]
           [--mode {lexical,hybrid,semantic}] [--ollama-url URL]
```

| Arg | Type | Default | Meaning |
|-----|------|---------|---------|
| `--db` | str | **required** | Corpus db to evaluate against. No default. |
| `--cases` | str | `corpora/aposd/cases.yaml` | YAML file with a `cases:` list. Each case has `query` plus any of `expect_principle` / `expect_section` / `expect_chapter`; a result matching **any** pinned field counts ([evalrun.py:8-23](../src/gloss/evalrun.py#L8)). Chapter/section pins exist because the null-principle chapters (10, 11, 14, 17-21) are unreachable through the principle facet. |
| `-k` | int | `5` | Top-k window for `hit@k`. |
| `-v` / `--verbose` | flag | off | Also print every case whose expected unit is **not** ranked #1 (its rank or `miss`). |
| `--mode` | choice | `lexical` | Retrieval mode to score. Deliberately no `auto`: an eval must not silently degrade. `hybrid`/`semantic` need an embedded db + running Ollama. |
| `--vs` | choice | — | Second mode to compare against: prints both score lines plus `Δmrr` and a p-value from a paired sign-flip randomization test on per-case reciprocal rank ([evalrun.py:paired_sign_flip](../src/gloss/evalrun.py)) — use it before adopting any knob change. |
| `--rerank` / `--rerank-model` / `--rerank-prompt` | | | As in `retrieve`, applied to the **primary `--mode` leg only** — so `--mode hybrid --rerank --vs hybrid` isolates exactly the reranker's contribution. |
| `--ollama-url` | str | `http://localhost:11434` | Ollama base URL for the non-lexical modes. |

### Output

```console
$ uv run --extra build gloss eval --db build/minimax-v2.db --mode hybrid
hit@5=0.94 hit@1=0.71 mrr=0.80 n=31

$ uv run --extra build gloss eval --db build/minimax-v2.db --mode hybrid --vs lexical
hybrid  : hit@5=0.94 hit@1=0.71 mrr=0.80 n=31
lexical : hit@5=0.84 hit@1=0.55 mrr=0.66 n=31
Δmrr=+0.136 p=0.0365 (paired sign-flip on per-case reciprocal rank, 10000 resamples)
```

`hit@k` = fraction of cases whose top-k contains the expected unit (the historical
`hit_rate`); `hit@1` = fraction where it is ranked first; `mrr` = mean reciprocal rank
of the first matching result.

---

## Controlled vocabulary

### `--principle` (coarse facet — 6 fixed slugs)

The closed set is defined in [`corpora/aposd/taxonomy.yaml`](../corpora/aposd/taxonomy.yaml) and aligns to the software-design-philosophy skill's 6 principles ([taxonomy.yaml:16-202](../corpora/aposd/taxonomy.yaml#L16)):

| Slug | Name |
|------|------|
| `complexity` | Complexity and Its Causes |
| `deep-modules` | Deep vs Shallow Modules |
| `information-hiding` | Information Hiding and Leakage |
| `general-purpose` | General-Purpose vs Special-Purpose Modules |
| `comments` | Comments as Design Documentation |
| `strategic-programming` | Strategic vs Tactical Programming |

Gap chapters map to `principle: null` ([taxonomy.yaml:216-227](../corpora/aposd/taxonomy.yaml#L216)); their units carry an empty/null `principle` and are not reachable via any `--principle` filter.

### `--type` (5 values)

A `CHECK` constraint pins the set at the schema level ([store.py:16](../src/gloss/store.py#L16)). Confirmed against the built corpus:

```console
$ sqlite3 build/minimax-v2.db "SELECT type, COUNT(*) FROM units GROUP BY type;"
code|4
definition|58
example|50
rationale|63
red_flag|22
```

`code` is rare under the current segmentation: code blocks travel inside the prose unit
that introduces them, so `type='code'` appears only where the LLM classifies a merged
unit as code (a *standalone* block with no lead-in would be forced to `code`, but APOSD
has none).

| Value | Meaning |
|-------|---------|
| `definition` | States/defines a principle or term. |
| `rationale` | Explains the why. |
| `example` | Worked example. |
| `code` | Code passage. |
| `red_flag` | A named smell/anti-pattern. |

---

## Error & exit behavior

| Situation | Result |
|-----------|--------|
| Query with no >2-char tokens | `search` returns `[]`; text mode prints `(no matches)`, `--json` prints `[]` ([store.py:77-78](../src/gloss/store.py#L77)). |
| `retrieve` / `eval` against a db with no `units_fts` table (e.g. a fresh 0-byte `build/aposd.db`) | `sqlite3.OperationalError: no such table: units_fts` — uncaught traceback ([store.py:93](../src/gloss/store.py#L93)). See [BUILDS.md](BUILDS.md) for the 0-byte-db gotcha. |
| `eval` (or any build-extra command) run without the `build` extra | `ModuleNotFoundError: No module named 'yaml'`. Use `uv run --extra build`. |
| `retrieve --mode hybrid`/`semantic` against a db with no vectors, or with Ollama down | `SystemExit: gloss retrieve --mode …: db has no vectors — run: gloss embed …` (or `embedder unreachable at <url>`). Explicit modes fail loudly. |
| `show <id>` where no unit has that id | `SystemExit: gloss show: no unit with id=N in <db>` — non-zero exit, message on stderr. |
| `retrieve` (auto mode) when vectors exist but the embedder is down | Falls back to lexical and prints `gloss: semantic channel off (…); lexical only` to **stderr**; results still returned. |
| `embed` with Ollama down / model not pulled | Uncaught `urllib.error.URLError`/`HTTPError` traceback — loud by design. Start Ollama / `ollama pull embeddinggemma`. |
| `build --chapter X` where `X` isn't detected | `raise SystemExit("chapter 'X' not found by detection/override")` ([build.py:86](../src/gloss/build.py#L86)). |
| `--db` omitted on any subcommand | argparse error, non-zero exit: `the following arguments are required: --db`. There is no default db ([cli.py:49](../src/gloss/cli.py#L49),`:59`,`:68`). |
| No subcommand given | argparse error, non-zero exit (`required=True`, [cli.py:45](../src/gloss/cli.py#L45)). |
