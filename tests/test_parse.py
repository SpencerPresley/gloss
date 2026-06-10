"""Tests for the font-aware PDF parser.

These tests pin the engine's first stage: turning a PDF page range into ordered
structural Elements. The APOSD-specific knobs live in a Profile built inline here
(the engine must stay corpus-agnostic), never imported from a corpora/ instance.
"""
from pathlib import Path

from gloss.profile import Profile
from gloss.parse import classify_font, parse_pdf


def _aposd_profile(corpus_path: Path) -> Profile:
    """Build a Profile carrying the empirically-verified APOSD parse knobs."""
    return Profile(corpus_path=corpus_path, code_font="Typewriter", head_font="NimbusSanL-Bol",
                   chapter_size=20.0, section_size=16.0, figure_min_area=5000,
                   section_re=r"^(\d+\.\d+)", chapter_pages={"6": (50, 58)})


def test_classify_font():
    """Fonts classify by substring against code_font then head_font, else body."""
    p = _aposd_profile(Path("x"))
    assert classify_font("AAAAAE+LucidaSans-Typewriter", p) == "code"
    assert classify_font("BBBB+NimbusSanL-Bol", p) == "head"
    assert classify_font("CCCC+NimbusRomNo9L-Regu", p) == "body"


def test_parse_ch6_has_heading_code_para(corpus_path):
    """Chapter 6's opening pages yield headings, a real code block, and prose."""
    p = _aposd_profile(corpus_path)
    els = parse_pdf(corpus_path, 50, 53, p)
    kinds = {e.kind for e in els}
    assert {"heading", "code", "para"} <= kinds
    assert any(e.kind == "heading" and e.level == 1 and "General-Purpose" in e.text for e in els)
    assert any(e.kind == "heading" and e.level == 2 and e.text.startswith("6.1") for e in els)
    code = "\n".join(e.text for e in els if e.kind == "code")
    assert "changePosition(Position position, int numChars)" in code


def test_parse_detects_figure(corpus_path):
    """A real embedded image yields a figure Element with non-zero dimensions."""
    p = _aposd_profile(corpus_path)
    # Page 21 holds a large diagram (~374x172 pt); parse just that page to stay fast.
    els = parse_pdf(corpus_path, 21, 21, p)
    figures = [e for e in els if e.kind == "figure"]
    assert figures, "expected a figure Element on page 21"
    w, h = (int(n) for n in figures[0].text.removeprefix("[FIGURE ").rstrip("]").split("x"))
    assert w > 0 and h > 0
