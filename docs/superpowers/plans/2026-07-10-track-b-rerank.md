# Track B — LLM rerank of the top-k (opt-in)

Read `2026-07-10-retrieval-tracks-overview.md` first. **Claimed by the main session
2026-07-10** — this brief is the handoff record in case it's picked up elsewhere.

## Goal

Convert near-misses into #1s. Census on the 31-case eval (hybrid): 7 cases have the
right passage at rank 2–5 — a perfect top-5 reranker's ceiling is hit@1 0.71 → 0.935.
A reranker *reads* the candidates (RRF never does), so it can distinguish
surface-similar distractors (e.g. "naming a boolean" vs "flag parameter for one
caller").

Opt-in only: `--rerank` flag. Latency (~1–3s local model) is fine for an agent
workflow, unacceptable as a silent default. Explicit flag ⇒ fails loudly if the
model is unavailable (same philosophy as `--mode hybrid`).

## Design

New `src/docq/rerank.py`, stdlib-only (urllib against Ollama `/api/chat`,
`stream:false`, temperature 0):

- `rerank(db_path, query, hits, *, model, base_url, keep_text=600) -> list[dict]`
  — listwise: one prompt containing the query and each candidate as
  `id / citation / context_line / first ~600 chars of verbatim text`; instruction:
  "return ONLY a JSON array of ids, best-first, judging which passage actually
  answers the situation". Parse strictly; **on any failure (bad JSON, missing/extra
  ids, HTTP error) return the hits unchanged and note on stderr** — reranking must
  never break retrieval or drop/invent candidates. Reorder full hit dicts; tag each
  with `reranked: true` and preserve `channels`.
- Injectable `chat_fn` for tests (mirror the `embed_fn` seam).
- Default model `gemma4:e2b` (small/fast, installed); `--rerank-model` to override.
  Escalation candidates if quality disappoints: `gemma4:e4b`, `llama3.1:8b`,
  `gpt-oss:20b`. Avoid thinking models (latency, parse noise).

CLI: `retrieve --rerank [--rerank-model M]` composing with `--mode auto|hybrid|semantic`
(reranks whatever the mode returned, pool = the k requested… **use k=max(k,5)
candidates into the reranker, return top-k**). Eval: `--rerank` flag on `docq eval`
composing with `--mode`, so `--mode hybrid --rerank --vs hybrid` measures exactly the
reranker's contribution.

## Experiment protocol (gate before shipping as recommended usage)

1. `docq eval --db build/minimax-v2.db --mode hybrid --rerank --vs hybrid` (the
   `--vs` leg unreranked). Record hit@1/mrr deltas + p.
2. Count **demotions**: cases where unreranked rank was 1 and reranked is worse.
   Ship-gate: net hit@1 positive AND demotions ≤1. The 22 correct #1s are the
   asset; a reranker that churns them is worse than none.
3. Record wall-clock per query (target: ≤3s with the default model).
4. Try `gemma4:e2b` first; escalate model size only if the gate fails.
5. Record the outcome (adopted or rejected, with numbers) in DESIGN.md's experiment
   log either way. If adopted: document in CLI.md + a one-line pointer in the skill
   brief (track A owns the skill file — coordinate or leave a TODO note in the log).

## Files

`src/docq/rerank.py` (new), `src/docq/cli.py` (flags), `tests/test_rerank.py`
(fake chat_fn: reorder happy path; malformed-JSON fallback; missing-id fallback;
HTTP-error fallback; candidates never dropped/invented), `tests/test_stdlib_contract.py`
(add `docq.rerank` to the import set), `docs/CLI.md`, `docs/DESIGN.md`.

Do not touch: `cases.yaml`, segmentation/enrichment, `vectors.py` internals.
