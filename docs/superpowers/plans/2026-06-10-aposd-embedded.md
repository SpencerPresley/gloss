# aposd-embedded Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a skill-complement retriever — turn *A Philosophy of Software Design* into a structured, principle-anchored SQLite/FTS5 corpus and expose a stdlib-only CLI that returns relevant primary-source passages for a design situation.

**Architecture:** Build-time (offline, once): PyMuPDF font-aware parse → deterministic unit segmentation → LLM enrichment (structured output, behind a `StructuredExtractor` seam) → single `aposd.db`. Query-time (repeated, zero deps): FTS5 BM25 + metadata filter over that file via `aposd retrieve`.

**Tech Stack:** Python 3.12, uv, PyMuPDF, langchain + langchain-ollama (build-only), Pydantic, SQLite FTS5 (stdlib), pytest.

**Spec:** `docs/superpowers/specs/2026-06-10-aposd-embedded-design.md`

**Session scope:** Tasks 0–10 (the Chapter-6 slice, end to end) are for the current session. Tasks 11–13 (model A/B, full-book build, distribution) are deferred and intentionally sketched — they depend on Ch.6 outcomes and will be detailed in a later session.

---

## Conventions

- Run everything through uv: `uv run pytest …`, `uv run aposd …`.
- Corpus PDF: `resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf`.
- Chapter 6 = PDF pages 50–58 (book pages 42–50). Known code on PDF p52–53 (`changePosition`, `text.delete`).
- Fonts: code = substring `Typewriter`; heading = `NimbusSanL-Bol`; chapter title size ≈ 20.2, section ≈ 16.6, body ≈ 14.4, code ≈ 10.8. Match font names by **substring** (subset prefixes like `AAAAAE+`).
- Commit after every task with the message shown in its final step.

---

## Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`, `.python-version`, `src/aposd/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Pin Python and init layout**

```bash
cd /Users/spencerpresley/code/aposd-embedded
echo "3.12" > .python-version
mkdir -p src/aposd tests eval build
touch src/aposd/__init__.py tests/__init__.py
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "aposd"
version = "0.1.0"
description = "Skill-complement retriever for A Philosophy of Software Design"
requires-python = ">=3.12"
dependencies = []  # query-time is stdlib-only; nothing required to RUN `aposd retrieve`

[project.optional-dependencies]
build = ["pymupdf>=1.24", "langchain>=1.3", "langchain-ollama>=1.1", "pydantic>=2.10", "pyyaml>=6"]

[project.scripts]
aposd = "aposd.cli:main"

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aposd"]
```

- [ ] **Step 3: Add a conftest exposing the corpus path**

```python
# tests/conftest.py
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[1]
PDF = REPO / "resources" / "2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf"

@pytest.fixture
def pdf_path() -> Path:
    if not PDF.exists():
        pytest.skip(f"corpus PDF not present at {PDF}")
    return PDF
```

- [ ] **Step 4: Verify the toolchain resolves**

Run: `uv sync --extra build --group dev`
Expected: resolves and installs pymupdf, langchain-ollama, pydantic, pyyaml, pytest with Python 3.12.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: scaffold uv project (stdlib-only runtime, build extra)"
```

---

## Task 1: Taxonomy reconciliation (analysis + review gate)

> This is the spec's first task. It is **analysis**, not TDD code: it produces the controlled vocabulary the rest of the build consumes, plus a gap report for Spencer to review before any enrichment is built on top.

**Files:**
- Create: `src/aposd/data/taxonomy.yaml`, `docs/superpowers/notes/2026-06-10-taxonomy-reconciliation.md`

- [ ] **Step 1: Extract the book's chapter/section structure**

Run a throwaway probe to list headings (uses parse logic informally; fine to use `pdftotext` TOC pages or a quick PyMuPDF scan):

```bash
uv run --with pymupdf python - <<'PY'
import fitz
doc = fitz.open("resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf")
for i in range(len(doc)):
    for b in doc[i].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if "NimbusSanL-Bol" in s["font"] and s["size"] >= 16:
                    print(i+1, round(s["size"],1), s["text"])
PY
```

- [ ] **Step 2: Dispatch a subagent to reconcile skill ↔ book**

Dispatch a subagent (general-purpose) with this task: read `.claude/skills/software-design-philosophy/SKILL.md` + its `references/*.md`; read the heading list from Step 1; produce (a) `src/aposd/data/taxonomy.yaml` and (b) the reconciliation report. The YAML shape:

```yaml
principles:                        # COARSE facet — the skill's 6, used for --principle filters
  - slug: general-purpose
    name: "General-Purpose vs Special-Purpose Modules"
    vocabulary: ["somewhat general-purpose", "push complexity down", "special case", "configuration parameter"]
    diagnostics: ["What is the simplest interface that covers all current needs?"]
    red_flags: ["use-case-specific methods", "boolean flag for one special case"]
  # … all 6 …
topics:                            # FINE facet — the book's chapters, each mapped to a coarse principle
  - {chapter: 6, title: "General-Purpose Modules are Deeper", principle: general-purpose}
  # … all chapters; chapters with no clean coarse home get principle: null + flagged in the report …
```

The report (`docs/superpowers/notes/2026-06-10-taxonomy-reconciliation.md`) must contain: the proposed two-facet vocabulary, and an explicit **gap list** — book chapters/topics the skill's 6 principles do NOT cover (e.g. *Define Errors Out of Existence*, *Choosing Names*, *Consistency*, *Designing for Performance*), each marked fold-into-`<principle>` or add-as-standalone-`topic`.

- [ ] **Step 3: STOP for review**

Present the gap list + proposed `taxonomy.yaml` to Spencer. Do not proceed to enrichment design until he signs off on the two-facet vocabulary. (Ch.6's coarse principle will be `general-purpose`; confirm that mapping at minimum.)

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: taxonomy reconciliation — two-facet vocabulary + gap report"
```

---

## Task 2: `parse.py` — font-aware PDF extraction

**Files:**
- Create: `src/aposd/parse.py`, `tests/test_parse.py`

- [ ] **Step 1: Write failing tests (real Ch.6 pages)**

```python
# tests/test_parse.py
from aposd.parse import classify_font, parse_pdf, Element

def test_classify_font():
    assert classify_font("AAAAAE+LucidaSans-Typewriter") == "code"
    assert classify_font("BBBB+NimbusSanL-Bol") == "head"
    assert classify_font("CCCC+NimbusRomNo9L-Regu") == "body"

def test_parse_ch6_has_heading_and_code(pdf_path):
    els = parse_pdf(pdf_path, first_page=50, last_page=53)
    kinds = {e.kind for e in els}
    assert "heading" in kinds and "code" in kinds and "para" in kinds
    # the General-Purpose chapter title appears as a level-1 heading
    assert any(e.kind == "heading" and e.level == 1 and "General-Purpose" in e.text for e in els)
    # the changePosition signature is captured as code, on one contiguous line
    code = "\n".join(e.text for e in els if e.kind == "code")
    assert "changePosition(Position position, int numChars)" in code
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_parse.py -v`
Expected: FAIL (ImportError: cannot import name 'parse_pdf').

- [ ] **Step 3: Implement `parse.py`**

```python
# src/aposd/parse.py
"""Font-aware extraction of the APOSD PDF into ordered structural Elements.

The PDF has a clean text layer with discriminative fonts: code is set in a
Lucida typewriter face, headings in NimbusSanL-Bol. We exploit those signals
rather than guessing from layout. No header/footer stripping needed (calibre
already removed them).
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Iterator
import fitz  # PyMuPDF

Kind = Literal["heading", "para", "code", "figure"]
CODE_FONT, HEAD_FONT = "Typewriter", "NimbusSanL-Bol"
FIGURE_MIN_AREA = 5000  # px²; drops 28x28 decorative margin icons

@dataclass
class Element:
    kind: Kind
    text: str
    page: int
    level: int = 0  # heading level: 1 = chapter title (size>=20), 2 = section (size>=16)

def classify_font(font: str) -> str:
    # subset prefixes like 'AAAAAE+...' => match by substring, never equality
    if CODE_FONT in font:
        return "code"
    if HEAD_FONT in font:
        return "head"
    return "body"

def _line_text(spans: list[dict]) -> str:
    return "".join(s["text"] for s in spans)

def parse_pdf(path: Path, first_page: int | None = None, last_page: int | None = None) -> list[Element]:
    """Return ordered Elements for the (1-based, inclusive) page range, or whole doc."""
    doc = fitz.open(path)
    lo = (first_page or 1) - 1
    hi = (last_page or len(doc))
    out: list[Element] = []
    code_buf: list[str] = []
    code_page = 0

    def flush_code():
        nonlocal code_buf, code_page
        if code_buf:
            out.append(Element("code", "\n".join(code_buf), code_page))
            code_buf = []

    for pno in range(lo, hi):
        page = doc[pno]
        d = page.get_text("dict")
        for block in d["blocks"]:
            if block.get("type") == 1:  # image block
                w = block["bbox"][2] - block["bbox"][0]
                h = block["bbox"][3] - block["bbox"][1]
                if w * h >= FIGURE_MIN_AREA:
                    flush_code()
                    out.append(Element("figure", f"[FIGURE {int(w)}x{int(h)}]", pno + 1))
                continue
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                classes = [classify_font(s["font"]) for s in spans]
                text = _line_text(line["spans"]).rstrip()
                size = max(s["size"] for s in spans)
                head_frac = sum(c == "head" for c in classes) / len(classes)
                if head_frac >= 0.6:
                    flush_code()
                    level = 1 if size >= 20 else 2
                    out.append(Element("heading", text.strip(), pno + 1, level))
                elif all(c == "code" for c in classes):
                    if not code_buf:
                        code_page = pno + 1
                    code_buf.append(text)
                else:
                    flush_code()
                    out.append(Element("para", text.strip(), pno + 1))
    flush_code()
    doc.close()
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_parse.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(parse): font-aware PDF extraction into structural Elements"
```

---

## Task 3: `segment.py` — deterministic units

**Files:**
- Create: `src/aposd/segment.py`, `tests/test_segment.py`

> Rule: a heading starts a new section; within a section, a contiguous run of prose is one `RawUnit`, and each code block is its own `RawUnit` (`is_code=True`). `chapter`/`section` are tracked from headings. We also return `section_texts` (full text per section) for situating context at enrichment time.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_segment.py
from aposd.parse import Element
from aposd.segment import segment, RawUnit

def test_segment_splits_prose_and_code():
    els = [
        Element("heading", "6 General-Purpose Modules are Deeper", 50, 1),
        Element("para", "One of the most common decisions...", 50),
        Element("heading", "6.3 A more general-purpose API", 52, 2),
        Element("para", "A better approach is to make the text class...", 52),
        Element("code", "Position changePosition(Position position, int numChars);", 52),
        Element("para", "This method returns a new position...", 52),
    ]
    units, section_texts = segment(els, chapter="6")
    assert [u.is_code for u in units] == [False, False, True, False]
    code = [u for u in units if u.is_code][0]
    assert code.section == "6.3"
    assert "changePosition" in code.text
    # situating context for 6.3 includes its prose + code
    assert "better approach" in section_texts["6.3"]
    assert "changePosition" in section_texts["6.3"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_segment.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `segment.py`**

```python
# src/aposd/segment.py
"""Deterministic unit boundaries. Verbatim text is fixed here; the LLM never
changes it. A unit = a contiguous prose run within a section, or a single code
block. section_texts gives the full per-section text used as situating context.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re

@dataclass
class RawUnit:
    text: str
    chapter: str
    section: str
    page: int
    is_code: bool = False

_SECTION_RE = re.compile(r"^(\d+\.\d+)")

def _section_of(heading_text: str, chapter: str) -> str:
    m = _SECTION_RE.match(heading_text.strip())
    return m.group(1) if m else chapter  # chapter intro prose -> section == chapter

def segment(elements, chapter: str):
    units: list[RawUnit] = []
    section_texts: dict[str, list[str]] = {}
    cur_section = chapter
    prose: list[str] = []
    prose_page = 0

    def flush_prose():
        nonlocal prose, prose_page
        if prose:
            units.append(RawUnit("\n".join(prose), chapter, cur_section, prose_page, False))
            prose = []

    for el in elements:
        if el.kind == "figure":
            continue
        if el.kind == "heading":
            flush_prose()
            if el.level == 2:
                cur_section = _section_of(el.text, chapter)
            section_texts.setdefault(cur_section, [])
            continue
        section_texts.setdefault(cur_section, []).append(el.text)
        if el.kind == "code":
            flush_prose()
            units.append(RawUnit(el.text, chapter, cur_section, el.page, True))
        else:  # para
            if not prose:
                prose_page = el.page
            prose.append(el.text)
    flush_prose()
    return units, {k: "\n".join(v) for k, v in section_texts.items()}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_segment.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(segment): deterministic prose/code units + section texts"
```

---

## Task 4: `extract.py` — the StructuredExtractor seam

**Files:**
- Create: `src/aposd/extract.py`, `tests/test_extract.py`

- [ ] **Step 1: Write failing tests (stub + method-branch logic only — no model calls)**

```python
# tests/test_extract.py
from pydantic import BaseModel
from aposd.extract import StubExtractor, default_method

class _S(BaseModel):
    a: int

def test_default_method_branches_by_model():
    assert default_method("minimax-m3:cloud") == "function_calling"
    assert default_method("gpt-oss:120b-cloud") == "function_calling"
    assert default_method("gpt-oss:20b") == "json_schema"
    assert default_method("llama3.1:8b") == "json_schema"

def test_stub_extractor_returns_schema_instance():
    ex = StubExtractor({"a": 7})
    out = ex.extract("anything", _S)
    assert isinstance(out, _S) and out.a == 7
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `extract.py`**

```python
# src/aposd/extract.py
"""The single seam between the build pipeline and any LLM provider.

The pipeline depends ONLY on the StructuredExtractor protocol. OllamaExtractor
is the sole adapter today; it hides model loading and the json_schema-vs-
function_calling branch. A future provider is a new adapter, not a refactor.
"""
from __future__ import annotations
from typing import Protocol, runtime_checkable
from pydantic import BaseModel

@runtime_checkable
class StructuredExtractor(Protocol):
    def extract(self, prompt: str, schema: type[BaseModel]) -> BaseModel: ...

def default_method(model: str) -> str:
    # Cloud models (esp. minimax) ignore Ollama's format=/json_schema and need
    # the tool-calling path; local models honor json_schema. Override-able.
    return "function_calling" if model.endswith(":cloud") else "json_schema"

class StubExtractor:
    """Deterministic test double — returns the same payload for every call."""
    def __init__(self, payload: dict):
        self._payload = payload
    def extract(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        return schema(**self._payload)

class OllamaExtractor:
    def __init__(self, model: str, method: str | None = None, num_ctx: int = 8192, max_retries: int = 2):
        from langchain_ollama import ChatOllama  # imported lazily (build-only dep)
        self.model = model
        self.method = method or default_method(model)
        self._llm = ChatOllama(model=model, temperature=0, num_ctx=num_ctx)
        self._max_retries = max_retries

    def extract(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        structured = self._llm.with_structured_output(schema, method=self.method, include_raw=True)
        last = None
        for _ in range(self._max_retries + 1):
            res = structured.invoke(prompt)
            if res.get("parsed") is not None and res.get("parsing_error") is None:
                return res["parsed"]
            last = res.get("parsing_error")
        raise ValueError(f"structured extraction failed for {self.model}: {last}")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS (2 tests; no Ollama needed — only stub + pure function tested).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(extract): StructuredExtractor seam + Ollama/Stub adapters"
```

---

## Task 5: `enrich.py` — checkpointed enrichment

**Files:**
- Create: `src/aposd/enrich.py`, `tests/test_enrich.py`

- [ ] **Step 1: Write failing tests (StubExtractor → no model)**

```python
# tests/test_enrich.py
import json
from aposd.segment import RawUnit
from aposd.extract import StubExtractor
from aposd.enrich import Enrichment, enrich_units, build_prompt

STUB = {"principle": "general-purpose", "type": "rationale",
        "context_line": "Ch.6 on general-purpose APIs.",
        "applies_when": "Designing a class's public methods.",
        "key_terms": ["general-purpose", "special-purpose"],
        "questions": ["Is this API too specialized?"]}

def test_enrich_writes_and_resumes(tmp_path):
    units = [RawUnit("text A", "6", "6.1", 50), RawUnit("Position changePosition(...);", "6", "6.3", 52, is_code=True)]
    sect = {"6.1": "section 6.1 text", "6.3": "section 6.3 text"}
    ckpt = tmp_path / "units.jsonl"
    ex = StubExtractor(STUB)
    out1 = enrich_units(units, sect, ex, card="CARD", checkpoint=ckpt)
    assert len(out1) == 2
    assert out1[1]["type"] == "code"          # code units forced to type=code
    assert out1[0]["enrich_model"] == "stub"
    # resume: same checkpoint, no re-extraction (StubExtractor would still work, but count stays 2)
    assert ckpt.read_text().count("\n") == 2
    out2 = enrich_units(units, sect, ex, card="CARD", checkpoint=ckpt)
    assert len(out2) == 2 and ckpt.read_text().count("\n") == 2  # no duplicates

def test_prompt_includes_section_and_card():
    p = build_prompt(RawUnit("body", "6", "6.3", 52), "FULL SECTION 6.3", "THE CARD")
    assert "FULL SECTION 6.3" in p and "THE CARD" in p and "body" in p
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `enrich.py`**

```python
# src/aposd/enrich.py
"""Per-unit enrichment. Substantive content comes ONLY from the passage; the
skill card supplies which-principle and preferred phrasing. Checkpointed to
JSONL so a quota cap / blip resumes from the last completed unit.
"""
from __future__ import annotations
import json, hashlib
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from .segment import RawUnit
from .extract import StructuredExtractor

UnitType = Literal["definition", "rationale", "example", "code", "red_flag"]

class Enrichment(BaseModel):
    principle: str = Field(description="principle slug from the provided taxonomy card")
    type: UnitType
    context_line: str = Field(description="1 sentence situating this passage within its chapter")
    applies_when: str = Field(description="the code/design symptom where this applies")
    key_terms: list[str] = Field(description="canonical term(s) + everyday synonyms")
    questions: list[str] = Field(description="3-6 symptom-phrased questions this passage answers")

PROMPT = """You enrich one passage from a software-design book for retrieval.

TAXONOMY CARD (controlled vocabulary — use ONLY these principle slugs, and prefer this phrasing):
{card}

FULL SECTION (situating context — do not summarize it, just use it to situate the passage):
{section}

THE PASSAGE TO ENRICH:
{passage}

Generate the fields. Use ONLY information present in the passage for substantive content; if a
field is unknown, give your best short value from the passage. Phrase questions/applies_when in
code-review symptom language, not book language."""

def _key(u: RawUnit) -> str:
    return hashlib.sha1(f"{u.section}|{u.is_code}|{u.text[:64]}".encode()).hexdigest()[:12]

def build_prompt(unit: RawUnit, section_text: str, card: str) -> str:
    return PROMPT.format(card=card, section=section_text, passage=unit.text)

def _done_keys(checkpoint: Path) -> set[str]:
    if not checkpoint.exists():
        return set()
    return {json.loads(l)["key"] for l in checkpoint.read_text().splitlines() if l.strip()}

def enrich_units(units, section_texts, extractor: StructuredExtractor, card: str, checkpoint: Path):
    checkpoint = Path(checkpoint)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    model = getattr(extractor, "model", "stub")
    done = _done_keys(checkpoint)
    with checkpoint.open("a") as fh:
        for u in units:
            k = _key(u)
            if k in done:
                continue
            prompt = build_prompt(u, section_texts.get(u.section, ""), card)
            try:
                e = extractor.extract(prompt, Enrichment).model_dump()
                needs = 0
            except Exception:
                e = {"principle": "", "type": "code" if u.is_code else "rationale",
                     "context_line": "", "applies_when": "", "key_terms": [], "questions": []}
                needs = 1
            if u.is_code:
                e["type"] = "code"   # deterministic override
            row = {"key": k, "text": u.text, "chapter": u.chapter, "section": u.section,
                   "page": u.page, "enrich_model": model, "needs_enrich": needs, **e}
            fh.write(json.dumps(row) + "\n")
            done.add(k)
    return [json.loads(l) for l in checkpoint.read_text().splitlines() if l.strip()]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(enrich): checkpointed per-unit enrichment via StructuredExtractor"
```

---

## Task 6: `store.py` — SQLite/FTS5 build + search (stdlib only)

**Files:**
- Create: `src/aposd/store.py`, `tests/test_store.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_store.py
from aposd.store import build_db, search, to_match_query

ROWS = [
    {"text": "A module with a complex interface for little functionality is shallow.",
     "principle": "deep-modules", "chapter": "4", "section": "4.5", "type": "red_flag",
     "page": 45, "context_line": "Ch.4 on module depth.", "applies_when": "thin wrapper smell",
     "key_terms": ["shallow module", "thin wrapper"], "questions": ["is this module too shallow?"],
     "enrich_model": "stub", "needs_enrich": 0},
    {"text": "A general-purpose changePosition method covers many UI operations.",
     "principle": "general-purpose", "chapter": "6", "section": "6.3", "type": "example",
     "page": 52, "context_line": "Ch.6 general-purpose API.", "applies_when": "special-purpose method smell",
     "key_terms": ["general-purpose"], "questions": ["is this API too specialized?"],
     "enrich_model": "stub", "needs_enrich": 0},
]

def test_match_query_rewrite_avoids_implicit_and():
    # raw NL must not be passed through; tokens OR-joined with prefix globs
    q = to_match_query("should I add a configuring flag parameter")
    assert " OR " in q and "configuring*" in q and "flag*" in q  # tokens prefix-globbed, OR-joined
    assert "I*" not in q  # 1-2 char tokens dropped — never a raw implicit-AND that returns zero rows

def test_build_and_search_with_filter(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(ROWS, db)
    hits = search(db, "module interface complex little functionality", k=3)
    assert hits[0]["section"] == "4.5"
    filtered = search(db, "changePosition method", k=3, principles=["general-purpose"])
    assert filtered and all(h["principle"] == "general-purpose" for h in filtered)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `store.py`**

```python
# src/aposd/store.py
"""SQLite + FTS5 store. STDLIB ONLY — this is the query-time hot path and must
run anywhere Python runs, with nothing installed. FTS5 is only an index; all
data lives in the plain `units` table.
"""
from __future__ import annotations
import json, re, sqlite3
from pathlib import Path

_DDL = """
CREATE TABLE units (
  id INTEGER PRIMARY KEY,
  principle TEXT, chapter TEXT, section TEXT, type TEXT, page INTEGER,
  text TEXT, context_line TEXT, applies_when TEXT, key_terms TEXT, questions TEXT,
  enrich_model TEXT, needs_enrich INTEGER DEFAULT 0,
  CHECK (type IN ('definition','rationale','example','code','red_flag'))
);
CREATE VIRTUAL TABLE units_fts USING fts5(
  text, context_line, applies_when, key_terms, questions,
  content='units', content_rowid='id',
  tokenize="porter unicode61 tokenchars '_'"
);
CREATE TRIGGER units_ai AFTER INSERT ON units BEGIN
  INSERT INTO units_fts(rowid, text, context_line, applies_when, key_terms, questions)
  VALUES (new.id, new.text, new.context_line, new.applies_when, new.key_terms, new.questions);
END;
"""
# BM25 column weights: text, context_line, applies_when, key_terms, questions. Tune via eval.
_WEIGHTS = (10.0, 4.0, 5.0, 8.0, 4.0)

def to_match_query(text: str) -> str:
    toks = [t for t in re.findall(r"[A-Za-z0-9_]+", text) if len(t) > 2]
    return " OR ".join(f"{t}*" for t in toks)

def build_db(rows: list[dict], db_path: Path) -> None:
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(_DDL)
    for r in rows:
        con.execute(
            "INSERT INTO units(principle,chapter,section,type,page,text,context_line,applies_when,"
            "key_terms,questions,enrich_model,needs_enrich) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["principle"], r["chapter"], r["section"], r["type"], r["page"], r["text"],
             r["context_line"], r["applies_when"], " ".join(r["key_terms"]),
             " ".join(r["questions"]), r["enrich_model"], r.get("needs_enrich", 0)))
    con.execute("INSERT INTO units_fts(units_fts) VALUES('optimize')")
    con.commit(); con.close()

def search(db_path: Path, query: str, k: int = 5,
           principles: list[str] | None = None, types: list[str] | None = None) -> list[dict]:
    match = to_match_query(query)
    if not match:
        return []
    where = ["units_fts MATCH ?"]
    params: list = [match]
    if principles:
        where.append(f"u.principle IN ({','.join('?'*len(principles))})"); params += principles
    if types:
        where.append(f"u.type IN ({','.join('?'*len(types))})"); params += types
    sql = (f"SELECT bm25(units_fts,{','.join(map(str,_WEIGHTS))}) AS score, u.* "
           f"FROM units_fts JOIN units u ON u.id = units_fts.rowid "
           f"WHERE {' AND '.join(where)} ORDER BY score LIMIT ?")
    con = sqlite3.connect(db_path); con.row_factory = sqlite3.Row
    rows = con.execute(sql, params + [k]).fetchall()
    con.close()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS. (If `test_match_query` asserts on `'add'`, confirm 3-char tokens are kept.)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(store): SQLite/FTS5 build + BM25 search with query rewrite"
```

---

## Task 7: `cli.py` — build / retrieve / eval

**Files:**
- Create: `src/aposd/cli.py`, `tests/test_cli.py`

- [ ] **Step 1: Write failing test (retrieve over a fixture db)**

```python
# tests/test_cli.py
import json, subprocess, sys
from aposd.store import build_db
from tests.test_store import ROWS

def test_retrieve_json(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(ROWS, db)
    out = subprocess.run([sys.executable, "-m", "aposd.cli", "retrieve",
                          "module interface complex little functionality",
                          "--db", str(db), "--json"], capture_output=True, text=True)
    assert out.returncode == 0
    hits = json.loads(out.stdout)
    assert hits[0]["section"] == "4.5"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `cli.py`** (retrieve is stdlib-only; build/eval import build-only modules lazily)

```python
# src/aposd/cli.py
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from .store import search

def _fmt(h: dict) -> str:
    cite = f"{h['principle']} §{h['section']} p.{h['page']}"
    return f"[{cite}] ({h['type']})\n{h['text']}\n"

def cmd_retrieve(a):
    hits = search(Path(a.db), a.query, k=a.k, principles=a.principle, types=a.type)
    if a.json:
        print(json.dumps(hits, indent=2))
    else:
        print("\n".join(_fmt(h) for h in hits) or "(no matches)")

def cmd_build(a):
    from .build import run_build  # build-only; lazy import keeps retrieve stdlib-only
    run_build(chapter=a.chapter, model=a.model, db=Path(a.db), resume=a.resume)

def cmd_eval(a):
    from .evalrun import run_eval
    run_eval(Path(a.db), Path(a.cases))

def main(argv=None):
    p = argparse.ArgumentParser(prog="aposd")
    sub = p.add_subparsers(required=True)

    r = sub.add_parser("retrieve"); r.add_argument("query")
    r.add_argument("--db", default="aposd.db"); r.add_argument("-k", type=int, default=5)
    r.add_argument("--principle", action="append"); r.add_argument("--type", action="append")
    r.add_argument("--json", action="store_true"); r.set_defaults(func=cmd_retrieve)

    b = sub.add_parser("build"); b.add_argument("--chapter"); b.add_argument("--model", default="minimax-m3:cloud")
    b.add_argument("--db", default="aposd.db"); b.add_argument("--resume", action="store_true")
    b.set_defaults(func=cmd_build)

    e = sub.add_parser("eval"); e.add_argument("--db", default="aposd.db")
    e.add_argument("--cases", default="eval/cases.yaml"); e.set_defaults(func=cmd_eval)

    a = p.parse_args(argv); a.func(a)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(cli): aposd retrieve/build/eval (retrieve stays stdlib-only)"
```

---

## Task 8: build orchestration + eval harness

**Files:**
- Create: `src/aposd/build.py`, `src/aposd/evalrun.py`, `eval/cases.yaml`, `tests/test_eval.py`

- [ ] **Step 1: Write failing test for eval metric**

```python
# tests/test_eval.py
from aposd.store import build_db
from aposd.evalrun import score_cases
from tests.test_store import ROWS

def test_score_cases_hit_rate(tmp_path):
    db = tmp_path / "aposd.db"; build_db(ROWS, db)
    cases = [{"query": "module interface complex little functionality", "expect_section": "4.5"},
             {"query": "changePosition general purpose", "expect_section": "6.3"}]
    res = score_cases(db, cases, k=3)
    assert res["hit_rate"] == 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_eval.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `build.py` and `evalrun.py`**

```python
# src/aposd/build.py
"""Wire parse -> segment -> enrich -> store for one chapter (or whole book)."""
from __future__ import annotations
from pathlib import Path
from .parse import parse_pdf
from .segment import segment
from .enrich import enrich_units
from .extract import OllamaExtractor
from .taxonomy import load_card, chapter_pages, principle_for_chapter

PDF = Path("resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf")

def run_build(chapter: str | None, model: str, db: Path, resume: bool):
    card = load_card()
    first, last = chapter_pages(chapter) if chapter else (None, None)
    els = parse_pdf(PDF, first, last)
    units, section_texts = segment(els, chapter=chapter or "")
    ckpt = Path("build") / f"ch{chapter or 'all'}" / "units.jsonl"
    if not resume and ckpt.exists():
        ckpt.unlink()
    extractor = OllamaExtractor(model)
    rows = enrich_units(units, section_texts, extractor, card=card, checkpoint=ckpt)
    from .store import build_db
    build_db(rows, db)
    print(f"built {len(rows)} units -> {db}")
```

```python
# src/aposd/evalrun.py
from __future__ import annotations
from pathlib import Path
from .store import search

def score_cases(db: Path, cases: list[dict], k: int = 5) -> dict:
    hits = 0
    for c in cases:
        res = search(db, c["query"], k=k)
        if any(h["section"] == c.get("expect_section") for h in res):
            hits += 1
    return {"hit_rate": hits / len(cases), "n": len(cases)}

def run_eval(db: Path, cases_path: Path) -> dict:
    import yaml
    cases = yaml.safe_load(cases_path.read_text())["cases"]
    res = score_cases(db, cases)
    print(f"hit_rate={res['hit_rate']:.2f} over n={res['n']}")
    return res
```

> `taxonomy.py` (helpers `load_card`, `chapter_pages`, `principle_for_chapter`) is produced in Task 1's wake — add it here if Task 1 only emitted the YAML. `load_card()` reads `data/taxonomy.yaml` and renders the compact card string; `chapter_pages("6")` returns `(50, 58)`.

- [ ] **Step 4: Seed `eval/cases.yaml` from the skill**

```yaml
# eval/cases.yaml  — seeded from the skill's Quick-Diagnostic + Common-Mistakes
cases:
  - query: "this module has a complex interface for little functionality"
    expect_principle: deep-modules
  - query: "should I add a configuration parameter for this special case"
    expect_principle: general-purpose
  - query: "the same design decision is duplicated across two modules"
    expect_principle: information-hiding
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_eval.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(build,eval): chapter build orchestration + hit-rate eval"
```

---

## Task 9: Run the Chapter-6 build (real model, real data)

> Execution + inspection, not TDD. This produces the first real `aposd.db` and validates extraction quality.

- [ ] **Step 1: Confirm Ollama + model available**

Run: `ollama list | grep -E 'minimax-m3:cloud|gpt-oss:20b'`
Expected: both present. (Cloud auth via `ollama signin` if needed.)

- [ ] **Step 2: Build Ch.6 (start with the cheap local model to shake out bugs)**

Run: `uv run aposd build --chapter 6 --model gpt-oss:20b --db build/ch6.db`
Expected: `built N units -> build/ch6.db` (N ≈ 15–40). Checkpoint at `build/ch6/units.jsonl`.

- [ ] **Step 3: Eyeball the units**

Run: `uv run python -c "import json,sys; [print(json.loads(l)['section'], json.loads(l)['type'], json.loads(l)['text'][:80]) for l in open('build/ch6/units.jsonl')]"`
Expected: sensible sections (6, 6.1–6.4), code units typed `code`, prose units with plausible generated fields. Verify verbatim text matches the book (no LLM rewrite).

- [ ] **Step 4: Run eval + a couple manual queries**

Run: `uv run aposd eval --db build/ch6.db` and `uv run aposd retrieve "should I make this API general purpose" --db build/ch6.db`
Expected: eval prints a hit-rate; retrieve returns Ch.6 passages with citations.

- [ ] **Step 5: Commit the checkpoint + db (decide per spec §15 whether to track the .db)**

```bash
git add -A && git commit -m "build: first Chapter-6 corpus (gpt-oss:20b dev pass) + eval"
```

---

## Task 10: Session handoff doc

- [ ] **Step 1: Write `docs/superpowers/notes/2026-06-10-session-handoff.md`** capturing: what's built, how to run the build/eval, the checkpoint location + resume semantics, the open model-A/B decision, the taxonomy decisions from Task 1, anything surprising encountered, and the exact next steps (Tasks 11–13). Commit.

```bash
git add -A && git commit -m "docs: session handoff notes for aposd-embedded"
```

---

## Future session (sketch — detail after Ch.6 is validated)

These depend on Ch.6 outcomes (unit quality, eval scores, weight tuning) and are intentionally NOT detailed yet:

- **Task 11 — Eval-driven model pick:** rebuild Ch.6 with `minimax-m3:cloud` and `gpt-oss:120b-cloud`; compare `aposd eval` hit-rate/MRR; lock the winner. (Cloud quota is plan-level; checkpoint + resume across resets.)
- **Task 12 — Full-book build:** `aposd build --model <winner> --db aposd.db` over all 188 pages; tune BM25 weights + tokenizer on the full eval set; consider the optional `trigram` secondary index.
- **Task 13 — Distribution:** bundle `aposd.db` as package data (`importlib.resources`), build deps behind the `[build]` extra, verify `uvx aposd retrieve` works from a clean environment. (Spencer leads this.)

---

## Self-review notes

- **Spec coverage:** parse (§6.1) → T2; segment (§6.2, deterministic) → T3; extractor seam (§6 interface) → T4; enrich + checkpoint + no-fallback (§6/§9) → T5/T9; SQLite/FTS5 + query rewrite + stdlib-only (§5/§7) → T6/T7; eval seeded from skill (§8) → T8; two-facet taxonomy + reconciliation first (§5/§15) → T1; model A/B + full book (§6/§15) → T11/T12; distribution (§11/§15) → T13; handoff (Spencer's request) → T10.
- **Types consistent across tasks:** `Element`(kind,text,page,level) T2→T3; `RawUnit`(text,chapter,section,page,is_code) T3→T5/T8; `Enrichment` fields T5 == `units` columns T6; `StructuredExtractor.extract` T4 used in T5; `to_match_query`/`search` T6 used in T7/T8.
- **Known follow-ups (not blockers):** `taxonomy.py` helpers (`load_card`, `chapter_pages`, `principle_for_chapter`) land alongside Task 1's YAML; BM25 `_WEIGHTS` are eval-tuned in T11/T12; unit granularity (section-run) may be refined after eval.
