"""Per-unit LLM enrichment: generate retrieval metadata for each RawUnit via a
StructuredExtractor. Substantive content comes only from the passage; the taxonomy
card supplies which-principle + preferred phrasing. Checkpointed to JSONL so an
interrupted run resumes from the last completed unit.
"""
from __future__ import annotations
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .extract import StructuredExtractor
from .segment import RawUnit

UnitType = Literal["definition", "rationale", "example", "code", "red_flag"]


class Enrichment(BaseModel):
    """Retrieval metadata generated for one passage (verbatim text stored separately)."""
    principle: str = Field(description="The design-principle slug this passage belongs to, from the provided card.")
    type: UnitType = Field(description="What this passage is: definition, rationale, example, code, or red_flag.")
    context_line: str = Field(description="One sentence situating this passage within its principle/section, naming the principle and the problem it addresses, to improve search.")
    applies_when: str = Field(description="One or two lines: the code/design situation or symptom where this applies, in a developer's words.")
    key_terms: list[str] = Field(description="3-8 canonical terms plus everyday synonyms a developer might search for. Distinct, no padding.")
    questions: list[str] = Field(description="3-6 short questions, in code-review/symptom language, that this passage answers.")


def build_prompt(unit: RawUnit, section_text: str, card: str, template: str) -> str:
    """Render the enrichment user prompt for a unit from the instance template."""
    return template.format(card=card, section=section_text, passage=unit.text)


def _key(unit: RawUnit) -> str:
    """Stable per-unit checkpoint key (section + kind + full verbatim text).

    Hashing the full text (not a prefix) avoids silently dropping a unit that
    shares a long prefix with another in the same section — truncation saves
    nothing before a SHA1.
    """
    raw = f"{unit.section}|{unit.is_code}|{unit.text}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _done_keys(checkpoint: Path) -> set[str]:
    """Keys already written to the checkpoint (for resume)."""
    if not checkpoint.exists():
        return set()
    return {json.loads(line)["key"] for line in checkpoint.read_text().splitlines() if line.strip()}


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
