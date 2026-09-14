# Startup Guide — getting to a queryable APOSD corpus

A repeatable runbook for going from a fresh checkout to a working `docq retrieve`.
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
A full-book build is **197 units** under current segmentation (a 257-unit db is from
the pre-code-attach rules — usable but stale; rebuild to pick up merged code units).
If you see 197, skip to Step 4 and point `--db` at that file. To check whether it's
also embedded for hybrid retrieval: `sqlite3 <db> "SELECT COUNT(*) FROM vectors;"`
(~1,500 = embedded; an error = lexical-only, see Step 4).

---

## Step 3 — Build the corpus (needs the `build` extra + an Ollama model)

Build is a single end-to-end command: parse → segment → enrich (LLM) → SQLite/FTS5.
There is **no separate "analyze" step** — `docq build` does it all and writes the `.db`.

**Pick a model.** Get exact tags with `ollama ls`. The two we use:

| Model           | `--model` tag      | suggested `--db` / `--build-dir`        |
|-----------------|--------------------|-----------------------------------------|
| MiniMax M3      | `minimax-m3:cloud` | `--db build/minimax-v2.db --build-dir build/minimax-v2` |
| GLM 5.2         | `glm-5.2:cloud`    | `--db build/glm52.db   --build-dir build/glm52`   |

Always give each model its **own `--build-dir`** so their per-chapter JSONL checkpoints
don't overwrite each other.

**Full-book build** (188 pages; concurrent enrichment):
```bash
# MiniMax M3
uv run --extra build docq build \
  --model minimax-m3:cloud --workers 8 \
  --db build/minimax-v2.db --build-dir build/minimax-v2

# …or GLM 5.2
uv run --extra build docq build \
  --model glm-5.2:cloud --workers 8 \
  --db build/glm52.db --build-dir build/glm52
```

Expected tail: `built 197 units (0 enrichment failures) -> build/<name>.db`.
A nonzero failure count usually means the model ignored structured output — the build
warns and you can `--resume` to retry only the failed units.

**Smoke-test one chapter first** (fast, cheap) before committing to the whole book:
```bash
uv run --extra build docq build --chapter 6 \
  --model minimax-m3:cloud --db build/ch6.db --build-dir build/ch6
```

**Resume an interrupted/partial build** (keeps existing checkpoints, re-enriches only
failed units):
```bash
uv run --extra build docq build --model minimax-m3:cloud --workers 8 \
  --db build/minimax-v2.db --build-dir build/minimax-v2 --resume
```

---

## Step 4 — (Recommended) Embed for hybrid retrieval

One pass writes per-unit vectors into the same `.db`, enabling the hybrid
(BM25 + vector, RRF-fused) mode that `retrieve` uses by default when available.
Needs the embedding model pulled once (`ollama pull embeddinggemma`):

```bash
uv run docq embed --db build/minimax-v2.db
# embedded 197 units -> 1493 vectors (dim=768, model=embeddinggemma:latest) ...
```

~16 seconds. **Re-run this after every `docq build`** — a build overwrites the db
file, so vectors don't survive it. Skipping this step is fine: retrieval works
lexical-only, with nothing running.

---

## Step 5 — Verify retrieval works

```bash
uv run docq retrieve "should I make this API general purpose" \
  --db build/minimax-v2.db -k 3
```
You should get cited passages (`[principle §section p.N] (type via lex#1+sem#2)` +
verbatim text), not `(no matches)` and not a traceback. The `via` tag shows each
retrieval channel's rank (both = two independent signals agree) and only appears when
the db is embedded and Ollama is up — without them retrieval silently runs lexical-only
and the tag is absent. Add `--json` for structured output, filter with
`--principle <slug>` / `--type <t>`, and force a mode with
`--mode lexical|hybrid|semantic` (valid values in `CLAUDE.md`).

---

## Step 6 — (Optional) Score / compare models

Eval is the only way to answer "is this model's corpus actually good" — hit@k / hit@1 /
MRR over `corpora/aposd/cases.yaml` (31 cases):
```bash
uv run --extra build docq eval --db build/minimax-v2.db                # lexical
uv run --extra build docq eval --db build/minimax-v2.db --mode hybrid  # needs Step 4
```
Current numbers (k=5): lexical `hit@5=0.84 hit@1=0.55 mrr=0.66`, hybrid
`hit@5=0.94 hit@1=0.71 mrr=0.80`. Add `--vs lexical` to a hybrid eval to get a
paired significance test between the modes.

---

## Quick decision tree

```
PDF at resources/…compress.pdf?  ── no ──► Step 1 (curl)
            │ yes
build/<model>.db with 197 units? ── no ──► Step 3 (build, minimax-m3 or glm-5.2)
            │ yes
db has vectors (COUNT(*) FROM vectors)? ── no ──► Step 4 (docq embed, ~16s)
            │ yes
            ▼
   retrieve --db build/<model>.db   (Step 5)
```
