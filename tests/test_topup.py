import json

from docq.enrich import enrich_units, rows_by_key
from docq.extract import StubExtractor
from docq.segment import RawUnit
from docq.topup import build_topup_prompt, topup_questions

_TAXONOMY = {"principles": [{"slug": "deep-modules", "name": "Deep Modules",
                             "vocabulary": ["deep module", "shallow module"]}],
             "topics": [{"chapter": "4", "principle": "deep-modules"}]}
_TEMPLATE = "CARD:\n{card}\nPASSAGE:\n{passage}\nEXISTING:\n{questions}"
_SYSTEM = "system instructions"


class _CountingStub(StubExtractor):
    def __init__(self, payload):
        super().__init__(payload)
        self.calls = 0

    def extract(self, prompt, schema, *, system=None):
        self.calls += 1
        return super().extract(prompt, schema, system=system)


def _row(key="k1", chapter="4", **over):
    row = {"key": key, "text": "passage text", "chapter": chapter, "section": "4.1",
           "page": 31, "enrich_model": "minimax", "needs_enrich": 0,
           "principle": "deep-modules", "type": "rationale", "context_line": "c",
           "applies_when": "a", "key_terms": ["k"], "questions": ["Old question?"]}
    row.update(over)
    return row


def _seed(tmp_path, rows, chapter_dir="ch4"):
    ckpt = tmp_path / chapter_dir / "units.jsonl"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return ckpt


def test_topup_appends_superseding_row_with_merged_questions(tmp_path):
    ckpt = _seed(tmp_path, [_row()])
    original_first_line = ckpt.read_text().splitlines()[0]
    counts = topup_questions(tmp_path, _TAXONOMY, StubExtractor({"questions": ["New angle?"]}),
                             template=_TEMPLATE, system=_SYSTEM)
    assert counts == {"pending": 1, "topped_up": 1, "failed": 0}
    lines = ckpt.read_text().splitlines()
    assert len(lines) == 2 and lines[0] == original_first_line   # append-only
    current = rows_by_key(ckpt)["k1"]
    assert current["questions"] == ["Old question?", "New angle?"]  # old preserved, new appended
    assert current["topup_model"] == "stub"
    assert current["text"] == "passage text" and current["needs_enrich"] == 0


def test_topup_resumes_without_recalling(tmp_path):
    ckpt = _seed(tmp_path, [_row()])
    stub = _CountingStub({"questions": ["New angle?"]})
    topup_questions(tmp_path, _TAXONOMY, stub, template=_TEMPLATE, system=_SYSTEM)
    counts = topup_questions(tmp_path, _TAXONOMY, stub, template=_TEMPLATE, system=_SYSTEM)
    assert stub.calls == 1 and counts["pending"] == 0
    assert ckpt.read_text().count("\n") == 2


def test_topup_skips_failed_enrichment_rows(tmp_path):
    ckpt = _seed(tmp_path, [_row(key="bad", needs_enrich=1, questions=[])])
    stub = _CountingStub({"questions": ["New angle?"]})
    counts = topup_questions(tmp_path, _TAXONOMY, stub, template=_TEMPLATE, system=_SYSTEM)
    assert stub.calls == 0 and counts["pending"] == 0
    assert ckpt.read_text().count("\n") == 1


def test_topup_failure_appends_nothing_and_is_retried_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr("docq.topup.time.sleep", lambda s: None)   # skip retry backoff

    class _Boom:
        model = "boom"

        def extract(self, prompt, schema, *, system=None):
            raise RuntimeError("model unavailable")

    ckpt = _seed(tmp_path, [_row()])
    counts = topup_questions(tmp_path, _TAXONOMY, _Boom(), template=_TEMPLATE, system=_SYSTEM)
    assert counts == {"pending": 1, "topped_up": 0, "failed": 1}
    assert ckpt.read_text().count("\n") == 1                        # nothing baked in
    counts = topup_questions(tmp_path, _TAXONOMY, StubExtractor({"questions": ["New angle?"]}),
                             template=_TEMPLATE, system=_SYSTEM)
    assert counts["topped_up"] == 1                                 # recovered on the next run
    assert rows_by_key(ckpt)["k1"]["questions"] == ["Old question?", "New angle?"]


def test_topup_drops_exact_restatements_of_existing_questions(tmp_path):
    ckpt = _seed(tmp_path, [_row()])
    stub = StubExtractor({"questions": ["Old question?", "  old   QUESTION? ", "Fresh angle?", ""]})
    topup_questions(tmp_path, _TAXONOMY, stub, template=_TEMPLATE, system=_SYSTEM)
    assert rows_by_key(ckpt)["k1"]["questions"] == ["Old question?", "Fresh angle?"]


def test_topup_prompt_carries_card_passage_and_existing_questions(tmp_path):
    prompts = []

    class _Recorder:
        model = "rec"

        def extract(self, prompt, schema, *, system=None):
            prompts.append(prompt)
            return schema(questions=["New angle?"])

    _seed(tmp_path, [_row()])                                       # ch4 -> deep-modules card
    _seed(tmp_path, [_row(key="k2", chapter="99")], chapter_dir="ch99")   # unmapped -> empty card
    topup_questions(tmp_path, _TAXONOMY, _Recorder(), template=_TEMPLATE, system=_SYSTEM)
    carded = [p for p in prompts if "deep-modules" in p]
    assert len(prompts) == 2 and len(carded) == 1
    assert "passage text" in carded[0] and "- Old question?" in carded[0]


def test_topup_prompt_renders_empty_question_list(tmp_path):
    assert "(none)" in build_topup_prompt(_row(questions=[]), "", _TEMPLATE)


def test_topup_concurrent_appends_all_without_dupes(tmp_path):
    rows = [_row(key=f"k{i}") for i in range(10)]
    ckpt = _seed(tmp_path, rows)
    topup_questions(tmp_path, _TAXONOMY, StubExtractor({"questions": ["New angle?"]}),
                    template=_TEMPLATE, system=_SYSTEM, max_workers=4)
    lines = [json.loads(l) for l in ckpt.read_text().splitlines()]
    topped = [r for r in lines if r.get("topup_model")]
    assert len(lines) == 20 and len(topped) == 10
    assert len({r["key"] for r in topped}) == 10


def test_topup_ships_through_enrich_readback(tmp_path):
    """The rebuild path (enrich_units under --resume) must return the merged
    questions with zero re-enrichment — that is the whole persistence design."""
    enrich_payload = {"principle": "deep-modules", "type": "rationale", "context_line": "c",
                      "applies_when": "a", "key_terms": ["k"], "questions": ["Old question?"]}
    units = [RawUnit("Prose about module depth.", "4", "4.1", 31)]
    ckpt = tmp_path / "ch4" / "units.jsonl"
    enrich_units(units, {"4.1": "s"}, StubExtractor(enrich_payload), card="C",
                 template="{card}{section}{passage}", system=_SYSTEM, checkpoint=ckpt)
    topup_questions(tmp_path, _TAXONOMY, StubExtractor({"questions": ["New angle?"]}),
                    template=_TEMPLATE, system=_SYSTEM)

    class _Boom:                                     # resume must not re-enrich anything
        model = "boom"

        def extract(self, prompt, schema, *, system=None):
            raise AssertionError("resume re-enriched a completed unit")

    rows = enrich_units(units, {"4.1": "s"}, _Boom(), card="C",
                        template="{card}{section}{passage}", system=_SYSTEM, checkpoint=ckpt)
    assert len(rows) == 1
    assert rows[0]["questions"] == ["Old question?", "New angle?"]
