# CLI Reference

The `gloss` console script ([`src/gloss/cli.py`](../src/gloss/cli.py)) has three subcommands: `retrieve` (query-time), `build`, and `eval`. A subcommand is **required** — running `gloss` with no args exits non-zero.

```
gloss [-h] {retrieve,build,eval} ...
```

`retrieve` imports only the stdlib store. `build` and `eval` lazily import their build-only deps inside the command function ([cli.py:31](../src/gloss/cli.py#L31), [cli.py:38](../src/gloss/cli.py#L38)), so `retrieve` never pulls them in. Run query-time commands with plain `uv run gloss ...`; run `build`/`eval` with `uv run --extra build gloss ...` (eval needs `pyyaml`; see [Errors](#error--exit-behavior)).

---

## `retrieve`

Print source passages matching a design situation, ranked by BM25 over the FTS5 index.

```
gloss retrieve [-h] [--db DB] [-k K] [--principle PRINCIPLE] [--type TYPE] [--json] query
```

| Arg | Type | Default | Meaning |
|-----|------|---------|---------|
| `query` | positional, str | *(required)* | Free-text design situation. Tokenized to an FTS5 `MATCH` expr: tokens >2 chars are OR-joined with prefix globs ([store.py:34-42](../src/gloss/store.py#L34)). A query with no usable (>2-char) tokens matches nothing. |
| `--db` | str | `aposd.db` | Path to the SQLite/FTS5 corpus db. |
| `-k` | int | `5` | Max number of hits to return. |
| `--principle` | str, repeatable | `None` | Filter to one or more coarse principle slugs. `action="append"` — pass the flag once per value ([cli.py:51](../src/gloss/cli.py#L51)). |
| `--type` | str, repeatable | `None` | Filter to one or more unit types. `action="append"` ([cli.py:52](../src/gloss/cli.py#L52)). |
| `--json` | flag | off | Emit the raw list of row dicts as indented JSON instead of formatted text. |

Filters are AND-combined across facets, OR-combined within a facet (`u.principle IN (...) AND u.type IN (...)`, [store.py:79-89](../src/gloss/store.py#L79)).

### Output: default (text)

Each hit renders as a one-line citation header followed by the verbatim passage and a trailing blank line ([`_format_hit`, cli.py:14-17](../src/gloss/cli.py#L14)):

```
[<principle> §<section> p.<page>] (<type>)
<verbatim text>
```

If there are no hits, prints `(no matches)`.

```console
$ uv run gloss retrieve "deep module hides complexity" --db build/minimax.db -k 1
[information-hiding §5.10 p.48] (definition)
Information hiding and deep modules are closely related. If a module hides a lot
of information, that tends to increase the amount of functionality provided by the
module ...
```

### Output: `--json`

Prints `json.dumps(hits, indent=2)` ([cli.py:24](../src/gloss/cli.py#L24)) — a JSON array of objects, one per hit. Keys come straight from the `units` row plus the BM25 `score` ([`search`, store.py:87-96](../src/gloss/store.py#L87)):

| Key | Type | Notes |
|-----|------|-------|
| `score` | float | BM25 score; **more negative = more relevant**, results ordered ascending ([store.py:73-74](../src/gloss/store.py#L73)). |
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
| `questions` | str | Space-joined questions the passage answers. |
| `enrich_model` | str | Model that enriched the unit, e.g. `minimax-m3:cloud`. |
| `needs_enrich` | int | `0` = enriched, `1` = enrichment failed/pending. |

```console
$ uv run gloss retrieve "deep module hides complexity" --db build/minimax.db -k 1 --json
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
# default db (aposd.db), top 5
uv run gloss retrieve "my class just forwards calls and adds nothing"

# filter to one principle, more results
uv run gloss retrieve "callers must call setup in the right order" \
  --db build/minimax.db --principle information-hiding -k 10

# repeat a flag for OR within a facet
uv run gloss retrieve "shallow class" --db build/minimax.db \
  --type red_flag --type rationale

# combine facets (AND across, OR within)
uv run gloss retrieve "shallow helper manager" --db build/minimax.db \
  --principle deep-modules --type red_flag
```

---

## `build`

Build the corpus db from the source document (parse → segment → enrich → store). Build-only deps are lazily imported ([cli.py:30-33](../src/gloss/cli.py#L30)); run with the `build` extra and a reachable Ollama model. Operational details (checkpoints, resume, workers, model A/B, troubleshooting) live in [BUILDS.md](BUILDS.md); the fresh-checkout runbook is in [STARTUP_GUIDE.md](STARTUP_GUIDE.md).

```
gloss build [-h] [--chapter CHAPTER] [--model MODEL] [--db DB]
            [--resume] [--workers WORKERS] [--build-dir BUILD_DIR]
```

| Arg | Type | Default | Meaning |
|-----|------|---------|---------|
| `--chapter` | str | `None` | Build a single chapter by id. `None` = build the whole book + appendices ([cli.py:57](../src/gloss/cli.py#L57), [build.py:80-86](../src/gloss/build.py#L80)). |
| `--model` | str | `minimax-m3:cloud` | Ollama model used for enrichment, recorded per-unit in `enrich_model`. |
| `--db` | str | `aposd.db` | Output db path (overwritten on build). |
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

## `eval`

Score retrieval against eval cases (top-k hit-rate). Lazily imports `evalrun`, which imports `pyyaml` ([evalrun.py:28](../src/gloss/evalrun.py#L28)) — run with the `build` extra.

```
gloss eval [-h] [--db DB] [--cases CASES]
```

| Arg | Type | Default | Meaning |
|-----|------|---------|---------|
| `--db` | str | `aposd.db` | Corpus db to evaluate against. |
| `--cases` | str | `corpora/aposd/cases.yaml` | YAML file with a `cases:` list. Each case has `query` plus `expect_principle` and/or `expect_section`; a case is a hit if any top-5 result matches either expectation ([evalrun.py:8-23](../src/gloss/evalrun.py#L8)). |

`k` is fixed at 5 (not a flag; [evalrun.py:20](../src/gloss/evalrun.py#L20)).

### Output

A single line ([evalrun.py:31](../src/gloss/evalrun.py#L31)):

```console
$ uv run --extra build gloss eval --db build/minimax.db
hit_rate=0.75 over n=16
```

`hit_rate` is the fraction of cases whose top-5 contains the expected unit; `n` is the case count.

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
$ sqlite3 build/minimax.db "SELECT DISTINCT type FROM units;"
rationale
definition
code
example
red_flag
```

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
| `eval` (or any build-extra command) run without the `build` extra | `ModuleNotFoundError: No module named 'yaml'` ([evalrun.py:28](../src/gloss/evalrun.py#L28)). Use `uv run --extra build`. |
| `build --chapter X` where `X` isn't detected | `raise SystemExit("chapter 'X' not found by detection/override")` ([build.py:86](../src/gloss/build.py#L86)). |
| No subcommand given | argparse error, non-zero exit (`required=True`, [cli.py:45](../src/gloss/cli.py#L45)). |
