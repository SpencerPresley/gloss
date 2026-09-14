"""Load the corpus taxonomy and render a compact per-principle card for enrichment.

The taxonomy (corpora/<name>/taxonomy.yaml) is the two-facet controlled vocabulary:
coarse ``principles`` (slug + vocabulary/diagnostics/red_flags) and fine ``topics``
(chapters mapped to a principle). Enrichment feeds the LLM only the *relevant*
principle's card, never the whole taxonomy.
"""
from __future__ import annotations
from pathlib import Path
import yaml


def load_taxonomy(path: Path) -> dict:
    """Load a taxonomy.yaml into a dict with 'principles' and 'topics' keys."""
    return yaml.safe_load(Path(path).read_text())


def principle_for_chapter(taxonomy: dict, chapter: str) -> str | None:
    """Return the coarse principle slug mapped to a chapter, or None if unmapped."""
    for topic in taxonomy.get("topics", []):
        if str(topic.get("chapter")) == str(chapter):
            return topic.get("principle")
    return None


def card_for(taxonomy: dict, principle: str) -> str:
    """Render a compact card (name + vocabulary + diagnostics + red_flags) for one principle.

    Raises:
        KeyError: if no principle with that slug exists.
    """
    for entry in taxonomy.get("principles", []):
        if entry.get("slug") == principle:
            lines = [f"principle: {entry['slug']}", f"name: {entry.get('name', '')}"]
            for field in ("vocabulary", "diagnostics", "red_flags"):
                values = entry.get(field) or []
                if values:
                    lines.append(f"{field}:")
                    lines.extend(f"  - {value}" for value in values)
            return "\n".join(lines)
    raise KeyError(f"no principle '{principle}' in taxonomy")
