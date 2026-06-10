"""Per-corpus configuration. The engine is corpus-agnostic; a Profile carries the
document-specific knobs so no corpus details are hardcoded in engine logic.
Instances live under corpora/<name>/profile.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Profile:
    """Document-specific parse/segment/build configuration.

    Attributes:
        corpus_path: Path to the source PDF.
        code_font: Substring identifying the code/monospace font (matched as a substring,
            because PDFs use subset prefixes like ``AAAAAE+LucidaSans-Typewriter``).
        head_font: Substring identifying the heading font.
        chapter_size: Minimum span size (pt) for a chapter-title heading (level 1).
        section_size: Minimum span size (pt) for a section heading (level 2).
        figure_min_area: Minimum image area (px^2) to count as a real figure (drops icons).
        section_re: Regex whose first group captures a section number in a heading
            (e.g. ``r"^(\\d+\\.\\d+)"``). Used by the later segment stage.
        chapter_pages: Map of chapter id -> (first_page, last_page), 1-based inclusive.
    """

    corpus_path: Path
    code_font: str
    head_font: str
    chapter_size: float
    section_size: float
    figure_min_area: int
    section_re: str
    chapter_pages: dict[str, tuple[int, int]] = field(default_factory=dict)
