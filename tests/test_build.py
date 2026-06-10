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
    assert p.code_font == "Typewriter" and p.chapter_pages["6"] == (50, 58)
    assert p.corpus_path.name.endswith(".pdf")


def test_estimate_num_ctx_floor_and_cap():
    assert estimate_num_ctx([], "") == 8192                 # floor
    assert estimate_num_ctx(["x" * 200_000], "") == 32768   # cap (warns)
    mid = estimate_num_ctx(["x" * 30_000], "")
    assert 8192 <= mid <= 32768
