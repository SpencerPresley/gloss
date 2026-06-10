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
                   section_re=r"^(\d+\.\d+)", chapter_re=r"^Chapter\s+(\d+)", chapter_pages={})


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


def test_segment_stops_at_next_chapter_title():
    """A level-1 chapter title after body content marks the next chapter — stop there."""
    els = [
        Element("heading", "Chapter 6", 50, 1),
        Element("heading", "General-Purpose Modules are Deeper", 50, 1),
        Element("para", "Chapter 6 intro prose.", 50),
        Element("heading", "6.1 Make classes somewhat general-purpose", 50, 2),
        Element("para", "Section 6.1 body.", 50),
        Element("heading", "Chapter 7", 56, 1),
        Element("heading", "Different Layer, Different Abstraction", 56, 1),
        Element("para", "Chapter 7 intro must be excluded.", 56),
        Element("heading", "7.1 Pass-through methods", 57, 2),
        Element("para", "Section 7.1 body must be excluded.", 57),
    ]
    units, section_texts = segment(els, _profile(), chapter="6")
    assert sorted({u.section for u in units}) == ["6", "6.1"]   # nothing from chapter 7
    assert "7.1" not in section_texts
    assert all("excluded" not in u.text for u in units)


def test_split_chapters_groups_by_marker():
    from gloss.parse import Element
    from gloss.segment import split_chapters
    els = [
        Element("heading", "Preface", 9, 1),            # front matter -> dropped
        Element("para", "preface body", 9),
        Element("heading", "Chapter 1", 13, 1),
        Element("heading", "Introduction", 13, 1),      # title line, not a boundary
        Element("para", "intro body", 13),
        Element("heading", "1.1 Something", 14, 2),
        Element("para", "more intro", 14),
        Element("heading", "Chapter 2", 18, 1),
        Element("heading", "The Nature of Complexity", 18, 1),
        Element("para", "ch2 body", 18),
        Element("heading", "Index", 178, 1),            # back matter -> stays in last span
    ]
    chapters = split_chapters(els, _profile())
    assert [cid for cid, _ in chapters] == ["1", "2"]
    by_id = dict(chapters)
    assert any(e.text == "intro body" for e in by_id["1"])
    assert any(e.text == "1.1 Something" for e in by_id["1"])
    assert all(e.text != "preface body" for e in by_id["1"])     # front matter excluded
    assert any(e.text == "ch2 body" for e in by_id["2"])


def test_split_chapters_empty_without_marker():
    from gloss.parse import Element
    from gloss.profile import Profile
    from gloss.segment import split_chapters
    no_re = Profile(corpus_path=Path("x"), code_font="Typewriter", head_font="NimbusSanL-Bol",
                    chapter_size=20.0, section_size=16.0, figure_min_area=5000,
                    section_re=r"^(\d+\.\d+)")  # chapter_re defaults to ""
    els = [Element("heading", "Chapter 1", 1, 1), Element("para", "body", 1)]
    assert split_chapters(els, no_re) == []


def test_split_chapters_real_pdf_finds_all_21(corpus_path):
    from gloss.build import load_profile
    from gloss.parse import parse_pdf
    from gloss.segment import split_chapters
    profile = load_profile(Path("corpora/aposd"))
    els = parse_pdf(corpus_path, None, None, profile)
    chapters = split_chapters(els, profile)
    assert [cid for cid, _ in chapters] == [str(n) for n in range(1, 22)]
