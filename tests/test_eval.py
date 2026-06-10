from gloss.store import build_db
from gloss.evalrun import score_cases

_ROWS = [
    {"text": "A module with a complex interface for little functionality is shallow.",
     "principle": "deep-modules", "chapter": "4", "section": "4.5", "type": "red_flag",
     "page": 45, "context_line": "module depth", "applies_when": "thin wrapper",
     "key_terms": ["shallow module"], "questions": ["too shallow?"],
     "enrich_model": "stub", "needs_enrich": 0},
    {"text": "A general-purpose changePosition method covers many UI operations.",
     "principle": "general-purpose", "chapter": "6", "section": "6.3", "type": "example",
     "page": 52, "context_line": "general-purpose API", "applies_when": "special method smell",
     "key_terms": ["general-purpose"], "questions": ["too specialized?"],
     "enrich_model": "stub", "needs_enrich": 0},
]


def test_score_cases_all_hit(tmp_path):
    db = tmp_path / "aposd.db"; build_db(_ROWS, db)
    cases = [{"query": "module interface complex little functionality", "expect_section": "4.5"},
             {"query": "changePosition general purpose method", "expect_section": "6.3"}]
    assert score_cases(db, cases, k=3)["hit_rate"] == 1.0


def test_score_cases_miss(tmp_path):
    db = tmp_path / "aposd.db"; build_db(_ROWS, db)
    cases = [{"query": "module interface complex", "expect_section": "9.9"}]  # no such section
    assert score_cases(db, cases, k=3)["hit_rate"] == 0.0
