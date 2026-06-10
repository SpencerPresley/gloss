"""Font-aware PDF parser: the engine's first stage.

Turns a PDF page range into ordered structural :class:`Element` objects (headings,
paragraphs, code blocks, figures) in reading order. A later segment stage groups
these into retrieval units. All document-specific thresholds come from a
:class:`~gloss.profile.Profile`, never hardcoded here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF

from .profile import Profile

Kind = Literal["heading", "para", "code", "figure"]


@dataclass
class Element:
    """One structural element in reading order.

    Attributes:
        kind: The structural role of the element.
        text: The element's text (or a ``"[FIGURE WxH]"`` placeholder for figures).
        page: 1-based page number the element was found on.
        level: Heading depth (1 = chapter title, 2 = section); 0 for non-headings.
    """

    kind: Kind
    text: str
    page: int
    level: int = 0


def classify_font(font: str, profile: Profile) -> str:
    """Classify a span font as 'code', 'head', or 'body' by substring match.

    PDFs embed subset prefixes (e.g. ``AAAAAE+LucidaSans-Typewriter``), so the
    profile fonts are matched as substrings rather than by equality.

    Args:
        font: The span's font name as reported by PyMuPDF.
        profile: Carries the ``code_font`` and ``head_font`` substrings to match.

    Returns:
        ``"code"`` if the code font matches, ``"head"`` if the heading font
        matches, otherwise ``"body"``.
    """
    if profile.code_font in font:
        return "code"
    if profile.head_font in font:
        return "head"
    return "body"


def parse_pdf(path: Path, first_page: int | None, last_page: int | None,
              profile: Profile) -> list[Element]:
    """Extract ordered structural Elements from a PDF page range.

    Iterates the (1-based, inclusive) page range — or the whole document when both
    bounds are ``None`` — and walks PyMuPDF's block/line/span structure, which is
    already in reading order. Per line: a line whose spans are >=60% heading-font
    becomes a ``heading`` (level 1 if its max span size reaches
    ``profile.chapter_size``, else level 2); a line whose every non-space span is
    code-font accumulates into a contiguous ``code`` block (flushed when a non-code
    line or heading interrupts it); any other line becomes a ``para``. Image blocks
    at or above ``profile.figure_min_area`` become a ``figure`` placeholder; smaller
    images (decorative icons) are skipped.

    Args:
        path: Path to the source PDF.
        first_page: First page to parse, 1-based inclusive (``None`` for page 1).
        last_page: Last page to parse, 1-based inclusive (``None`` for the last page).
        profile: Document-specific fonts and thresholds.

    Returns:
        The structural elements in reading order across the requested pages.
    """
    doc = fitz.open(path)
    lo = (first_page or 1) - 1
    hi = last_page or len(doc)
    out: list[Element] = []
    code_buf: list[str] = []
    code_page = 0

    def flush_code() -> None:
        """Emit any buffered code lines as a single joined ``code`` Element."""
        nonlocal code_buf, code_page
        if code_buf:
            out.append(Element("code", "\n".join(code_buf), code_page))
            code_buf = []

    for pno in range(lo, hi):
        page = doc[pno]
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") == 1:  # image
                w = block["bbox"][2] - block["bbox"][0]
                h = block["bbox"][3] - block["bbox"][1]
                if w * h >= profile.figure_min_area:
                    flush_code()
                    out.append(Element("figure", f"[FIGURE {int(w)}x{int(h)}]", pno + 1))
                continue
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                classes = [classify_font(s["font"], profile) for s in spans]
                text = "".join(s["text"] for s in line["spans"]).rstrip()
                size = max(s["size"] for s in spans)
                if sum(c == "head" for c in classes) / len(classes) >= 0.6:
                    flush_code()
                    level = 1 if size >= profile.chapter_size else 2
                    out.append(Element("heading", text.strip(), pno + 1, level))
                elif all(c == "code" for c in classes):
                    if not code_buf:
                        code_page = pno + 1
                    code_buf.append(text)
                else:
                    flush_code()
                    out.append(Element("para", text.strip(), pno + 1))
    flush_code()
    doc.close()
    return out
