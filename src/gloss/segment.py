"""Deterministic unit boundaries. Verbatim text is fixed here; the LLM never
changes it. A unit is a contiguous prose run within a section, or a single code
block. ``section_texts`` gives the full per-section text used as situating
context during enrichment.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .parse import Element
from .profile import Profile


@dataclass
class RawUnit:
    """One retrieval unit's verbatim text + provenance, before LLM enrichment.

    Attributes:
        text: The unit's verbatim text — a joined prose run or a single code block.
        chapter: The chapter id this unit belongs to (the ``chapter`` segment arg).
        section: The section id (e.g. ``"6.3"``); the chapter id for prose/code
            appearing before the first level-2 heading.
        page: 1-based page number where the unit begins.
        is_code: ``True`` for a code-block unit, ``False`` for a prose run.
    """

    text: str
    chapter: str
    section: str
    page: int
    is_code: bool = False


def segment(elements: list[Element], profile: Profile, chapter: str) -> tuple[list[RawUnit], dict[str, str]]:
    """Group parsed Elements into RawUnits + per-section situating text.

    Walks the ordered Elements applying the segmentation rule: a heading flushes
    any in-progress prose run; a level-2 heading whose text matches
    ``profile.section_re`` starts a new section (the first captured group is the
    section id), while a level-1 heading flushes prose but keeps the current
    section — except a level-1 (chapter) title seen *after* body content has
    begun, which marks the next chapter and stops segmentation (so a
    single-chapter call excludes the following chapter). A contiguous run of
    ``para`` Elements within a section becomes one
    prose unit; each ``code`` Element is its own unit; ``figure`` Elements are
    skipped. Until the first level-2 heading, the section is ``chapter``.

    Args:
        elements: Parsed structural Elements in reading order.
        profile: Carries ``section_re``, the regex whose first group captures a
            section number in a level-2 heading.
        chapter: The chapter id, used as the section for content before the first
            level-2 heading.

    Returns:
        A ``(units, section_texts)`` tuple. ``units`` are the RawUnits in order.
        ``section_texts`` maps each section id to the concatenated text of its
        paras and code blocks (heading text excluded), for situating context.
    """
    section_re = re.compile(profile.section_re)
    units: list[RawUnit] = []
    section_texts: dict[str, list[str]] = {}
    cur_section = chapter
    prose: list[str] = []
    prose_page = 0
    started = False  # True once body content (a section, paragraph, or code) has begun

    def flush_prose() -> None:
        """Emit any buffered prose lines as one joined prose RawUnit."""
        nonlocal prose, prose_page
        if prose:
            units.append(RawUnit("\n".join(prose), chapter, cur_section, prose_page, False))
            prose = []

    for el in elements:
        if el.kind == "figure":
            continue
        if el.kind == "heading":
            # A level-1 (chapter) title after body content has begun marks the
            # next chapter; a single-chapter segment stops there.
            if el.level == 1 and started:
                flush_prose()
                break
            flush_prose()
            if el.level == 2:
                started = True
                m = section_re.match(el.text.strip())
                if m:
                    cur_section = m.group(1)
            section_texts.setdefault(cur_section, [])
            continue
        section_texts.setdefault(cur_section, []).append(el.text)
        started = True
        if el.kind == "code":
            flush_prose()
            units.append(RawUnit(el.text, chapter, cur_section, el.page, True))
        else:  # para
            if not prose:
                prose_page = el.page
            prose.append(el.text)
    flush_prose()
    return units, {k: "\n".join(v) for k, v in section_texts.items()}
