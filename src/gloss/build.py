"""Wire parse -> segment -> enrich -> store for one chapter (or the whole book).

Loads the corpus instance (profile, taxonomy, prompt) from corpora/<name>/ and
sizes num_ctx from the actual prompts (conservative chars//3 + headroom, capped
and warned) rather than guessing a constant.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

from .parse import parse_pdf
from .segment import segment
from .enrich import build_prompt, enrich_units
from .extract import OllamaExtractor
from .taxonomy import load_taxonomy, principle_for_chapter, card_for

_DEFAULT_INSTANCE = Path("corpora/aposd")


def load_profile(instance: Path):
    """Import the corpus instance's Profile (its module-level ``APOSD``)."""
    spec = importlib.util.spec_from_file_location("_gloss_instance_profile", Path(instance) / "profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.APOSD


def load_prompt(instance: Path) -> tuple[str, str]:
    """Split the instance prompt.md into (system, user_template) on the TEMPLATE marker."""
    text = (Path(instance) / "prompt.md").read_text()
    system, template = text.split("<!-- TEMPLATE -->", 1)
    return system.replace("<!-- SYSTEM -->", "").strip(), template.strip()


def estimate_num_ctx(prompts: list[str], system: str,
                     headroom: int = 2048, floor: int = 8192, cap: int = 32768) -> int:
    """Size num_ctx from real prompts. chars//3 deliberately OVER-estimates tokens
    (undercounting would truncate); warn rather than silently exceed the cap."""
    longest = max((len(system) + len(p) for p in prompts), default=0)
    need = longest // 3 + headroom
    if need > cap:
        print(f"WARNING: largest prompt ~{need} est tokens exceeds num_ctx cap {cap}; "
              f"trim situating context or raise the cap")
    return max(floor, min(need, cap))


def run_build(chapter, model, db, resume, instance: Path = _DEFAULT_INSTANCE):
    """Build the corpus db for one chapter (or whole book if chapter is None)."""
    profile = load_profile(instance)
    taxonomy = load_taxonomy(Path(instance) / "taxonomy.yaml")
    system, template = load_prompt(instance)

    first, last = profile.chapter_pages.get(chapter, (None, None)) if chapter else (None, None)
    elements = parse_pdf(profile.corpus_path, first, last, profile)
    units, section_texts = segment(elements, profile, chapter or "")

    principle = principle_for_chapter(taxonomy, chapter) if chapter else None
    card = card_for(taxonomy, principle) if principle else ""

    prompts = [build_prompt(u, section_texts.get(u.section, ""), card, template) for u in units]
    num_ctx = estimate_num_ctx(prompts, system)
    print(f"chapter={chapter} units={len(units)} principle={principle} num_ctx={num_ctx} model={model}")

    checkpoint = Path("build") / f"ch{chapter or 'all'}" / "units.jsonl"
    if not resume and checkpoint.exists():
        checkpoint.unlink()
    extractor = OllamaExtractor(model, num_ctx=num_ctx)
    rows = enrich_units(units, section_texts, extractor, card=card, template=template,
                        system=system, checkpoint=checkpoint)
    from .store import build_db
    build_db(rows, Path(db))
    print(f"built {len(rows)} units -> {db}")
    return rows
