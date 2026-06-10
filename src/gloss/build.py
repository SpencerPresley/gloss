"""Wire parse -> segment -> enrich -> store for one chapter (or the whole book).

Loads the corpus instance (profile, taxonomy, prompt) from corpora/<name>/ and
sizes num_ctx from the actual prompts (conservative chars//3 + headroom, capped
and warned) rather than guessing a constant.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

from .parse import parse_pdf
from .segment import segment, split_chapters
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


def run_build(chapter, model, db, resume, instance: Path = _DEFAULT_INSTANCE,
              extractor=None, build_dir: Path = Path("build")):
    """Build the corpus db: one chapter (``chapter`` set) or the whole book (``chapter`` None).

    Chapters are detected via ``profile.chapter_re`` (or taken from ``profile.chapter_pages``
    when that override is set). Each chapter is enriched with its own principle card; appendix
    ranges are indexed with no card. All rows accumulate into one db.

    Args:
        chapter: Chapter id to build, or None for the whole book + appendices.
        model: Ollama model tag for enrichment (ignored when ``extractor`` is given).
        db: Output db path.
        resume: Keep existing per-chapter checkpoints instead of wiping them.
        instance: Corpus instance dir (profile/taxonomy/prompt).
        extractor: Optional pre-built StructuredExtractor (tests inject a stub); when None,
            one ``OllamaExtractor`` is built and reused across all chapters.
        build_dir: Root for per-chapter JSONL checkpoints.

    Returns:
        All enrichment rows across every built chapter.
    """
    profile = load_profile(instance)
    taxonomy = load_taxonomy(Path(instance) / "taxonomy.yaml")
    system, template = load_prompt(instance)

    # 1) Resolve per-chapter element spans: explicit override, else dynamic detection.
    if profile.chapter_pages:
        specs = [(cid, parse_pdf(profile.corpus_path, first, last, profile))
                 for cid, (first, last) in profile.chapter_pages.items()]
    else:
        whole = parse_pdf(profile.corpus_path, None, None, profile)
        specs = split_chapters(whole, profile)
    if chapter is None:
        specs += [(aid, parse_pdf(profile.corpus_path, first, last, profile))
                  for aid, (first, last) in profile.appendices.items()]
    else:
        specs = [(cid, els) for cid, els in specs if cid == chapter]
        if not specs:
            raise SystemExit(f"chapter {chapter!r} not found by detection/override")

    # 2) Segment each span + build prompts; size num_ctx once over the whole build.
    plans = []          # (chapter_id, units, section_texts, card, principle)
    all_prompts: list[str] = []
    for cid, els in specs:
        units, section_texts = segment(els, profile, cid)
        principle = principle_for_chapter(taxonomy, cid)
        card = card_for(taxonomy, principle) if principle else ""
        plans.append((cid, units, section_texts, card, principle))
        all_prompts += [build_prompt(u, section_texts.get(u.section, ""), card, template)
                        for u in units]

    num_ctx = estimate_num_ctx(all_prompts, system)
    total = sum(len(units) for _, units, _, _, _ in plans)
    print(f"chapters={len(plans)} units={total} num_ctx={num_ctx} model={model}")

    # 3) One extractor for the whole build (method probed/pinned once); enrich + accumulate.
    if extractor is None:
        extractor = OllamaExtractor(model, num_ctx=num_ctx)
    all_rows: list[dict] = []
    for cid, units, section_texts, card, principle in plans:
        checkpoint = Path(build_dir) / f"ch{cid}" / "units.jsonl"
        if not resume and checkpoint.exists():
            checkpoint.unlink()
        rows = enrich_units(units, section_texts, extractor, card=card, template=template,
                            system=system, checkpoint=checkpoint)
        failed = sum(r["needs_enrich"] for r in rows)
        print(f"  ch{cid}: {len(rows)} units ({failed} failed) principle={principle or 'null'}")
        all_rows += rows

    failed = sum(r["needs_enrich"] for r in all_rows)
    if failed:
        print(f"WARNING: {failed}/{len(all_rows)} units failed enrichment — does model "
              f"{model!r} support structured output?")
    from .store import build_db
    build_db(all_rows, Path(db))
    print(f"built {len(all_rows)} units ({failed} enrichment failures) -> {db}")
    return all_rows
