"""APOSD instance profile for the gloss engine."""
from pathlib import Path
from gloss.profile import Profile

_REPO = Path(__file__).resolve().parents[2]

APOSD = Profile(
    corpus_path=_REPO / "resources" / "2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf",
    code_font="Typewriter",
    head_font="NimbusSanL-Bol",
    chapter_size=20.0,
    section_size=16.0,
    figure_min_area=5000,
    section_re=r"^(\d+\.\d+)",
    chapter_pages={"6": (50, 58)},
)
