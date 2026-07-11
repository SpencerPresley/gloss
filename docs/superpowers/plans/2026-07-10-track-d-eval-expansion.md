# Track D — eval-set expansion (31 → ~120–150 cases)

Read `2026-07-10-retrieval-tracks-overview.md` first. Do only this track.
Worktree-friendly: copy `build/minimax-v2.db` in (read-only use). No PDF, no model
calls needed — the generating LLM is *you*, the session running this track.

## Goal

The instrument every other track depends on. At n=31, one case ≈ 3.2 points of
hit@1 and real improvements land at p≈0.1–0.3 (measured: even hybrid-vs-lexical only
reached p=0.037 after centering). At n≈130+, a 3–4-case improvement becomes
certifiable and small-knob tuning (channel weights, rrf_k, rerank on/off) becomes
measurable instead of folklore. **This track lands first if possible** — other
tracks re-measure against the expanded set after it merges.

## Method

1. **Stratified unit sampling.** Read units from the db:
   `SELECT id, chapter, section, principle, type, text FROM units` — select ~50–60
   source units covering every chapter (≥2 each where possible), all six principles
   AND the null-principle chapters (10, 11, 14, 17–21), and a spread of types.
2. **Draft 2–3 situation-phrased queries per selected unit**, reading ONLY the
   verbatim `text` (plus chapter/section for the pin). Voice: a developer mid-task
   describing their own code — first person, symptom language, no book vocabulary,
   no quoting the passage. Mix lengths (terse 4-word gripes and full sentences).
3. **Leakage firewall (hard rules):**
   - Never SELECT or read `questions`, `key_terms`, `context_line`, `applies_when`
     while drafting. Query only the columns in step 1.
   - After drafting, screen: for each candidate, check 4-gram overlap against the
     stored metadata (`SELECT questions || key_terms || context_line FROM units`)
     and drop/rewrite candidates sharing any 4-gram with metadata. Keep the screen
     script in the commit.
4. **Do NOT filter candidates by whether the current system retrieves them.**
   Selecting cases the system already gets right (or wrong) biases the instrument.
   The only admissibility test is well-posedness: "does this passage actually answer
   this query, and is it the passage a competent reader would want?" If ambiguous
   between two units, pin both fields (expect_section + expect_principle are
   OR-matched) or drop the case.
5. **Pins:** prefer `expect_section` (the source unit's section); `expect_chapter`
   for sectionless chapters; add `expect_principle` only when any unit of that
   principle would genuinely satisfy the query.
6. **Output for human vetting:** write everything to
   `corpora/aposd/cases-candidates.yaml` (same schema as cases.yaml, one
   provenance comment per case: source unit id). Spencer vets; approved cases are
   then appended to `cases.yaml` under a marker comment
   `# --- synthetic set (generated from verbatim text, vetted 2026-07-10) ---` so
   curated-vs-synthetic can always be scored separately.
7. After merge into cases.yaml: re-baseline all three modes
   (`gloss eval --db build/minimax-v2.db --mode lexical|semantic|hybrid`), record
   the new baseline in DESIGN.md's experiment log (keep the n=31 history intact,
   labeled), and note that per-track numbers must be re-measured.

## Quality bar for a case

- Answerable from exactly this corpus (no external knowledge required).
- Symptom-phrased, not definitional ("my class just forwards calls…" not "what is a
  shallow module").
- One defensible expected answer (or explicitly OR-pinned).
- Not a paraphrase of an existing case (check the current 31 for near-duplicates).

## Files

`corpora/aposd/cases-candidates.yaml` (new), the leakage-screen script (e.g.
`corpora/aposd/screen_candidates.py`, stdlib), `tests/test_eval.py` well-formedness
still green after the eventual merge, `docs/DESIGN.md` experiment-log note.

Do not touch: `cases.yaml` directly (candidates file only — the merge happens after
human vetting), any `src/gloss/` code.
