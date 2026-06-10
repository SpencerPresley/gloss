"""Tests for the deterministic segment stage.

These pin the engine's second stage: grouping ordered parse Elements into
RawUnits (one per contiguous prose run or single code block) and producing the
per-section situating text. Inputs are hand-built Elements — no PDF — so the
segmentation rule is tested in isolation from parsing.
"""
from pathlib import Path

from gloss.parse import Element
from gloss.profile import Profile
from gloss.segment import segment, RawUnit


def _profile() -> Profile:
    return Profile(corpus_path=Path("x"), code_font="Typewriter", head_font="NimbusSanL-Bol",
                   chapter_size=20.0, section_size=16.0, figure_min_area=5000,
                   section_re=r"^(\d+\.\d+)", chapter_pages={})


def test_segment_splits_prose_and_code():
    els = [
        Element("heading", "Chapter 6", 50, 1),
        Element("heading", "General-Purpose Modules are Deeper", 50, 1),
        Element("para", "One of the most common decisions you will face...", 50),
        Element("heading", "6.3 A more general-purpose API", 52, 2),
        Element("para", "A better approach is to make the text class general-purpose.", 52),
        Element("code", "Position changePosition(Position position, int numChars);", 52),
        Element("para", "This method returns a new position.", 52),
    ]
    units, section_texts = segment(els, _profile(), chapter="6")
    assert [u.is_code for u in units] == [False, False, True, False]
    assert units[0].section == "6"            # chapter intro prose, before any 6.x section
    code = [u for u in units if u.is_code][0]
    assert code.section == "6.3" and "changePosition" in code.text
    assert "better approach" in section_texts["6.3"]
    assert "changePosition" in section_texts["6.3"]   # section text includes code


def test_heading_only_section_is_present_but_empty():
    """A section whose heading has no body still appears, mapping to ''."""
    els = [
        Element("heading", "6.5 Conclusions", 60, 2),
        Element("heading", "6.6 Taking it too far", 61, 2),
    ]
    _, section_texts = segment(els, _profile(), chapter="6")
    assert section_texts["6.5"] == ""


def test_figure_between_paragraphs_does_not_split_prose():
    """A figure carries no prose text, so it must not fragment a prose run."""
    els = [
        Element("para", "before the figure", 50),
        Element("figure", "[FIGURE 100x100]", 50),
        Element("para", "after the figure", 50),
    ]
    units, _ = segment(els, _profile(), chapter="6")
    prose = [u for u in units if not u.is_code]
    assert len(prose) == 1
    assert "before the figure" in prose[0].text and "after the figure" in prose[0].text
