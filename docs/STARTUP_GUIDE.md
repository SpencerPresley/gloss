# Startup Guide — getting to a queryable APOSD corpus

A repeatable runbook for going from a fresh checkout to a working `gloss retrieve`.
Each section is **check first, act only if needed** — safe to re-run. For the terse
command reference see [`../CLAUDE.md`](../CLAUDE.md); for the why/overview see
[`../README.md`](../README.md).

The source PDF and built `.db` are **gitignored** — never in the repo. This guide is
how you reconstruct them.

---

## Step 1 — Source PDF present?

The build reads a **hardcoded path** (`corpora/aposd/profile.py` → `corpus_path`):

```
resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf
```

The filename and location must match exactly — the build doesn't take a `--pdf` flag.

**Check:**
```bash
ls -la resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf
```

**If missing, fetch it** (≈1.6 MB) from the Awesome-CS-Books mirror, saving to the exact
path the profile expects:
```bash
mkdir -p resources
curl -L -o resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf \
  'https://github.com/rocky-191/Awesome-CS-Books/raw/master/SoftwareEngineering/Architecture/2018-John%20Ousterhout-A%20Philosophy%20of%20Software%20Design.pdf'
```
Verify it's a real PDF, not an HTML error page:
```bash
file resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf   # => PDF document
```

---

## Step 2 — Is a corpus already built?

Builds are **model-named** so different models don't clobber each other, so the live db
is usually `build/<model>.db` (e.g. `build/minimax.db`), **not** `build/aposd.db`.
`build/aposd.db` is often a 0-byte stub — opening it gives
`OperationalError: no such table: units_fts`.

**Check what real dbs exist** (non-empty + has the FTS table + has rows):
```bash
for db in build/*.db; do
  n=$(sqlite3 "$db" "SELECT COUNT(*) FROM units;" 2>/dev/null) \
    && echo "$db -> ${n:-0} units" || echo "$db -> EMPTY/invalid"
done
```
A full-book build is **257 units**. If you see that, you're done — skip to Step 4 and
point `--db` at that file.

---

## Step 3 — Build the corpus (needs the `build` extra + an Ollama model)

Build is a single end-to-end command: parse → segment → enrich (LLM) → SQLite/FTS5.
There is **no separate "analyze" step** — `gloss build` does it all and writes the `.db`.

**Pick a model.** Get exact tags with `ollama ls`. The two we use:

| Model           | `--model` tag      | suggested `--db` / `--build-dir`        |
|-----------------|--------------------|-----------------------------------------|
| MiniMax M3      | `minimax-m3:cloud` | `--db build/minimax.db --build-dir build/minimax` |
| GLM 5.2         | `glm-5.2:cloud`    | `--db build/glm52.db   --build-dir build/glm52`   |

Always give each model its **own `--build-dir`** so their per-chapter JSONL checkpoints
don't overwrite each other.

**Full-book build** (188 pages; concurrent enrichment):
```bash
# MiniMax M3
uv run --extra build gloss build \
  --model minimax-m3:cloud --workers 8 \
  --db build/minimax.db --build-dir build/minimax

# …or GLM 5.2
uv run --extra build gloss build \
  --model glm-5.2:cloud --workers 8 \
  --db build/glm52.db --build-dir build/glm52
```

Expected tail: `built 257 units (0 enrichment failures) -> build/<name>.db`.
A nonzero failure count usually means the model ignored structured output — the build
warns and you can `--resume` to retry only the failed units.

**Smoke-test one chapter first** (fast, cheap) before committing to the whole book:
```bash
uv run --extra build gloss build --chapter 6 \
  --model minimax-m3:cloud --db build/ch6.db --build-dir build/ch6
```

**Resume an interrupted/partial build** (keeps existing checkpoints, re-enriches only
failed units):
```bash
uv run --extra build gloss build --model minimax-m3:cloud --workers 8 \
  --db build/minimax.db --build-dir build/minimax --resume
```

---

## Step 4 — Verify retrieval works

```bash
uv run gloss retrieve "should I make this API general purpose" \
  --db build/minimax.db -k 3
```
You should get cited passages (`[principle §section p.N] (type)` + verbatim text), not
`(no matches)` and not a traceback. Add `--json` for structured output, and filter with
`--principle <slug>` / `--type <t>` (valid values in `CLAUDE.md`).

---

## Step 5 — (Optional) Score / compare models

Eval is the only way to answer "is this model's corpus actually good" — top-k hit-rate
over `corpora/aposd/cases.yaml` (16 cases):
```bash
uv run --extra build gloss eval --db build/minimax.db
uv run --extra build gloss eval --db build/glm52.db    # build both, compare, keep the winner
```

---

## Quick decision tree

```
PDF at resources/…compress.pdf?  ── no ──► Step 1 (curl)
            │ yes
build/<model>.db with 257 units? ── no ──► Step 3 (build, minimax-m3 or glm-5.2)
            │ yes
            ▼
   retrieve --db build/<model>.db   (Step 4)
```
