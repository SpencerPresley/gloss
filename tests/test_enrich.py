from gloss.segment import RawUnit
from gloss.extract import StubExtractor
from gloss.enrich import enrich_units, build_prompt

_STUB = {"principle": "general-purpose", "type": "rationale",
         "context_line": "Ch.6 on general-purpose APIs.",
         "applies_when": "Designing a class's public methods.",
         "key_terms": ["general-purpose", "special-purpose"],
         "questions": ["Is this API too specialized?"]}
_TEMPLATE = "CARD:\n{card}\nSECTION:\n{section}\nPASSAGE:\n{passage}"
_SYSTEM = "system instructions"


def _units():
    return [RawUnit("Prose about general-purpose design.", "6", "6.1", 50),
            RawUnit("Position changePosition(Position position, int numChars);", "6", "6.3", 52, is_code=True)]


def test_build_prompt_includes_parts():
    p = build_prompt(RawUnit("body text", "6", "6.3", 52), "FULL SECTION", "THE CARD", _TEMPLATE)
    assert "THE CARD" in p and "FULL SECTION" in p and "body text" in p


def test_enrich_writes_rows_and_forces_code_type(tmp_path):
    ckpt = tmp_path / "units.jsonl"
    rows = enrich_units(_units(), {"6.1": "s1", "6.3": "s3"}, StubExtractor(_STUB),
                        card="CARD", template=_TEMPLATE, system=_SYSTEM, checkpoint=ckpt)
    assert len(rows) == 2
    code_row = [r for r in rows if r["section"] == "6.3"][0]
    assert code_row["type"] == "code"
    assert rows[0]["enrich_model"] == "stub" and rows[0]["needs_enrich"] == 0
    assert ckpt.read_text().count("\n") == 2


def test_enrich_resumes_without_duplicates(tmp_path):
    ckpt = tmp_path / "units.jsonl"
    enrich_units(_units(), {"6.1": "s1", "6.3": "s3"}, StubExtractor(_STUB),
                 card="CARD", template=_TEMPLATE, system=_SYSTEM, checkpoint=ckpt)
    rows = enrich_units(_units(), {"6.1": "s1", "6.3": "s3"}, StubExtractor(_STUB),
                        card="CARD", template=_TEMPLATE, system=_SYSTEM, checkpoint=ckpt)
    assert len(rows) == 2 and ckpt.read_text().count("\n") == 2


def test_enrich_flags_failure(tmp_path):
    class _Boom:
        model = "boom"
        def extract(self, prompt, schema, *, system=None):
            raise RuntimeError("model unavailable")
    ckpt = tmp_path / "units.jsonl"
    rows = enrich_units([RawUnit("prose", "6", "6.1", 50)], {"6.1": "s1"}, _Boom(),
                        card="C", template=_TEMPLATE, system=_SYSTEM, checkpoint=ckpt)
    assert rows[0]["needs_enrich"] == 1 and rows[0]["text"] == "prose"


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


def test_enrich_units_concurrent_resumes(tmp_path):
    import json
    from gloss.segment import RawUnit
    from gloss.extract import StubExtractor
    from gloss.enrich import enrich_units
    units = [RawUnit(f"passage {i}", "6", "6.1", 50) for i in range(10)]
    sect = {"6.1": "s"}
    stub = StubExtractor({"principle": "general-purpose", "type": "rationale",
                          "context_line": "c", "applies_when": "a",
                          "key_terms": ["k"], "questions": ["q?"]})
    ckpt = tmp_path / "units.jsonl"
    enrich_units(units[:5], sect, stub, card="C", template="{card}{section}{passage}",
                 system="s", checkpoint=ckpt, max_workers=4)
    rows = enrich_units(units, sect, stub, card="C", template="{card}{section}{passage}",
                        system="s", checkpoint=ckpt, max_workers=4)
    keys = [json.loads(l)["key"] for l in ckpt.read_text().splitlines() if l.strip()]
    assert len(rows) == 10
    assert len(keys) == 10 and len(set(keys)) == 10    # no re-enrichment, no dupes
