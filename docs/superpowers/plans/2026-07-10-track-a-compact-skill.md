# Track A — compact output, `gloss show`, skill wiring

Read `2026-07-10-retrieval-tracks-overview.md` first. Do only this track.
Runs fine in a worktree with no corpus (tests need neither PDF nor db; for manual
spot-checks copy `build/minimax-v2.db` in per the overview).

## Goal

The recovery mechanism for the irreducible wrong-#1 tail, with the steering encoded
**once in the skill** instead of per-use by the user: Claude gets full text for #1,
cheap one-line previews for the runners-up, and a way to expand a runner-up only when
the previews say #1 is off. Secondary: k=3 stops costing ~3 full passages of tokens.

This track must NOT change ranking. Output shape and skill instructions only.

## Deliverables

### 1. `retrieve --compact` (text mode only; `--json` unchanged)

Rank #1 renders exactly as today (citation header + verbatim text). Ranks 2..k render
as one preview line each, no verbatim text:

```
[deep-modules §4.6 p.45] (red_flag via lex#1+sem#1)
<full verbatim text of hit #1>

more: id=61 [general-purpose §6.3 p.52] (example via lex#3+sem#2) — A general-purpose changePosition API absorbs many UI operations…
more: id=97 [ §10.7 p.91] (definition via sem#3) — Exception aggregation handles many exceptions with one handler…
```

Preview line = `more: id=<id> [<citation>] (<type> via <tags>) — <context_line>`.
The preview uses the generated `context_line` (a paraphrase) — acceptable because it
is labeled as a pointer, never presented as the passage; the verbatim promise applies
to passage bodies. Truncate context_line at ~140 chars if needed.

### 2. `gloss show <id> --db DB`

Print one unit in full by id (same rendering as a retrieve hit: citation header +
verbatim text; include `applies_when` as a trailing line — it helps an agent confirm
fit). Errors cleanly on unknown id (non-zero exit, message). Implement the lookup as
`store.get_unit(db_path, unit_id) -> dict | None` (stdlib, one SELECT).

### 3. Skill wiring

Update `.claude/skills/software-design-philosophy/SKILL.md` so the skill actually
uses gloss (DESIGN.md §14 marks this deferred — this closes it). Instructions to
encode, in the skill's own voice/format:

- When the corpus db exists (check `build/minimax-v2.db`, else the CLAUDE.md-named
  live db), answer design questions by querying it:
  `uv run gloss retrieve "<symptom-phrased query>" --db build/minimax-v2.db -k 3 --compact`
- Phrase queries as a developer's symptom ("callers have to call setup in the right
  order"), not book vocabulary; pass `--principle <slug>` when the principle is
  known.
- Trust rules: `via lex#N+sem#M` on #1 with both channels present = high confidence.
  Channels disagree, or a `more:` preview matches the situation better → run
  `gloss show <id>` for that preview before answering. Never answer from a preview
  line's paraphrase — expand it first (verbatim only).
- No hits / weak fit → rephrase once with different symptom vocabulary; else fall
  back to the skill's bundled references.
- Fallback when the db is absent: current skill behavior (references/*.md).

## Files

`src/gloss/cli.py` (flag + `show` subcommand + `_format_hit`/`_format_preview`),
`src/gloss/store.py` (`get_unit`), `tests/test_cli.py` + `tests/test_store.py`
(subprocess test for `--compact` and `show`, incl. unknown-id exit), `docs/CLI.md`
(both commands, preview-line format), skill file.

Do not touch: `vectors.py`, `evalrun.py`, `cases.yaml`, segmentation/enrichment.

## Acceptance

- `pytest -q` green (50/9-skip without PDF is fine); `test_stdlib_contract` green.
- `retrieve --compact --json` behaves identically to `--json` today (compact is a
  text-mode concern) — documented.
- CLI.md documents both; skill file references only commands/flags that exist.
- No ranking change: `gloss eval` numbers identical before/after (spot-check).
