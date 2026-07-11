"""Vector-channel tests — no Ollama anywhere: ``embed_fn`` is injected.

The fake embedder maps keywords to axes so cosine behaves semantically:
cat-flavored text lands on one axis, engine-flavored on another, regardless of
exact wording. That lets these tests exercise the real property the channel
exists for — matching by meaning with zero shared tokens.
"""
from pathlib import Path

import pytest

from gloss.store import build_db
from gloss.vectors import (DOC_PREFIX, QUERY_PREFIX, VectorsUnavailable, _chunks,
                           _pack, _unpack, embed_corpus, search_auto,
                           search_hybrid, search_semantic)

_ROWS = [
    {"text": "Felines purr when they are content.",
     "principle": "deep-modules", "chapter": "4", "section": "4.5", "type": "rationale",
     "page": 45, "context_line": "about cats", "applies_when": "cat behavior",
     "key_terms": ["feline"], "questions": ["why does my cat purr", "is purring good"],
     "enrich_model": "stub", "needs_enrich": 0},
    {"text": "Engines convert fuel into motion.",
     "principle": "general-purpose", "chapter": "6", "section": "6.3", "type": "example",
     "page": 52, "context_line": "about engines", "applies_when": "motor design",
     "key_terms": ["engine"], "questions": ["how does an engine work"],
     "enrich_model": "stub", "needs_enrich": 0},
]

_CAT = ("cat", "feline", "purr", "kitty")
_ENGINE = ("engine", "motor", "fuel")


def _fake_embed(texts):
    out = []
    for t in texts:
        low = t.lower()
        if any(w in low for w in _CAT):
            out.append([1.0, 0.0, 0.1])
        elif any(w in low for w in _ENGINE):
            out.append([0.0, 1.0, 0.1])
        else:
            out.append([0.0, 0.0, 1.0])
    return out


def _db(tmp_path, embed=True):
    db = tmp_path / "corpus.db"
    build_db(_ROWS, db)
    if embed:
        embed_corpus(db, embed_fn=_fake_embed)
    return db


def test_pack_roundtrip_normalizes():
    vec = _unpack(_pack([3.0, 4.0]))
    assert abs(vec[0] - 0.6) < 1e-6 and abs(vec[1] - 0.8) < 1e-6


def test_chunks_split_on_line_boundaries():
    lines = ["a" * 2500, "b" * 2500, "c" * 2500]
    chunks = _chunks("\n".join(lines), limit=6000)
    assert len(chunks) == 2
    assert "\n".join(chunks) == "\n".join(lines)          # nothing lost
    assert all(len(c) <= 6000 for c in chunks)


def test_embed_corpus_counts_and_prefixes(tmp_path):
    seen = []

    def spy(texts):
        seen.extend(texts)
        return _fake_embed(texts)

    db = tmp_path / "corpus.db"
    build_db(_ROWS, db)
    info = embed_corpus(db, embed_fn=spy)
    # unit 1: gist + 2 questions + 1 text chunk; unit 2: gist + 1 question + 1 text chunk
    assert info == {"units": 2, "vectors": 7, "dim": 3, "model": "embeddinggemma:latest"}
    assert seen and all(t.startswith(DOC_PREFIX) for t in seen)


def test_semantic_matches_by_meaning_not_tokens(tmp_path):
    db = _db(tmp_path)
    seen = []

    def spy(texts):
        seen.extend(texts)
        return _fake_embed(texts)

    # zero token overlap with the feline unit's text/metadata — only meaning
    hits = search_semantic(db, "my kitty seems happy", k=1, embed_fn=spy)
    assert hits[0]["section"] == "4.5"
    assert hits[0]["channels"] == {"semantic": 1}
    assert all(t.startswith(QUERY_PREFIX) for t in seen)   # query-side prefix applied


def test_hybrid_fuses_and_tags_agreement(tmp_path):
    db = _db(tmp_path)
    # 'engines'/'fuel' hit lexically AND semantically -> both channels tagged #1
    hits = search_hybrid(db, "engines convert fuel", k=2, embed_fn=_fake_embed)
    assert hits[0]["section"] == "6.3"
    assert hits[0]["channels"]["lexical"] == 1 and hits[0]["channels"]["semantic"] == 1
    # semantic-only rescue: no token of this query appears in the feline unit
    hits = search_hybrid(db, "kitty", k=1, embed_fn=_fake_embed)
    assert hits[0]["section"] == "4.5"
    assert hits[0]["channels"] == {"semantic": 1}
    assert hits[0]["score"] is None                        # no bm25 score for a lexical miss


def test_hybrid_respects_filters(tmp_path):
    db = _db(tmp_path)
    hits = search_hybrid(db, "kitty", k=5, principles=["general-purpose"], embed_fn=_fake_embed)
    assert all(h["principle"] == "general-purpose" for h in hits)


def test_embed_centers_vectors_and_search_survives(tmp_path):
    """Doc vectors are stored mean-centered + renormalized, the corpus mean is
    recorded, and (because the query gets the identical correction) matching by
    meaning still works."""
    import math
    import sqlite3

    db = _db(tmp_path)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    meta = con.execute("SELECT * FROM vectors_meta").fetchone()
    mean = _unpack(meta["center_vec"])
    blobs = [_unpack(r["vec"]) for r in con.execute("SELECT vec FROM vectors")]
    con.close()
    assert any(abs(x) > 1e-6 for x in mean)                 # nonzero corpus mean recorded
    for v in blobs:
        assert abs(math.sqrt(math.sumprod(v, v)) - 1.0) < 1e-3   # renormalized
    # centered mean of stored vectors is ~0 in every dimension
    resid = [sum(v[i] for v in blobs) / len(blobs) for i in range(len(mean))]
    assert all(abs(x) < 0.35 for x in resid)                # common direction removed
    hits = search_semantic(db, "my kitty seems happy", k=1, embed_fn=_fake_embed)
    assert hits[0]["section"] == "4.5"


def test_uncentered_index_still_queries(tmp_path):
    """A pre-centering index (center_vec NULL) keeps working: the query is simply
    not centered, no crash."""
    import sqlite3

    db = _db(tmp_path)
    con = sqlite3.connect(db)
    con.execute("UPDATE vectors_meta SET center_vec = NULL")
    con.commit()
    con.close()
    hits = search_semantic(db, "my kitty seems happy", k=1, embed_fn=_fake_embed)
    assert hits and hits[0]["section"] == "4.5"


def test_hybrid_without_vectors_raises_auto_degrades(tmp_path, capsys):
    db = _db(tmp_path, embed=False)
    with pytest.raises(VectorsUnavailable):
        search_hybrid(db, "engines convert fuel", embed_fn=_fake_embed)
    hits = search_auto(db, "engines convert fuel", k=1, embed_fn=_fake_embed)
    assert hits and hits[0]["section"] == "6.3"            # lexical still answers
    assert capsys.readouterr().err == ""                   # vector-less db: silent, expected state


def test_auto_warns_when_embedder_down(tmp_path, capsys):
    db = _db(tmp_path)

    def down(texts):
        raise OSError("connection refused")

    hits = search_auto(db, "engines convert fuel", k=1, embed_fn=down)
    assert hits and hits[0]["section"] == "6.3"            # degraded to lexical
    assert "semantic channel off" in capsys.readouterr().err
