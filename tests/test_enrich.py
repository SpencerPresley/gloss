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


def test_enrich_units_concurrent_builds_chat_once(tmp_path):
    # The warmup must build the OllamaExtractor's chat (and pin its method) ONCE before the
    # pool starts, so concurrent workers only READ shared state — no lazy-init/probe race.
    import threading
    from gloss.segment import RawUnit
    from gloss.extract import OllamaExtractor
    from gloss.enrich import enrich_units, Enrichment

    builds = []
    lock = threading.Lock()

    class _FakeRunnable:
        def invoke(self, messages):
            return {"parsed": Enrichment(principle="p", type="rationale", context_line="c",
                                         applies_when="a", key_terms=["k"], questions=["q?"]),
                    "parsing_error": None}

    class _FakeChat:
        def with_structured_output(self, schema, method, include_raw):
            return _FakeRunnable()

    def factory():
        with lock:
            builds.append(1)
        return _FakeChat()

    ex = OllamaExtractor("fake", chat_factory=factory)
    units = [RawUnit(f"u{i}", "6", "6.1", 50) for i in range(15)]
    enrich_units(units, {"6.1": "s"}, ex, card="C", template="{card}{section}{passage}",
                 system="s", checkpoint=tmp_path / "u.jsonl", max_workers=4)
    assert builds == [1]   # chat built exactly once (warmup), not once-per-thread


def test_enrich_resumes_failed_units_without_dup_rows(tmp_path, monkeypatch):
    # A quota-cap failure must NOT be baked permanently: a later --resume re-enriches
    # the failed units, and the recovered success must not create a duplicate row.
    monkeypatch.setattr("gloss.enrich.time.sleep", lambda s: None)  # skip retry backoff
    import json
    from gloss.segment import RawUnit
    from gloss.enrich import enrich_units
    payload = {"principle": "general-purpose", "type": "rationale", "context_line": "c",
               "applies_when": "a", "key_terms": ["k"], "questions": ["q?"]}

    class _CapExtractor:        # simulates a quota cap: every call fails
        model = "cap"
        def extract(self, prompt, schema, *, system=None):
            raise RuntimeError("simulated quota cap")

    class _OkExtractor:         # cap lifted: every call succeeds
        model = "ok"
        def extract(self, prompt, schema, *, system=None):
            return schema(**payload)

    units = [RawUnit(f"passage {i}", "6", "6.1", 50) for i in range(3)]
    sect = {"6.1": "s"}
    ckpt = tmp_path / "units.jsonl"

    rows1 = enrich_units(units, sect, _CapExtractor(), card="C",
                         template="{card}{section}{passage}", system="s", checkpoint=ckpt)
    assert len(rows1) == 3 and all(r["needs_enrich"] == 1 for r in rows1)   # all failed

    rows2 = enrich_units(units, sect, _OkExtractor(), card="C",
                         template="{card}{section}{passage}", system="s", checkpoint=ckpt)
    assert len(rows2) == 3                                  # deduped by key, not 6
    assert all(r["needs_enrich"] == 0 for r in rows2)       # all recovered on resume
    assert len({r["key"] for r in rows2}) == 3
