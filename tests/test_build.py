from pathlib import Path
from gloss.build import estimate_num_ctx, load_prompt, load_profile

_INSTANCE = Path("corpora/aposd")


def test_load_prompt_splits_on_marker():
    system, template = load_prompt(_INSTANCE)
    assert "enrich" in system.lower() and "<!--" not in system
    assert "{card}" in template and "{section}" in template and "{passage}" in template
    assert "<!--" not in template


def test_load_profile_has_aposd_values():
    p = load_profile(_INSTANCE)
    assert p.code_font == "Typewriter" and p.chapter_re == r"^Chapter\s+(\d+)"
    assert p.chapter_pages == {}                 # detection, not hardcoded ranges
    assert p.corpus_path.name.endswith(".pdf")


def test_estimate_num_ctx_floor_and_cap():
    assert estimate_num_ctx([], "") == 8192                 # floor
    assert estimate_num_ctx(["x" * 200_000], "") == 32768   # cap (warns)
    mid = estimate_num_ctx(["x" * 30_000], "")
    assert 8192 <= mid <= 32768


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
