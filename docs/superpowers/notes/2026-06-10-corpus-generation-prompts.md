# Corpus-generation prompts (reusable)

`gloss` is corpus-agnostic; onboarding a new corpus reuses the same LLM steps. This file
records the prompts so they're a reference, not reinvented each time. Swap the **[bracketed]**
parts for the new corpus.

---

## 1. Taxonomy reconciliation (run once per corpus)

**Role:** reconcile a controlling *skill/framework* against the *source text* it distills, to
produce the two-facet controlled vocabulary (`taxonomy.yaml`) + a gap report. Analysis only;
the human reviews before it's built on.

**Prompt used for APOSD (2026-06-10):**

> You are producing the controlled vocabulary for a retrieval system, by reconciling a
> [framework/skill] against the [source text] it distills. This is analysis + two config
> artifacts — NOT code, and do NOT commit (a human reviews your output first).
>
> Background: we're building `gloss`, a corpus-agnostic engine that turns a source text into a
> structured, lexically-retrievable corpus of cited passages. Each unit carries two metadata
> facets: **`principle`** — COARSE, a closed set = the [framework]'s [N] principles (what
> callers filter on, the vocabulary the index aligns to); **`topic`** — FINE = the text's
> actual chapter structure (the text covers more than the framework distills; the fine facet
> keeps everything).
>
> Inputs: read [skill path: SKILL.md + references/*.md] and extract, per principle, its name,
> characteristic **vocabulary/terms**, the **diagnostic questions**, and the **red-flags**.
> The fixed coarse slugs are: [slug list]. Get the text's structure by [heading-scan command
> or TOC]; cross-check against any "Summary of Principles"/"Summary of Red Flags" pages.
>
> Deliverables (write, don't commit): `corpora/[name]/taxonomy.yaml` (schema: `principles:`
> list with slug/name/vocabulary/diagnostics/red_flags; `topics:` list mapping every chapter to
> one coarse slug or `null`) and a reconciliation report with an explicit **GAP LIST** — every
> chapter the coarse principles don't cleanly cover, each marked fold-into-`<slug>` or
> add-as-standalone-`topic`, with a one-line reason.
>
> Constraints: ground everything in the ACTUAL framework text + ACTUAL headings (derive, don't
> invent); coarse slugs are fixed; every chapter → exactly one slug or `null`. No code, no
> commit. Return: chapters found, the coarse→fine mapping, the GAP LIST with fold-vs-add, and
> the file paths.

**What worked well:** grounding the coarse facet in the skill's Quick-Diagnostic + Common-
Mistakes tables (split back to each principle) and the fine facet in the heading scan + the
book's own summary pages. Rich `red_flags`/`diagnostics` are an asset (lexical target vocab),
not bloat — but they're a *palette*, see the enrichment note below.

**Watch-outs:** a few extracted terms are book-trivia (formulas, author names) that won't
appear in real queries — harmless. The fixed-N-principles constraint forces `null` on chapters
the framework omits; that's the human's promote-vs-leave-null decision.

---

## 2. Enrichment prompt + system prompt (per-unit, build time)

_To be recorded when Task 5 lands. Key disciplines already decided:_
- Feed only the **relevant principle's** taxonomy entry per unit (not all of them) — compact, no cross-principle bleed.
- Vocabulary is a **palette, prefer where it fits the passage** — do NOT stuff every term (BM25 saturation + length-normalization penalize padding).
- Substantive content from the **passage only** ("use only information present; if unknown, omit"); the card supplies which-principle + preferred phrasing, never facts.
- `temperature=0`; real **system prompt** (role + "return primary source, never paraphrase" guardrail + symptom-vocabulary instruction).
