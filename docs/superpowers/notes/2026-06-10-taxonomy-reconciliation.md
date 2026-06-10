# APOSD taxonomy reconciliation — two-facet vocabulary + gap list

**Date:** 2026-06-10. **Status:** proposal for human review (nothing committed).
**Artifact:** `corpora/aposd/taxonomy.yaml` (written alongside this note).

Reconciles the `software-design-philosophy` skill (the 6-principle distillation) against the
actual book it distills (21 chapters). Two metadata facets per retrieval unit:

- **`principle`** — COARSE, closed set = the skill's **6 principles**. What callers filter on.
- **`topic`** — FINE = the book's real chapter structure. The book covers more than the skill
  distills; the fine facet keeps everything, even when the coarse facet has no clean home.

Sources: `SKILL.md` + `references/*.md` for the coarse facet (vocabulary / diagnostics / red
flags quoted/derived, not invented); the PDF heading scan + the book's own "Summary of Design
Principles" (p.185) and "Summary of Red Flags" (p.186–187) for the fine facet.

---

## (a) Proposed two-facet vocabulary

### COARSE — 6 principles (fixed slugs)

| slug | name | one-line scope (from skill) |
|------|------|------------------------------|
| `complexity` | Complexity and Its Causes | symptoms (change amplification, cognitive load, unknown unknowns) + causes (dependencies, obscurity); complexity is incremental |
| `deep-modules` | Deep vs Shallow Modules | depth = functionality / interface cost; classitis; deep > shallow |
| `information-hiding` | Information Hiding and Leakage | encapsulate a decision in one module; leakage is a red flag; temporal decomposition |
| `general-purpose` | General-Purpose vs Special-Purpose Modules | "somewhat general-purpose"; "simplest interface that covers all current needs"; push complexity down; config-parameter antipattern |
| `comments` | Comments as Design Documentation | 4 comment types; interface comments first; self-documenting-code myth; why-not-what |
| `strategic-programming` | Strategic vs Tactical Programming | strategic vs tactical mindset; tactical tornado; 10–20% investment; startups |

Per-principle `vocabulary` / `diagnostics` / `red_flags` are enumerated in `taxonomy.yaml`. The
skill's `Quick-Diagnostic` table and `Common-Mistakes` table were split back out to the principle
each row belongs to, and cross-checked against the book's two summary appendices.

### FINE — 21 chapters → coarse principle

| ch | title | → principle | fit |
|----|-------|-------------|-----|
| 1 | Introduction (It's All About Complexity) | `complexity` | clean |
| 2 | The Nature of Complexity | `complexity` | clean |
| 3 | Working Code Isn't Enough | `strategic-programming` | clean |
| 4 | Modules Should Be Deep | `deep-modules` | clean |
| 5 | Information Hiding (and Leakage) | `information-hiding` | clean |
| 6 | General-Purpose Modules are Deeper | `general-purpose` | clean |
| 7 | Different Layer, Different Abstraction | `information-hiding` | **loose — gap** |
| 8 | Pull Complexity Downwards | `general-purpose` | **loose — gap** |
| 9 | Better Together Or Better Apart? | `deep-modules` | **loose — gap** |
| 10 | Define Errors Out Of Existence | `null` | **gap** |
| 11 | Design it Twice | `null` | **gap** |
| 12 | Why Write Comments? The Four Excuses | `comments` | clean |
| 13 | Comments Should Describe Things that Aren't Obvious | `comments` | clean |
| 14 | Choosing Names | `null` | **gap** |
| 15 | Write The Comments First | `comments` | clean |
| 16 | Modifying Existing Code | `strategic-programming` | clean |
| 17 | Consistency | `null` | **gap** |
| 18 | Code Should be Obvious | `null` | **gap (loose tie to `complexity`)** |
| 19 | Software Trends | `null` | **gap** |
| 20 | Designing for Performance | `null` | **gap** |
| 21 | Conclusion | `null` | back matter |

**Clean coverage: 9 of 19 design chapters** (ch.1–6, 12–13, 15–16). The skill's 6 principles map
1:1 onto the book's "core six" (complexity, strategic, deep, hiding, general, comments). Everything
else is a gap — either a loose fit or no fit.

---

## (b) GAP LIST — chapters the 6 principles do NOT cleanly cover

Recommendation per item: **fold-into-`<principle>`** (keep coarse set at 6, accept the chapter as a
`topic` under an existing principle) or **add-as-standalone-`topic`** (`principle: null`; lives only
in the fine facet, no coarse home). The coarse slug set is fixed at 6, so "fold" never adds a 7th
principle — it just routes the chapter's passages to the nearest existing one.

### Loose fits — currently folded, but arguable

1. **Ch.7 Different Layer, Different Abstraction** → **fold-into-`information-hiding`**.
   Reason: its red flags (pass-through method, pass-through variable, decorator leakage) are exactly
   the leakage vocabulary the skill already files under information-hiding. The "each layer should
   have a different abstraction" idea is its own book principle (Summary #9), but it has no coarse
   slug and is closest to hiding. *Keep folded; flag that "layer abstraction" is under-represented
   in the coarse vocabulary.*

2. **Ch.8 Pull Complexity Downwards** → **fold-into-`general-purpose`**.
   Reason: the skill literally lists "push complexity downward" inside the general-purpose principle,
   and the chapter's running example is the configuration-parameter antipattern (also under
   general-purpose). This is the *cleanest* of the loose fits — borderline clean. *Keep folded.*

3. **Ch.9 Better Together Or Better Apart?** → **fold-into-`deep-modules`** (with leakage spillover).
   Reason: the merge/split decision is the flip side of classitis (deep-modules), and the chapter's
   first rule ("bring together if information is shared") is information-hiding. It straddles two
   principles. Folded to deep-modules because the dominant payload is module granularity. *Acceptable,
   but the single-principle constraint loses the "separate general/special" sub-point (Summary #8).*

### No fit — recommend standalone `topic` (`principle: null`)

4. **Ch.10 Define Errors Out Of Existence** → **add-as-standalone-`topic`**.
   Reason: error/exception design is a first-class book principle (Summary #11) with zero presence in
   the skill's 6. No honest coarse home; folding into `complexity` would be a stretch. Keep retrievable
   via the fine facet.

5. **Ch.11 Design it Twice** → **add-as-standalone-`topic`**.
   Reason: a design *methodology* (generate multiple designs, compare), not a complexity lever. Nearest
   neighbor is `strategic-programming`, but it's about *how to design*, not invest-vs-shortcut. Standalone.

6. **Ch.14 Choosing Names** → **add-as-standalone-`topic`**.
   Reason: the skill touches naming only as a symptom of `complexity`/obscurity ("vague name" red flag,
   `numBytesReceived` not `n`). The book gives naming a whole chapter (Summary: "Vague Name" /
   "Hard to Pick Name" red flags). Strong enough to stand alone rather than be buried under complexity.
   *If a 7th principle were ever allowed, this is the top candidate.*

7. **Ch.17 Consistency** → **add-as-standalone-`topic`**.
   Reason: consistency (conventions, "if you do something a certain way, do all similar things the same
   way") is its own concept; no coarse slug fits. Standalone.

8. **Ch.18 Code Should be Obvious** → **add-as-standalone-`topic`** (loose tie to `complexity`).
   Reason: "obviousness" is the inverse of the `complexity` cause *obscurity*, so a `complexity` fold is
   defensible — but the book treats reader-centric obviousness (Summary #14, "Nonobvious Code" red flag)
   as distinct. Recommend standalone to avoid overloading `complexity`; revisit if callers want it folded.

9. **Ch.19 Software Trends** → **add-as-standalone-`topic`**.
   Reason: a survey chapter (OOP/inheritance, agile, unit tests, TDD, design patterns, getters/setters).
   It critiques other methodologies through the book's lens; it is not itself one of the 6 levers.
   Standalone, low retrieval priority.

10. **Ch.20 Designing for Performance** → **add-as-standalone-`topic`**.
    Reason: performance (measure first, design around the critical path) is orthogonal to the 6
    complexity-management principles. The book even notes the tension (general-purpose vs hot-path
    specialization). No coarse home. Standalone.

11. **Ch.21 Conclusion** → **exclude / back matter** (`null`, not a topic of interest).
    Reason: recap, no new design content. Listed for completeness; recommend not indexing as a topic.

### Summary of recommendations

- **Fold (keep coarse set = 6):** ch.7→`information-hiding`, ch.8→`general-purpose`, ch.9→`deep-modules`.
  All three already written that way in `taxonomy.yaml`; ch.8 is near-clean, ch.7 and ch.9 are the
  loosest and the ones to scrutinize.
- **Standalone `topic` (`principle: null`):** ch.10, 11, 14, 17, 18, 19, 20 (and ch.21 as excluded back
  matter). These are genuine book content with no coarse home; the fine facet preserves them.
- **Decision for the human:** the only real lever is whether to (a) accept `principle: null` for the 7
  no-fit chapters, or (b) relax the "6 fixed slugs" constraint to admit standout principles — **Choosing
  Names**, **Define Errors Out Of Existence**, and **Design it Twice** are the three the book itself
  elevates to top-level principles (Summary #11, #13-adjacent naming, and the errors principle) that the
  skill simply omits.
