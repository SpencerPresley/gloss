"""Per-unit LLM enrichment: generate retrieval metadata for each RawUnit via a
StructuredExtractor. Substantive content comes only from the passage; the taxonomy
card supplies which-principle + preferred phrasing. Checkpointed to JSONL so an
interrupted run resumes from the last completed unit.
"""
from __future__ import annotations
import hashlib
import json
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
    """Stable per-unit checkpoint key (section + kind + text prefix)."""
    raw = f"{unit.section}|{unit.is_code}|{unit.text[:64]}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _done_keys(checkpoint: Path) -> set[str]:
    """Keys already written to the checkpoint (for resume)."""
    if not checkpoint.exists():
        return set()
    return {json.loads(line)["key"] for line in checkpoint.read_text().splitlines() if line.strip()}


def enrich_units(units, section_texts, extractor: StructuredExtractor, *,
                 card: str, template: str, system: str, checkpoint: Path) -> list[dict]:
    """Enrich each unit (skipping any already checkpointed) and return all rows.

    Each unit is appended to the JSONL checkpoint as it completes, so an interrupted
    run resumes. Code units are forced to type='code'; a unit whose extraction fails
    keeps its verbatim text with empty generated fields and needs_enrich=1.
    """
    checkpoint = Path(checkpoint)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    model = getattr(extractor, "model", "stub")
    done = _done_keys(checkpoint)
    with checkpoint.open("a") as handle:
        for unit in units:
            key = _key(unit)
            if key in done:
                continue
            prompt = build_prompt(unit, section_texts.get(unit.section, ""), card, template)
            try:
                fields = extractor.extract(prompt, Enrichment, system=system).model_dump()
                needs = 0
            except Exception:
                fields = {"principle": "", "type": "code" if unit.is_code else "rationale",
                          "context_line": "", "applies_when": "", "key_terms": [], "questions": []}
                needs = 1
            if unit.is_code:
                fields["type"] = "code"
            row = {"key": key, "text": unit.text, "chapter": unit.chapter, "section": unit.section,
                   "page": unit.page, "enrich_model": model, "needs_enrich": needs, **fields}
            handle.write(json.dumps(row) + "\n")
            done.add(key)
    return [json.loads(line) for line in checkpoint.read_text().splitlines() if line.strip()]
