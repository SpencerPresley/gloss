from pathlib import Path

from gloss.taxonomy import load_taxonomy, principle_for_chapter, card_for

_YAML = """
principles:
  - slug: general-purpose
    name: "General-Purpose Modules are Deeper"
    vocabulary: ["somewhat general-purpose", "push complexity down"]
    diagnostics: ["What is the simplest interface that covers all current needs?"]
    red_flags: ["use-case-specific methods"]
topics:
  - {chapter: 6, title: "General-Purpose Modules are Deeper", principle: general-purpose}
  - {chapter: 10, title: "Define Errors Out Of Existence", principle: null}
"""


def test_load_and_lookup(tmp_path):
    p = tmp_path / "taxonomy.yaml"; p.write_text(_YAML)
    tax = load_taxonomy(p)
    assert principle_for_chapter(tax, "6") == "general-purpose"
    assert principle_for_chapter(tax, 6) == "general-purpose"   # int or str chapter
    assert principle_for_chapter(tax, "10") is None
    assert principle_for_chapter(tax, "99") is None


def test_card_for_renders_vocab(tmp_path):
    import pytest
    p = tmp_path / "taxonomy.yaml"; p.write_text(_YAML)
    tax = load_taxonomy(p)
    card = card_for(tax, "general-purpose")
    assert "general-purpose" in card and "push complexity down" in card and "use-case-specific methods" in card
    with pytest.raises(KeyError):
        card_for(tax, "nonexistent")


def test_real_taxonomy_topic_principles_are_known_slugs():
    from gloss.taxonomy import load_taxonomy, principle_for_chapter
    tax = load_taxonomy(Path("corpora/aposd/taxonomy.yaml"))
    slugs = {p["slug"] for p in tax["principles"]}
    for topic in tax["topics"]:
        principle = topic.get("principle")
        assert principle is None or principle in slugs, \
            f"ch{topic.get('chapter')} principle {principle!r} is not a known slug"
    # load-bearing mappings: ch6 routes to a principle, ch10 is topic-only (null)
    assert principle_for_chapter(tax, "6") == "general-purpose"
    assert principle_for_chapter(tax, "10") is None
