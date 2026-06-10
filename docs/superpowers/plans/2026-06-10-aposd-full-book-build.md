# APOSD Full-Book Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the whole *A Philosophy of Software Design* book into one `aposd.db` — every chapter enriched with its own principle card — by detecting chapters dynamically from the parsed structure instead of hardcoding page ranges.

**Architecture:** Add a `chapter_re` to the corpus `Profile` (mirroring the existing `section_re`); a new `split_chapters` engine function slices parsed Elements into per-chapter spans by chapter-marker headings. `run_build` loops those spans (plus explicit appendix ranges for the book's summary pages), enriches each with its taxonomy card, accumulates rows, and writes one db. Hardcoded `chapter_pages` is demoted to an optional override for corpora whose structure defeats detection. Concurrency is added to enrichment last, after a correct serial full-book build exists.

**Tech Stack:** Python 3.12, uv, PyMuPDF, langchain-ollama (build-only), Pydantic, SQLite/FTS5 (stdlib), pytest. Build model: `devstral-small-2:24b-cloud` (proven, cheap); `minimax-m3:cloud` reserved for a final quality pass.

**Spec:** `docs/superpowers/specs/2026-06-10-aposd-embedded-design.md`
**Prior plan (Ch.6 slice, Tasks 0–10):** `docs/superpowers/plans/2026-06-10-aposd-embedded.md`

---

## Decisions locked (this session, with Spencer)

- **`chapter_re` detection is the default**; `chapter_pages` is demoted to an optional override (used only when detection fails). Verified: `r"^Chapter\s+(\d+)"` finds all 21 chapters with correct numbers/pages and auto-excludes title page / Contents / Preface / Index / Summaries / About.
- **Do not over-engineer for arbitrary books.** One regex default + one override escape hatch is the entire generality budget. No speculative multi-strategy detection.
- **Index the book's summary appendices** ("Summary of Design Principles" p.185, "Summary of Red Flags" p.186–187) as explicit `principle: null` entries — they are a curated red-flag bank in the book's own query vocabulary.
- **`principle: null` chapters** (10, 11, 14, 17, 18, 19, 20) are indexed topic-only (enriched with an empty card). Ch.21 (Conclusion) and back matter are excluded by detection.
- **Model:** devstral for all iteration + the first full build; minimax only for a final compare on a strengthened eval set, after a 1-call sanity check.

## Conventions

- Run everything through uv: `uv run --extra build pytest …`, `uv run --extra build gloss build …`. The `retrieve` path stays stdlib-only: `uv run gloss retrieve …`.
- Google-style docstrings on every public module/class/function.
- Engine (`src/gloss/`) stays corpus-agnostic; corpus specifics live in `corpora/aposd/`.
- Commit after every task with the message in its final step.
- The full test suite must stay green: `uv run --extra build pytest -q` (currently 28 passing).

---

## Task 1: Dynamic chapter detection (`chapter_re` + `split_chapters`)

Add the chapter-marker regex to `Profile` and an engine function that slices parsed Elements into per-chapter spans. Pure functions — no model, no network.

**Files:**
- Modify: `src/gloss/profile.py` (add `chapter_re`, `appendices` fields)
- Modify: `src/gloss/segment.py` (add `split_chapters`)
- Modify: `corpora/aposd/profile.py` (set `chapter_re`; drop `chapter_pages`)
- Modify: `tests/test_segment.py` (add `chapter_re` to the test profile fixture; new tests)
- Modify: `tests/test_build.py` (`test_load_profile_has_aposd_values` no longer asserts `chapter_pages`)

- [ ] **Step 1: Add the `chapter_re` and `appendices` fields to `Profile`**

In `src/gloss/profile.py`, add two fields after `section_re` (both have defaults, so existing keyword constructions stay valid). Replace the dataclass body:

```python
    corpus_path: Path
    code_font: str
    head_font: str
    chapter_size: float
    section_size: float
    figure_min_area: int
    section_re: str
    chapter_re: str = ""
    chapter_pages: dict[str, tuple[int, int]] = field(default_factory=dict)
    appendices: dict[str, tuple[int, int]] = field(default_factory=dict)
```

And extend the docstring `Attributes:` block with:

```
        chapter_re: Regex whose first group captures a chapter id in a level-1 heading
            (e.g. ``r"^Chapter\\s+(\\d+)"``). When set, chapters are detected dynamically
            from the parsed headings; when empty, ``chapter_pages`` must supply ranges.
        chapter_pages: Optional override mapping chapter id -> (first_page, last_page),
            1-based inclusive. Used only when ``chapter_re`` detection is unavailable.
        appendices: Map of appendix id -> (first_page, last_page) for non-chapter
            spans to index anyway (e.g. summary pages); enriched with no principle card.
```

- [ ] **Step 2: Write the failing tests for `split_chapters`**

Add to `tests/test_segment.py`. First, extend the existing `_profile()` helper to carry `chapter_re` (find the `Profile(...)` call at line ~16 and add `chapter_re=r"^Chapter\s+(\d+)"`). Then add:

```python
def test_split_chapters_groups_by_marker():
    from gloss.parse import Element
    from gloss.segment import split_chapters
    els = [
        Element("heading", "Preface", 9, 1),            # front matter -> dropped
        Element("para", "preface body", 9),
        Element("heading", "Chapter 1", 13, 1),
        Element("heading", "Introduction", 13, 1),      # title line, not a boundary
        Element("para", "intro body", 13),
        Element("heading", "1.1 Something", 14, 2),
        Element("para", "more intro", 14),
        Element("heading", "Chapter 2", 18, 1),
        Element("heading", "The Nature of Complexity", 18, 1),
        Element("para", "ch2 body", 18),
        Element("heading", "Index", 178, 1),            # back matter -> stays in last span
    ]
    chapters = split_chapters(els, _profile())
    assert [cid for cid, _ in chapters] == ["1", "2"]
    by_id = dict(chapters)
    assert any(e.text == "intro body" for e in by_id["1"])
    assert any(e.text == "1.1 Something" for e in by_id["1"])
    assert all(e.text != "preface body" for e in by_id["1"])     # front matter excluded
    assert any(e.text == "ch2 body" for e in by_id["2"])


def test_split_chapters_empty_without_marker():
    from gloss.parse import Element
    from gloss.profile import Profile
    from gloss.segment import split_chapters
    no_re = Profile(corpus_path=Path("x"), code_font="Typewriter", head_font="NimbusSanL-Bol",
                    chapter_size=20.0, section_size=16.0, figure_min_area=5000,
                    section_re=r"^(\d+\.\d+)")  # chapter_re defaults to ""
    els = [Element("heading", "Chapter 1", 1, 1), Element("para", "body", 1)]
    assert split_chapters(els, no_re) == []
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run --extra build pytest tests/test_segment.py -v`
Expected: FAIL (`ImportError: cannot import name 'split_chapters'`).

- [ ] **Step 4: Implement `split_chapters`**

Add to `src/gloss/segment.py` (after the imports; it uses `re`, `Element`, `Profile`, all already imported):

```python
def split_chapters(elements: list[Element], profile: Profile) -> list[tuple[str, list[Element]]]:
    """Slice parsed Elements into per-chapter spans by chapter-marker headings.

    A level-1 heading whose text matches ``profile.chapter_re`` starts a new chapter;
    the regex's first group is the chapter id. Elements before the first marker (front
    matter) are dropped. Each chapter's span runs to the next marker — or to the end of
    the document for the last one, where trailing back matter (e.g. an "Index" heading)
    is left for the segment stage to trim via its stop-at-next-level-1 rule.

    Args:
        elements: Parsed structural Elements in reading order (whole document).
        profile: Carries ``chapter_re``; an empty ``chapter_re`` disables detection.

    Returns:
        ``[(chapter_id, [Element, ...]), ...]`` in document order, or ``[]`` when
        ``chapter_re`` is empty or no marker matches.
    """
    if not profile.chapter_re:
        return []
    marker = re.compile(profile.chapter_re)
    starts: list[tuple[str, int]] = []  # (chapter_id, element index)
    for i, el in enumerate(elements):
        if el.kind == "heading" and el.level == 1:
            m = marker.match(el.text.strip())
            if m:
                starts.append((m.group(1), i))
    spans: list[tuple[str, list[Element]]] = []
    for n, (cid, start) in enumerate(starts):
        end = starts[n + 1][1] if n + 1 < len(starts) else len(elements)
        spans.append((cid, elements[start:end]))
    return spans
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run --extra build pytest tests/test_segment.py -v`
Expected: PASS (existing segment tests + the two new ones).

- [ ] **Step 6: Point the APOSD instance at detection; drop its hardcoded pages**

In `corpora/aposd/profile.py`, replace the `chapter_pages={"6": (50, 58)},` line with the detection regex:

```python
    section_re=r"^(\d+\.\d+)",
    chapter_re=r"^Chapter\s+(\d+)",
)
```

(Delete the `chapter_pages={"6": (50, 58)},` line entirely — detection replaces it.)

- [ ] **Step 7: Update `test_load_profile_has_aposd_values`**

In `tests/test_build.py`, the assertion on `chapter_pages["6"]` is now stale. Replace that test body with:

```python
def test_load_profile_has_aposd_values():
    p = load_profile(_INSTANCE)
    assert p.code_font == "Typewriter" and p.chapter_re == r"^Chapter\s+(\d+)"
    assert p.chapter_pages == {}                 # detection, not hardcoded ranges
    assert p.corpus_path.name.endswith(".pdf")
```

- [ ] **Step 8: Add a real-PDF integration test (skips if the corpus is absent)**

Add to `tests/test_segment.py`:

```python
def test_split_chapters_real_pdf_finds_all_21(corpus_path):
    from gloss.build import load_profile
    from gloss.parse import parse_pdf
    from gloss.segment import split_chapters
    profile = load_profile(Path("corpora/aposd"))
    els = parse_pdf(corpus_path, None, None, profile)
    chapters = split_chapters(els, profile)
    assert [cid for cid, _ in chapters] == [str(n) for n in range(1, 22)]
```

`corpus_path` is the existing fixture in `tests/conftest.py` (skips when the PDF is missing). `tests/test_segment.py` already imports `from pathlib import Path`; if not, add it.

- [ ] **Step 9: Run the full suite**

Run: `uv run --extra build pytest -q`
Expected: PASS (all prior tests + new ones; the real-PDF test runs locally where the PDF exists).

- [ ] **Step 10: Commit**

```bash
git add -A && git commit -m "feat(segment): detect chapters via chapter_re; demote chapter_pages to override"
```

---

## Task 2: Multi-chapter `run_build` (whole-book → one db)

Refactor `run_build` so a build with no `--chapter` loops every detected chapter, enriches each with its own principle card, and accumulates into one db. Add `extractor` and `build_dir` injection points so the loop is testable without a model. Single-chapter builds keep working (filtered from the same path).

**Files:**
- Modify: `src/gloss/build.py` (`run_build`)
- Test: `tests/test_build.py`

- [ ] **Step 1: Write the failing test (StubExtractor over the real PDF — no model)**

Add to `tests/test_build.py`:

```python
def test_run_build_whole_book_accumulates_all_chapters(tmp_path, corpus_path):
    import sqlite3
    from gloss.build import run_build
    from gloss.extract import StubExtractor
    stub = StubExtractor({"principle": "general-purpose", "type": "rationale",
                          "context_line": "c", "applies_when": "a",
                          "key_terms": ["k"], "questions": ["q?"]})
    db = tmp_path / "aposd.db"
    rows = run_build(chapter=None, model="stub", db=db, resume=False,
                     extractor=stub, build_dir=tmp_path / "build")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    chapters = {r["chapter"] for r in con.execute("SELECT DISTINCT chapter FROM units")}
    con.close()
    assert {str(n) for n in range(1, 22)} <= chapters   # every design chapter present
    assert len(rows) > 100                              # full book, not one slice


def test_run_build_single_chapter_still_works(tmp_path, corpus_path):
    import sqlite3
    from gloss.build import run_build
    from gloss.extract import StubExtractor
    stub = StubExtractor({"principle": "general-purpose", "type": "rationale",
                          "context_line": "c", "applies_when": "a",
                          "key_terms": ["k"], "questions": ["q?"]})
    db = tmp_path / "ch6.db"
    rows = run_build(chapter="6", model="stub", db=db, resume=False,
                     extractor=stub, build_dir=tmp_path / "build")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    chapters = {r["chapter"] for r in con.execute("SELECT DISTINCT chapter FROM units")}
    con.close()
    assert chapters == {"6"}
    assert len(rows) >= 15
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra build pytest tests/test_build.py::test_run_build_whole_book_accumulates_all_chapters -v`
Expected: FAIL (`run_build()` got an unexpected keyword argument `extractor`).

- [ ] **Step 3: Rewrite `run_build`**

Replace the `run_build` function in `src/gloss/build.py` with this version. Add `from .segment import segment, split_chapters` (currently only `segment` is imported) and keep the other imports.

```python
def run_build(chapter, model, db, resume, instance: Path = _DEFAULT_INSTANCE,
              extractor=None, build_dir: Path = Path("build")):
    """Build the corpus db: one chapter (``chapter`` set) or the whole book (``chapter`` None).

    Chapters are detected via ``profile.chapter_re`` (or taken from ``profile.chapter_pages``
    when that override is set). Each chapter is enriched with its own principle card; appendix
    ranges are indexed with no card. All rows accumulate into one db.

    Args:
        chapter: Chapter id to build, or None for the whole book + appendices.
        model: Ollama model tag for enrichment (ignored when ``extractor`` is given).
        db: Output db path.
        resume: Keep existing per-chapter checkpoints instead of wiping them.
        instance: Corpus instance dir (profile/taxonomy/prompt).
        extractor: Optional pre-built StructuredExtractor (tests inject a stub); when None,
            one ``OllamaExtractor`` is built and reused across all chapters.
        build_dir: Root for per-chapter JSONL checkpoints.

    Returns:
        All enrichment rows across every built chapter.
    """
    profile = load_profile(instance)
    taxonomy = load_taxonomy(Path(instance) / "taxonomy.yaml")
    system, template = load_prompt(instance)

    # 1) Resolve per-chapter element spans: explicit override, else dynamic detection.
    if profile.chapter_pages:
        specs = [(cid, parse_pdf(profile.corpus_path, first, last, profile))
                 for cid, (first, last) in profile.chapter_pages.items()]
    else:
        whole = parse_pdf(profile.corpus_path, None, None, profile)
        specs = split_chapters(whole, profile)
    if chapter is None:
        specs += [(aid, parse_pdf(profile.corpus_path, first, last, profile))
                  for aid, (first, last) in profile.appendices.items()]
    else:
        specs = [(cid, els) for cid, els in specs if cid == chapter]
        if not specs:
            raise SystemExit(f"chapter {chapter!r} not found by detection/override")

    # 2) Segment each span + build prompts; size num_ctx once over the whole build.
    plans = []          # (chapter_id, units, section_texts, card)
    all_prompts: list[str] = []
    for cid, els in specs:
        units, section_texts = segment(els, profile, cid)
        principle = principle_for_chapter(taxonomy, cid)
        card = card_for(taxonomy, principle) if principle else ""
        plans.append((cid, units, section_texts, card))
        all_prompts += [build_prompt(u, section_texts.get(u.section, ""), card, template)
                        for u in units]

    num_ctx = estimate_num_ctx(all_prompts, system)
    total = sum(len(p[1]) for p in plans)
    print(f"chapters={len(plans)} units={total} num_ctx={num_ctx} model={model}")

    # 3) One extractor for the whole build (method probed/pinned once); enrich + accumulate.
    if extractor is None:
        extractor = OllamaExtractor(model, num_ctx=num_ctx)
    all_rows: list[dict] = []
    for cid, units, section_texts, card in plans:
        checkpoint = Path(build_dir) / f"ch{cid}" / "units.jsonl"
        if not resume and checkpoint.exists():
            checkpoint.unlink()
        rows = enrich_units(units, section_texts, extractor, card=card, template=template,
                            system=system, checkpoint=checkpoint)
        failed = sum(r["needs_enrich"] for r in rows)
        print(f"  ch{cid}: {len(rows)} units ({failed} failed) principle={card.splitlines()[0] if card else 'null'}")
        all_rows += rows

    failed = sum(r["needs_enrich"] for r in all_rows)
    if failed:
        print(f"WARNING: {failed}/{len(all_rows)} units failed enrichment — does model "
              f"{model!r} support structured output?")
    from .store import build_db
    build_db(all_rows, Path(db))
    print(f"built {len(all_rows)} units ({failed} enrichment failures) -> {db}")
    return all_rows
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --extra build pytest tests/test_build.py -v`
Expected: PASS (both new tests + the three existing build tests). The whole-book stub build runs ~500 instant stub calls.

- [ ] **Step 5: Run the full suite**

Run: `uv run --extra build pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(build): whole-book multi-chapter build into one db (per-chapter cards)"
```

---

## Task 3: Index the summary appendices

Populate the APOSD instance's `appendices` so the book's "Summary of Design Principles" (p.185) and "Summary of Red Flags" (p.186–187) get indexed as `principle: null` units. The `run_build` loop already consumes `profile.appendices` (Task 2).

**Files:**
- Modify: `corpora/aposd/profile.py` (add `appendices`)
- Test: `tests/test_build.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_build.py`:

```python
def test_run_build_indexes_summary_appendices(tmp_path, corpus_path):
    import sqlite3
    from gloss.build import run_build
    from gloss.extract import StubExtractor
    stub = StubExtractor({"principle": "general-purpose", "type": "red_flag",
                          "context_line": "c", "applies_when": "a",
                          "key_terms": ["k"], "questions": ["q?"]})
    db = tmp_path / "aposd.db"
    run_build(chapter=None, model="stub", db=db, resume=False,
              extractor=stub, build_dir=tmp_path / "build")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    chapters = {r["chapter"] for r in con.execute("SELECT DISTINCT chapter FROM units")}
    con.close()
    assert "summary-redflags" in chapters
    assert "summary-principles" in chapters
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra build pytest tests/test_build.py::test_run_build_indexes_summary_appendices -v`
Expected: FAIL (those chapter ids absent — `appendices` still empty).

- [ ] **Step 3: Add the appendices to the APOSD profile**

In `corpora/aposd/profile.py`, add an `appendices` argument to the `Profile(...)` call (after `chapter_re=...`):

```python
    chapter_re=r"^Chapter\s+(\d+)",
    appendices={"summary-principles": (185, 185), "summary-redflags": (186, 187)},
)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --extra build pytest tests/test_build.py::test_run_build_indexes_summary_appendices -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run --extra build pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(aposd): index summary-of-principles and summary-of-red-flags appendices"
```

---

## Task 4: Strengthen the eval set

Replace the 3 general-purpose smoke cases with cross-principle, symptom-phrased cases spanning all 6 principles, so model choice and BM25 weights get real signal once the full book is built.

**Files:**
- Modify: `corpora/aposd/cases.yaml`
- Test: `tests/test_eval.py`

- [ ] **Step 1: Write the failing test (structural breadth, not hit-rate)**

Add to `tests/test_eval.py`:

```python
def test_cases_span_principles_and_are_wellformed():
    import yaml
    cases = yaml.safe_load(open("corpora/aposd/cases.yaml"))["cases"]
    assert len(cases) >= 10
    principles = {"complexity", "deep-modules", "information-hiding",
                  "general-purpose", "comments", "strategic-programming"}
    seen = set()
    for c in cases:
        assert "query" in c
        assert ("expect_principle" in c) or ("expect_section" in c)
        if c.get("expect_principle"):
            assert c["expect_principle"] in principles
            seen.add(c["expect_principle"])
    assert len(seen) >= 5   # cases cover at least 5 of the 6 principles
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra build pytest tests/test_eval.py::test_cases_span_principles_and_are_wellformed -v`
Expected: FAIL (current `cases.yaml` has 3 cases, one principle).

- [ ] **Step 3: Rewrite `corpora/aposd/cases.yaml`**

```yaml
# Eval cases for the full APOSD corpus. Symptom-phrased (how a developer describes
# their own code), seeded from the skill's Quick-Diagnostic + Common-Mistakes and
# spanning all six coarse principles. expect_principle is robust to section drift;
# a few expect_section cases pin known passages. Refine after the full build.
cases:
  # deep-modules
  - query: "this small class just forwards calls to another class and adds almost nothing"
    expect_principle: deep-modules
  - query: "the interface has way more methods than the functionality seems to justify"
    expect_principle: deep-modules
  - query: "should I split this big class into several smaller classes"
    expect_principle: deep-modules
  # information-hiding
  - query: "callers have to call setup in the right order before this works"
    expect_principle: information-hiding
  - query: "the same design decision is duplicated across two different modules"
    expect_principle: information-hiding
  - query: "this method just passes its arguments straight through to the layer below"
    expect_principle: information-hiding
  # general-purpose
  - query: "should I make this API general purpose or special purpose"
    expect_principle: general-purpose
  - query: "I added a boolean flag to handle one special case"
    expect_principle: general-purpose
  - query: "this method is named after the one caller that uses it"
    expect_principle: general-purpose
  - query: "general-purpose text editor changePosition method"
    expect_section: "6.3"
  # complexity
  - query: "a small change forces edits in many unrelated places"
    expect_principle: complexity
  - query: "you need a lot of hidden context to change this safely"
    expect_principle: complexity
  # comments
  - query: "the comment just restates what the code already says"
    expect_principle: comments
  - query: "should the comment describe what isn't obvious from the code"
    expect_principle: comments
  # strategic-programming
  - query: "we keep skipping design to ship features faster"
    expect_principle: strategic-programming
  - query: "is getting the code working enough or should I invest in design"
    expect_principle: strategic-programming
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --extra build pytest tests/test_eval.py -v`
Expected: PASS (new structural test + the existing `test_score_cases_hit_rate`).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "test(eval): cross-principle symptom-phrased eval cases for the full book"
```

---

## Task 5: Build the full book with devstral (execution + validation)

Not TDD — this produces the first real full-book `aposd.db` and validates extraction quality across all chapters. Checkpoints per chapter; a quota cap resumes with `--resume`.

- [ ] **Step 1: Confirm the model is available**

Run: `ollama list | grep devstral-small-2:24b-cloud`
Expected: present. (Cloud auth via `ollama signin` if needed.)

- [ ] **Step 2: Run the full-book build**

Run: `uv run --extra build gloss build --model devstral-small-2:24b-cloud --db build/aposd.db`
Expected: per-chapter progress lines (ch1…ch21 + summary-principles/summary-redflags), a final `built N units (0 enrichment failures) -> build/aposd.db` with N in the low hundreds. If interrupted by a quota cap, rerun with `--resume` appended.

- [ ] **Step 3: Sanity-check the db**

Run:
```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('build/aposd.db'); c.row_factory = sqlite3.Row
print('units:', c.execute('SELECT COUNT(*) FROM units').fetchone()[0])
print('by principle:', [(r['principle'], r['n']) for r in c.execute('SELECT principle, COUNT(*) n FROM units GROUP BY principle')])
print('needs_enrich:', c.execute('SELECT COUNT(*) FROM units WHERE needs_enrich=1').fetchone()[0])
print('chapters:', sorted({r['chapter'] for r in c.execute('SELECT DISTINCT chapter FROM units')}, key=lambda s:(len(s),s)))
"
```
Expected: units across all 21 chapters + the two appendices; `needs_enrich` 0 (or a small flagged count); principle distribution roughly matching the taxonomy (null for chapters 10/11/14/17–20 and the appendices).

- [ ] **Step 4: Run eval + spot-check retrieval across principles**

Run:
```bash
uv run --extra build gloss eval --db build/aposd.db
uv run gloss retrieve "this small class just forwards calls and adds nothing" --db build/aposd.db -k 3
uv run gloss retrieve "the comment just restates what the code says" --db build/aposd.db -k 3 --principle comments
uv run gloss retrieve "define errors out of existence" --db build/aposd.db -k 3
```
Expected: a hit-rate printed (record it); retrieve returns on-topic passages with citations from the right chapters. Note any principle that retrieves poorly — that informs BM25 weight tuning (handoff gap #3) and is a follow-up, not a blocker here.

- [ ] **Step 5: Commit (decide artifact tracking per spec §15)**

The `build/` dir is gitignored. Commit the validated checkpoints and record the eval result in the handoff (Task updated separately). If shipping the db is decided later (distribution task), it moves to package data then.

```bash
git add -A && git commit -m "build: first full-book aposd corpus (devstral) + eval baseline" --allow-empty
```

---

## Task 6: Bounded concurrency in enrichment (`--workers`)

Speed up rebuilds (the minimax pass + future corpora) by issuing independent unit extractions concurrently. Checkpoint-safe (lock-guarded writes), method pinned by a serial warmup before the pool, transient errors retried with backoff. `max_workers=1` keeps the exact serial behavior (backward compatible).

**Files:**
- Modify: `src/gloss/enrich.py` (refactor to `_enrich_one`; add `max_workers`)
- Modify: `src/gloss/build.py` (`run_build` gains `workers`, threads it to `enrich_units`)
- Modify: `src/gloss/cli.py` (`build` gains `--workers`)
- Test: `tests/test_enrich.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_enrich.py`:

```python
def test_enrich_units_concurrent_writes_all_without_dupes(tmp_path):
    import json
    from gloss.segment import RawUnit
    from gloss.extract import StubExtractor
    from gloss.enrich import enrich_units
    units = [RawUnit(f"passage number {i}", "6", "6.1", 50) for i in range(20)]
    sect = {"6.1": "section text"}
    stub = StubExtractor({"principle": "general-purpose", "type": "rationale",
                          "context_line": "c", "applies_when": "a",
                          "key_terms": ["k"], "questions": ["q?"]})
    ckpt = tmp_path / "units.jsonl"
    rows = enrich_units(units, sect, stub, card="C",
                        template="{card}{section}{passage}", system="s",
                        checkpoint=ckpt, max_workers=4)
    assert len(rows) == 20
    keys = [json.loads(l)["key"] for l in ckpt.read_text().splitlines() if l.strip()]
    assert len(keys) == 20 and len(set(keys)) == 20    # all present, no duplicates
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run --extra build pytest tests/test_enrich.py::test_enrich_units_concurrent_writes_all_without_dupes -v`
Expected: FAIL (`enrich_units()` got an unexpected keyword argument `max_workers`).

- [ ] **Step 3: Refactor `enrich.py`**

Add `import time` and `import threading` and `from concurrent.futures import ThreadPoolExecutor` to the top of `src/gloss/enrich.py`. Extract the per-unit work into a helper and rewrite `enrich_units`:

```python
def _enrich_one(unit: RawUnit, section_texts, extractor: StructuredExtractor, *,
                card: str, template: str, system: str, model: str, retries: int = 2) -> dict:
    """Enrich one unit into a checkpoint row, retrying transient extractor errors.

    On persistent failure the unit keeps its verbatim text with empty generated fields and
    needs_enrich=1. Code units are forced to type='code'.
    """
    prompt = build_prompt(unit, section_texts.get(unit.section, ""), card, template)
    fields, needs = None, 0
    for attempt in range(retries + 1):
        try:
            fields = extractor.extract(prompt, Enrichment, system=system).model_dump()
            needs = 0
            break
        except Exception:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            fields = {"principle": "", "type": "code" if unit.is_code else "rationale",
                      "context_line": "", "applies_when": "", "key_terms": [], "questions": []}
            needs = 1
    if unit.is_code:
        fields["type"] = "code"
    return {"key": _key(unit), "text": unit.text, "chapter": unit.chapter,
            "section": unit.section, "page": unit.page, "enrich_model": model,
            "needs_enrich": needs, **fields}


def enrich_units(units, section_texts, extractor: StructuredExtractor, *,
                 card: str, template: str, system: str, checkpoint: Path,
                 max_workers: int = 1) -> list[dict]:
    """Enrich each not-yet-checkpointed unit and return all rows.

    Serial when ``max_workers <= 1``. Otherwise the first pending unit is enriched serially
    (pinning the extractor's method and warming its client) before the rest run on a thread
    pool; checkpoint writes are serialized by a lock. Rows are appended as they complete, so
    an interrupted run resumes by key.
    """
    checkpoint = Path(checkpoint)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    model = getattr(extractor, "model", "stub")
    done = _done_keys(checkpoint)
    pending = [u for u in units if _key(u) not in done]
    lock = threading.Lock()

    def work(unit: RawUnit) -> dict:
        return _enrich_one(unit, section_texts, extractor, card=card, template=template,
                           system=system, model=model)

    with checkpoint.open("a") as handle:
        def write(row: dict) -> None:
            with lock:
                handle.write(json.dumps(row) + "\n")
                handle.flush()

        if max_workers > 1 and pending:
            write(work(pending[0]))                       # warmup: pin method serially
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for row in pool.map(work, pending[1:]):
                    write(row)
        else:
            for unit in pending:
                write(work(unit))

    return [json.loads(line) for line in checkpoint.read_text().splitlines() if line.strip()]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run --extra build pytest tests/test_enrich.py -v`
Expected: PASS (new concurrency test + the existing serial enrich tests — the serial path is unchanged behavior).

- [ ] **Step 5: Thread `workers` through `run_build`**

In `src/gloss/build.py`, add `workers: int = 1` to the `run_build` signature (after `build_dir`), and pass it to the enrich call:

```python
        rows = enrich_units(units, section_texts, extractor, card=card, template=template,
                            system=system, checkpoint=checkpoint, max_workers=workers)
```

- [ ] **Step 6: Add `--workers` to the CLI**

In `src/gloss/cli.py`, in the `build` subparser block add:

```python
    b.add_argument("--workers", type=int, default=1, help="concurrent enrichment requests")
```

and update `cmd_build`:

```python
def cmd_build(args) -> None:
    """Build the corpus db (lazy import: build-only deps stay off the retrieve path)."""
    from .build import run_build
    run_build(chapter=args.chapter, model=args.model, db=Path(args.db),
              resume=args.resume, workers=args.workers)
```

- [ ] **Step 7: Run the full suite**

Run: `uv run --extra build pytest -q`
Expected: PASS (all tests, including the stdlib-contract test — `enrich.py` is build-only, so its new `threading`/`concurrent.futures` imports don't touch the retrieve path).

- [ ] **Step 8: Smoke-test concurrency against the real model on one chapter**

Run: `uv run --extra build gloss build --chapter 6 --model devstral-small-2:24b-cloud --db build/ch6.db --workers 8`
Expected: completes faster than serial, `built ~21 units (0 enrichment failures)`. Spot-check a retrieve to confirm quality is unchanged.

- [ ] **Step 9: Commit**

```bash
git add -A && git commit -m "feat(enrich): bounded concurrent enrichment (--workers), checkpoint-safe"
```

---

## Task 7: Minimax quality pass (optional — execution, after Task 5/6)

Compare the quality candidate against devstral on the strengthened eval, then lock the build model. Deferred unless Spencer wants the quality bet now; devstral is the working default.

- [ ] **Step 1: 1-call sanity check that minimax produces structured output**

Run:
```bash
uv run --extra build python -c "
from gloss.extract import OllamaExtractor
from gloss.enrich import Enrichment
out = OllamaExtractor('minimax-m3:cloud').extract('Classify: a deep module hides complexity behind a simple interface.', Enrichment, system='Return retrieval metadata.')
print(out.model_dump())
"
```
Expected: a populated `Enrichment` (proves `function_calling` works before a full run). If it fails, stop — do not burn quota on a full build.

- [ ] **Step 2: Full-book build with minimax (concurrent)**

Run: `uv run --extra build gloss build --model minimax-m3:cloud --db build/aposd-minimax.db --workers 8`
Expected: `built N units` with few/no failures. Resume across quota caps with `--resume`.

- [ ] **Step 3: Compare eval hit-rates**

Run:
```bash
uv run --extra build gloss eval --db build/aposd.db          # devstral
uv run --extra build gloss eval --db build/aposd-minimax.db  # minimax
```
Expected: two hit-rates. If minimax is not clearly better, keep devstral (cheaper). Lock the choice and record it in the handoff. Spot-check a few queries side by side, not just the aggregate.

- [ ] **Step 4: Commit the decision**

```bash
git add -A && git commit -m "docs: lock build model after devstral-vs-minimax eval comparison" --allow-empty
```

---

## Self-review notes

- **Spec coverage:** full-book build (§3, §6, Task 12 of prior plan) → T2/T3/T5; dynamic structure vs hardcoded pages (engine/instance split §10, "loaded inputs not hardcoded") → T1; appendices/red-flag bank (§5 fine facet "nothing in the book is lost") → T3; strengthened eval seeded from the skill (§8) → T4; one-model-per-build + checkpoint/resume, no silent fallback (§6/§9) → T2/T5 (per-chapter checkpoints, `--resume`); concurrency is an enrichment-internal optimization that preserves the seam (§6 extractor interface unchanged) → T6; eval-driven model lock (§6/§15) → T7; stdlib-only query path (§7) preserved — all new imports are build-only, guarded by `test_stdlib_contract.py` (T6 Step 7).
- **Type consistency:** `Profile.chapter_re`/`appendices` (T1) consumed by `split_chapters` (T1) and `run_build` (T2/T3); `split_chapters` returns `list[tuple[str, list[Element]]]` consumed by `run_build`; chapter ids are **strings** throughout (`"6"`, `"summary-redflags"`) matching the `--chapter` CLI arg and `principle_for_chapter`'s str-coercion; `enrich_units(..., max_workers=1)` default keeps every existing caller's behavior; `_enrich_one` row shape == `units` table columns (T6 == store.py).
- **Known follow-ups (not blockers, tracked in handoff):** BM25 `_WEIGHTS` tuning on the full eval (gap #3); `needs_enrich` targeted re-runs + FTS UPDATE/DELETE triggers (gap #4) — concurrency retries transient errors but a true quota-cap-stop (vs per-unit skip) is still manual `--resume`; `num_ctx` real `prompt_eval_count` logging (gap #1). Distribution (prior plan Task 13) remains deferred and is unblocked once `build/aposd.db` is validated (T5).
```
