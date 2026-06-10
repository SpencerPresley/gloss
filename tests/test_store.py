from gloss.store import build_db, search, to_match_query

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
    q = to_match_query("should I add a configuring flag parameter")
    assert " OR " in q and "configuring*" in q and "flag*" in q
    assert "I*" not in q   # 1-2 char tokens dropped; no raw implicit-AND


def test_build_and_search_with_filter(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(ROWS, db)
    hits = search(db, "module interface complex little functionality", k=3)
    assert hits and hits[0]["section"] == "4.5"
    filtered = search(db, "changePosition method", k=3, principles=["general-purpose"])
    assert filtered and all(h["principle"] == "general-purpose" for h in filtered)


def test_search_empty_query_returns_empty(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(ROWS, db)
    assert search(db, "a I", k=3) == []   # all tokens <=2 chars -> no MATCH -> []
