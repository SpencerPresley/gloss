# Builds

Operational reference for `gloss build` — checkpoints, resume, concurrency, model
selection, and troubleshooting. For the fresh-checkout runbook (fetch the PDF, build,
verify retrieval) see [STARTUP_GUIDE.md](STARTUP_GUIDE.md); this doc covers the build
internals, not the first-run steps.

## Build is one command

`gloss build` is the whole pipeline — parse → segment → enrich (LLM) → SQLite/FTS5 —
in a single invocation. There is **no separate `analyze` step**; the command writes
the finished `.db`. Build-time deps (`pymupdf`, `langchain`, `langchain-ollama`) plus
an Ollama model are required, so always run it under the `build` extra:

```bash
uv run --extra build gloss build --model minimax-m3:cloud --workers 8 \
  --db build/minimax.db --build-dir build/minimax
```

`run_build` (`src/gloss/build.py:47`) does it all: resolve per-chapter element spans
(dynamic detection or `profile.chapter_pages` override), segment each span into units,
build prompts, size `num_ctx` once over the whole build, enrich each chapter with its
taxonomy principle card, accumulate every row, and call `build_db`. Expected tail:

```
built 257 units (0 enrichment failures) -> build/minimax.db
```

## Pipeline, end to end

| Stage | Code | What happens |
|-------|------|--------------|
| parse | `parse.py:parse_pdf` | PDF page range → layout elements |
| segment | `segment.py:segment` / `split_chapters` | elements → `RawUnit`s (prose runs + code blocks) + per-section text |
| enrich | `enrich.py:enrich_units` | each unit → retrieval metadata via the extractor; checkpointed to JSONL |
| store | `store.py:build_db` | all rows → `units` table + `units_fts` FTS5 index |

Chapters are detected via `profile.chapter_re`, or taken verbatim from
`profile.chapter_pages` when that override is set (`build.py:74`). With `--chapter` set,
only that one chapter is built; with no `--chapter`, the whole book plus appendices is
built (`build.py:80`). A whole-book build of APOSD is **257 units across 23 chapters**
(21 chapters + 2 summary appendices).

## Per-chapter JSONL checkpoint

Each chapter's enrichment is checkpointed to one JSONL file, written incrementally by
`enrich_units` (`enrich.py:115`):

```
<build_dir>/ch<chapter_id>/units.jsonl
```

e.g. `build/minimax/ch1/units.jsonl`, `build/minimax/ch13/units.jsonl`,
`build/minimax/chsummary-redflags/units.jsonl`. The chapter id is whatever the profile
uses (`"1"`…`"21"`, `"summary-principles"`, `"summary-redflags"`).

One line per enriched unit, a flat JSON object (`enrich.py:89`). Real row from
`build/minimax/ch1/units.jsonl`:

| Field | Source | Notes |
|-------|--------|-------|
| `key` | `_key(unit)` | `sha1(section \| is_code \| text)[:12]` — stable resume key (`enrich.py:38`) |
| `text` | verbatim passage | fixed at segmentation, never LLM-rewritten |
| `chapter`, `section`, `page` | the unit | metadata |
| `enrich_model` | `extractor.model` | e.g. `"minimax-m3:cloud"` |
| `needs_enrich` | `0` success / `1` failure | `1` = LLM fields are empty placeholders |
| `principle` | LLM field | **overwritten** at db build from the taxonomy (see below) |
| `type` | LLM field (forced `"code"` for code units) | one of definition/rationale/example/code/red_flag |
| `context_line`, `applies_when`, `key_terms` (list), `questions` (list) | LLM fields | generated retrieval metadata |

The write path appends each row as it completes and `flush()`es it (`enrich.py:118-119`),
so an interrupted run leaves a valid partial file. On read-back, `enrich_units` parses
every line into `rows_by_key` keyed by `key`, **last-wins** (`enrich.py:134`) — a later
successful row supersedes an earlier failed one for the same unit. (Note: a unit
re-enriched on a later `--resume` appends a *new* line and dedup-by-key keeps the last, so
`wc -l` can exceed the unit count. The current minimax checkpoints have no re-attempts —
257 lines, 257 distinct keys, matching the 257 db units.)

## `--resume` semantics

`--resume` keeps existing checkpoints instead of wiping them. Without it,
`run_build` deletes each chapter's `units.jsonl` before enriching (`build.py:109`):

```python
if not resume and checkpoint.exists():
    checkpoint.unlink()
```

On resume, only **not-yet-successfully-enriched** units are re-attempted. `_done_keys`
(`enrich.py:49`) reads the checkpoint and collects keys **where `needs_enrich == 0`** —
failed units (`needs_enrich == 1`) are deliberately excluded:

```python
if not row.get("needs_enrich"):
    keys.add(row["key"])
```

`enrich_units` then enriches only `pending = [u for u in units if _key(u) not in done]`
(`enrich.py:108`). Consequences:

- **Successful units are never re-enriched** — zero LLM calls / zero quota for them.
- **Failed units (e.g. from a quota cap) are retried** on the next `--resume`. The
  empty placeholder result is never baked in permanently.
- A `--resume` with *all* units already successful does **no LLM work at all** — it just
  reads checkpoints back, re-applies the taxonomy principle override, and rebuilds the
  db. This is the zero-quota way to regenerate a `.db` from checkpoints:

```bash
uv run --extra build gloss build --resume \
  --db build/minimax.db --build-dir build/minimax
```

## `--workers` concurrency model

`--workers N` (default `1`) sets concurrent enrichment requests **per chapter**
(`enrich_units(..., max_workers=workers)`). The executor is a
`concurrent.futures.ThreadPoolExecutor` (`enrich.py:11,123`):

- `max_workers <= 1` → fully serial, units enriched in order.
- `max_workers > 1` → the **first pending unit is enriched serially as a warmup**
  (`enrich.py:122`) to pin the extractor's structured-output method and warm its client
  *before* fanning out; the rest run on `pool.map(work, pending[1:])`.
- Checkpoint writes are serialized by a `threading.Lock` (`enrich.py:117`), so the JSONL
  is never interleaved/corrupted under concurrency.
- `pool.map` preserves input order, but rows are written as they *complete*; final order
  in the file is completion order, which doesn't matter (read-back is keyed, not ordered).

The full minimax build used `--workers 8` and **<7% of a 5-hour budget**.

## `--build-dir` + per-model db convention

Two artifacts per build:

| Artifact | Convention | Why |
|----------|-----------|-----|
| Output db | `build/<model>.db` (`--db`) | one db per model |
| Checkpoints | `build/<model>/ch*/units.jsonl` (`--build-dir`) | one checkpoint tree per model |

`--build-dir` (`cli.py:62`) is the root for the per-chapter JSONL files. **Give each
model its own `--build-dir`**, because checkpoint paths are keyed only by chapter id
(`ch1/units.jsonl`, …), *not* by model. Two models sharing `build/` would write the same
`build/ch1/units.jsonl` and clobber each other's checkpoints — a `--resume` for model B
would read back model A's enrichment. The per-model dir keeps them independent:

```bash
# MiniMax M3
uv run --extra build gloss build --model minimax-m3:cloud --workers 8 \
  --db build/minimax.db --build-dir build/minimax
# GLM 5.2 (separate db AND build-dir — coexists, no clobber)
uv run --extra build gloss build --model glm-5.2:cloud --workers 8 \
  --db build/glm52.db --build-dir build/glm52
```

Both `build/` and the dbs are gitignored and regenerable.

## `num_ctx` sizing and the cap warning

`num_ctx` is **measured from the real prompts, not a constant**. `estimate_num_ctx`
(`build.py:35`) computes, over every prompt in the build:

```
need = max(len(system) + len(prompt)) // 3 + headroom   # headroom = 2048
num_ctx = max(floor, min(need, cap))                     # floor = 8192, cap = 32768
```

`chars // 3` deliberately **over-estimates** tokens (undercounting would truncate the
prompt). If `need > cap` (32768) it prints a warning rather than silently exceeding it:

```
WARNING: largest prompt ~<N> est tokens exceeds num_ctx cap 32768; trim situating context or raise the cap
```

The chosen `num_ctx` is logged in the first build line (`build.py:101`); the APOSD full
build sizes to `num_ctx=8192` (the floor). `num_ctx` is passed to the `OllamaExtractor`
for local models; cloud models ignore it (`extract.py:41`).

## Enrichment failure handling

`_enrich_one` (`enrich.py:66`) retries transient extractor errors with exponential
backoff (`2**attempt` s, `retries=2`). On persistent failure the unit **keeps its
verbatim text** but gets empty generated fields and `needs_enrich=1` (`enrich.py:84`):

```python
fields = {"principle": "", "type": "code" if unit.is_code else "rationale",
          "context_line": "", "applies_when": "", "key_terms": [], "questions": []}
needs = 1
```

After the build, if any unit failed, `run_build` prints (`build.py:125`):

```
WARNING: <F>/<N> units failed enrichment — does model '<model>' support structured output?
```

A nonzero failure count almost always means the model doesn't honor structured output.
`OllamaExtractor` tries `json_schema` (grammar-constrained) first, then
`function_calling` (`extract.py:48`); if **both** fail for a unit it raises and that unit
is marked failed. The fix: `--resume` to retry only the failed units (clean-resume never
bakes the empty result in), or switch to a model that supports structured output.

The final db carries `needs_enrich` per row, so you can audit a built db:

```bash
sqlite3 build/minimax.db "SELECT needs_enrich, COUNT(*) FROM units GROUP BY needs_enrich;"
# 0|257   (zero failures)
```

## Model selection + A/B via eval

Get exact tags with `ollama ls`. The two cloud models we use:

| Model | `--model` tag |
|-------|---------------|
| MiniMax M3 | `minimax-m3:cloud` |
| GLM 5.2 | `glm-5.2:cloud` |

The CLI default is `--model minimax-m3:cloud` (`cli.py:58`). Both produced 257 units / 0
failures on the full book.

**A/B two models** by building a db with each (own `--db` + `--build-dir`) and scoring
top-k hit-rate over `corpora/aposd/cases.yaml`:

```bash
uv run --extra build gloss eval --db build/minimax.db   # => hit_rate=0.75 over n=16
uv run --extra build gloss eval --db build/glm52.db     # build both, compare, keep the winner
```

(Only `build/minimax.db` exists today, and it evals to **0.75 (12/16)** — `build/glm52.db`
above is illustrative; GLM 5.2 has no eval number on record yet.)

`eval` (`evalrun.py:26`) reports `hit_rate` over the 16 cases — a case hits if its top-k
contains a result matching `expect_section` or `expect_principle`. The **current**
in-repo `build/minimax.db` scores **0.75 (12/16)**. The handoff notes record an A/B of
devstral 0.75 (12/16) vs minimax **0.81 (13/16)**, but that 0.81 was scored against a
since-superseded/renamed db and isn't reproducible from what's checked in (devstral's db,
`build/aposd.db`, is now a 0-byte stub) — so treat the +1/16 lead as historical, not live.
The eval set wants strengthening before locking a model. See [DESIGN.md](DESIGN.md) for the
decision rationale.

## Troubleshooting

**`no such table: units_fts` / `no such table: units`** — the db is an empty 0-byte stub,
not a real corpus. `--db` is required (no default — `cli.py:49`,`:59`,`:68`), so this comes
from pointing `--db` at a stale 0-byte file like `build/aposd.db` that a prior run left
behind (never populated because the model build wrote elsewhere):

```bash
$ wc -c build/aposd.db
       0 build/aposd.db
$ sqlite3 build/aposd.db "SELECT COUNT(*) FROM units_fts;"
Error: in prepare, no such table: units_fts
```

Detect a **real** db (non-empty, has the table, has rows) before using it:

```bash
for db in build/*.db; do
  n=$(sqlite3 "$db" "SELECT COUNT(*) FROM units;" 2>/dev/null) \
    && echo "$db -> ${n:-0} units" || echo "$db -> EMPTY/invalid"
done
```

A full-book build is **257 units**. Point `--db` at a db that reports 257 (e.g.
`build/minimax.db`), not at the 0-byte stub.

**Nonzero enrichment failures** — see [Enrichment failure handling](#enrichment-failure-handling).
`--resume` retries only the failed units; if every unit fails, the model likely doesn't
support structured output — pick another model.

**`chapter '<x>' not found by detection/override`** (`build.py:86`) — `--chapter` didn't
match any detected chapter id. Run without `--chapter` to see detected ids in the
per-chapter log lines, or check `profile.chapter_re` / `profile.chapter_pages`.

**Resume read back the wrong model's data** — two models shared a `--build-dir`. Give
each model a distinct `--build-dir` (see [the per-model convention](#build-dir--per-model-db-convention))
and rebuild.

**`largest prompt … exceeds num_ctx cap 32768`** — a prompt's estimated tokens exceed the
cap; trim the situating-context section in `prompt.md` or raise the `cap` in
`estimate_num_ctx` (`build.py:36`).