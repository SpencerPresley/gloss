"""Optional semantic channel: precomputed unit vectors stored in the same ``.db``,
query-time cosine scoring, and BM25+vector hybrid fusion. STDLIB ONLY — this is
on the query path. The one extra requirement is a *service*, not a package: a
local Ollama serving the embedding model, needed once to embed the corpus and
then once per query. Lexical retrieval (``store.search``) never imports this
module, so the zero-install path is unchanged; ``search_auto`` degrades back to
it when the semantic channel can't run.

EmbeddingGemma is prompt-tuned: documents and queries take different instruction
prefixes. They are applied here on both sides and recorded in ``vectors_meta``
so a query is always embedded the same way its corpus was. The corpus common
direction is removed at embed time (mean-center + renormalize, "all-but-the-top")
and the mean stored, so queries get the identical correction.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from array import array
from pathlib import Path

from .store import search

DEFAULT_BASE_URL = "http://localhost:11434"
DOC_PREFIX = "title: none | text: "
QUERY_PREFIX = "task: search result | query: "
_CHUNK_CHARS = 6000   # embeddinggemma's context is 2048 tokens (~8K chars); keep margin

_DDL = """
CREATE TABLE IF NOT EXISTS vectors (
  unit_id INTEGER NOT NULL,
  kind TEXT NOT NULL,           -- 'gist' | 'question' | 'text'
  seq INTEGER NOT NULL,         -- question index / text-chunk index within kind
  vec BLOB NOT NULL,            -- float32 (native-endian; every target is little-endian), unit-normalized
  PRIMARY KEY (unit_id, kind, seq)
);
CREATE TABLE IF NOT EXISTS vectors_meta (
  model TEXT NOT NULL, dim INTEGER NOT NULL,
  doc_prefix TEXT NOT NULL, query_prefix TEXT NOT NULL,
  center_vec BLOB,              -- corpus mean removed from every vector; NULL = uncentered index
  created TEXT NOT NULL
);
"""


class VectorsUnavailable(RuntimeError):
    """Semantic channel can't run: no vectors in the db, or no reachable embedder."""


def ollama_embed(texts: list[str], model: str, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 120.0) -> list[list[float]]:
    """Embed texts via Ollama's ``/api/embed``. Raises urllib errors as-is."""
    body = json.dumps({"model": model, "input": texts}).encode()
    req = urllib.request.Request(f"{base_url}/api/embed", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["embeddings"]


def _pack(vec) -> bytes:
    """float32-pack a vector, unit-normalizing if the embedder didn't."""
    a = array("f", vec)
    n = math.sqrt(math.sumprod(a, a)) or 1.0
    if abs(n - 1.0) > 1e-3:
        a = array("f", (x / n for x in a))
    return a.tobytes()


def _unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def _chunks(text: str, limit: int = _CHUNK_CHARS) -> list[str]:
    """Split text into <=limit-char windows at line boundaries (a single line
    longer than limit stays whole; the embedder truncates it)."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.split("\n"):
        if buf and size + len(line) > limit:
            out.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        out.append("\n".join(buf))
    return out


def _unit_jobs(row) -> list[tuple[str, int, str]]:
    """(kind, seq, text) embedding inputs for one unit.

    Three views per unit: a *gist* (the generated context/situation/terms — the
    situation-matching surface), one vector per generated *question* (dense
    doc2query: a query matches a question by meaning, not by shared words), and
    the verbatim *text* (catches concepts the enrichment didn't anticipate),
    chunked to fit the embedder's context.
    """
    jobs: list[tuple[str, int, str]] = []
    gist = " ".join(x for x in (row["context_line"], row["applies_when"], row["key_terms"]) if x)
    if gist.strip():
        jobs.append(("gist", 0, gist))
    questions = [q for q in (row["questions"] or "").split("\n") if q.strip()]
    jobs += [("question", i, q) for i, q in enumerate(questions)]
    jobs += [("text", j, c) for j, c in enumerate(_chunks(row["text"]))]
    return jobs


def embed_corpus(db_path: Path, model: str = "embeddinggemma:latest",
                 base_url: str = DEFAULT_BASE_URL, embed_fn=None, batch: int = 32) -> dict:
    """Compute and store vectors for every unit, replacing any prior vectors.

    A pure post-pass over an already-built db: reads only the ``units`` table,
    writes only ``vectors`` / ``vectors_meta``. Embedder errors propagate loudly
    — an embed *command* must not silently degrade the way queries do.
    """
    embed_fn = embed_fn or (lambda texts: ollama_embed(texts, model, base_url))
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT id, text, context_line, applies_when, key_terms, "
                           "questions FROM units").fetchall()
        jobs = [(r["id"], kind, seq, doc) for r in rows for kind, seq, doc in _unit_jobs(r)]
        embs: list[array] = []
        for start in range(0, len(jobs), batch):
            part = jobs[start:start + batch]
            for emb in embed_fn([DOC_PREFIX + doc for _, _, _, doc in part]):
                embs.append(_unpack(_pack(emb)))
        dim = len(embs[0]) if embs else 0
        # Remove the corpus common direction ("all-but-the-top", k=1). One book plus a
        # shared instruction prefix give every vector a large shared component (measured
        # on APOSD: mean-vector norm 0.67, mean pairwise doc-doc cosine 0.45) that rides
        # on every similarity and compresses contrast; centering + renormalizing restores
        # it (hit@1 0.61 -> 0.71 on the eval). The mean is stored so queries get the
        # identical treatment.
        mean = [sum(col) / len(embs) for col in zip(*embs)] if embs else []
        con.execute("DROP TABLE IF EXISTS vectors")        # drop, don't DELETE: migrates
        con.execute("DROP TABLE IF EXISTS vectors_meta")   # older vectors_meta schemas
        con.executescript(_DDL)
        for (uid, kind, seq, _), emb in zip(jobs, embs):
            centered = _pack([x - m for x, m in zip(emb, mean)])
            con.execute("INSERT INTO vectors VALUES (?,?,?,?)", (uid, kind, seq, centered))
        con.execute("INSERT INTO vectors_meta VALUES (?,?,?,?,?,?)",
                    (model, dim, DOC_PREFIX, QUERY_PREFIX, array("f", mean).tobytes(),
                     time.strftime("%Y-%m-%dT%H:%M:%S")))
        con.commit()
        return {"units": len(rows), "vectors": len(jobs), "dim": dim, "model": model}
    finally:
        con.close()


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _has_vectors(con: sqlite3.Connection) -> bool:
    row = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vectors_meta'").fetchone()
    return bool(row and con.execute("SELECT 1 FROM vectors_meta LIMIT 1").fetchone())


def _query_vector(con: sqlite3.Connection, query: str, base_url: str, embed_fn) -> array:
    """Embed the query with the same model, prefix, and centering the corpus used."""
    if not _has_vectors(con):
        raise VectorsUnavailable("db has no vectors — run: gloss embed --db <db>")
    meta = con.execute("SELECT * FROM vectors_meta").fetchone()
    fn = embed_fn or (lambda texts: ollama_embed(texts, meta["model"], base_url))
    try:
        emb = fn([meta["query_prefix"] + query])[0]
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise VectorsUnavailable(f"embedder unreachable at {base_url} ({e})") from e
    q = _unpack(_pack(emb))
    center = meta["center_vec"] if "center_vec" in meta.keys() else None
    if center:   # None/empty = uncentered index (pre-centering embed) — leave q as-is
        mean = _unpack(center)
        q = _unpack(_pack([x - m for x, m in zip(q, mean)]))
    return q


def _semantic_scores(con: sqlite3.Connection, qvec: array,
                     principles: list[str] | None, types: list[str] | None) -> dict[int, float]:
    """Max cosine per unit over all its vectors (metadata filters applied in SQL)."""
    where, params = [], []
    if principles:
        where.append(f"u.principle IN ({','.join('?' * len(principles))})")
        params += principles
    if types:
        where.append(f"u.type IN ({','.join('?' * len(types))})")
        params += types
    sql = "SELECT v.unit_id, v.vec FROM vectors v JOIN units u ON u.id = v.unit_id"
    if where:
        sql += " WHERE " + " AND ".join(where)
    scores: dict[int, float] = {}
    for uid, blob in con.execute(sql, params):
        s = math.sumprod(qvec, _unpack(blob))
        if s > scores.get(uid, -2.0):
            scores[uid] = s
    return scores


def _hydrate(con: sqlite3.Connection, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    rows = con.execute(f"SELECT * FROM units WHERE id IN ({','.join('?' * len(ids))})", ids)
    got = {r["id"]: dict(r) for r in rows}
    return [got[i] for i in ids if i in got]


def search_semantic(db_path: Path, query: str, k: int = 5,
                    principles: list[str] | None = None, types: list[str] | None = None, *,
                    base_url: str = DEFAULT_BASE_URL, embed_fn=None) -> list[dict]:
    """Rank units purely by vector max-similarity (rows shaped like store.search,
    plus ``sem_score``). Mostly an ablation/debugging mode; prefer hybrid."""
    con = _connect(db_path)
    try:
        qvec = _query_vector(con, query, base_url, embed_fn)
        scores = _semantic_scores(con, qvec, principles, types)
        top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
        rows = _hydrate(con, [uid for uid, _ in top])
    finally:
        con.close()
    for i, ((_, s), row) in enumerate(zip(top, rows), 1):
        row["sem_score"] = round(s, 5)
        row["channels"] = {"semantic": i}
    return rows


def search_hybrid(db_path: Path, query: str, k: int = 5,
                  principles: list[str] | None = None, types: list[str] | None = None, *,
                  base_url: str = DEFAULT_BASE_URL, embed_fn=None,
                  pool: int = 50, rrf_k: int = 20) -> list[dict]:
    """Two-channel retrieval fused with reciprocal rank fusion.

    The channels' errors are decorrelated — BM25 misses vocabulary mismatches,
    vectors miss terse exact-term queries — so RRF (1/(rrf_k + rank), summed
    across channels) promotes units both channels like without having to
    calibrate bm25() against cosine. Each returned row carries
    ``channels: {'lexical': rank, 'semantic': rank}`` (absent key = that channel
    didn't rank it in its top ``pool``): both keys present means two independent
    signals agree — the caller can *see* how trustworthy a hit is.

    ``rrf_k=20`` rather than the literature's 60: that constant was sized for
    web-scale ranked lists. On a one-book corpus (~200 units, pool 50) rank
    tails are noise, and 60 over-smooths — a query full of common words gives
    dozens of units mediocre dual-channel ranks that sum past one channel's
    emphatic #1 (observed: ch11's whole-chapter unit at semantic #1 with a
    0.69-vs-0.59 score cliff, diluted out of hybrid top-5 at rrf_k=60).

    Raises VectorsUnavailable when the semantic channel can't run (no vectors,
    embedder down); use ``search_auto`` to degrade to lexical instead.
    """
    pool = max(pool, k)
    lex = search(db_path, query, k=pool, principles=principles, types=types)
    lex_rank = {r["id"]: i for i, r in enumerate(lex, 1)}
    con = _connect(db_path)
    try:
        qvec = _query_vector(con, query, base_url, embed_fn)
        sem_scores = _semantic_scores(con, qvec, principles, types)
        sem_top = sorted(sem_scores.items(), key=lambda kv: (-kv[1], kv[0]))[:pool]
        sem_rank = {uid: i for i, (uid, _) in enumerate(sem_top, 1)}
        fused: dict[int, float] = {}
        for uid, rank in lex_rank.items():
            fused[uid] = fused.get(uid, 0.0) + 1.0 / (rrf_k + rank)
        for uid, rank in sem_rank.items():
            fused[uid] = fused.get(uid, 0.0) + 1.0 / (rrf_k + rank)
        order = sorted(fused, key=lambda uid: (-fused[uid], lex_rank.get(uid, pool + 1), uid))[:k]
        by_id = {r["id"]: r for r in lex}
        for row in _hydrate(con, [uid for uid in order if uid not in by_id]):
            by_id[row["id"]] = row
    finally:
        con.close()
    out = []
    for uid in order:
        row = dict(by_id[uid])
        row.setdefault("score", None)   # bm25 score; absent for semantic-only hits
        row["channels"] = {name: rank for name, rank in
                           (("lexical", lex_rank.get(uid)), ("semantic", sem_rank.get(uid))) if rank}
        row["rrf"] = round(fused[uid], 5)
        out.append(row)
    return out


def search_auto(db_path: Path, query: str, k: int = 5,
                principles: list[str] | None = None, types: list[str] | None = None, *,
                base_url: str = DEFAULT_BASE_URL, embed_fn=None) -> list[dict]:
    """Hybrid when the db has vectors, silently lexical when it doesn't (the
    normal state of a lexical-only corpus), lexical with a stderr note when
    vectors exist but the embedder is unreachable (a degraded state worth
    flagging)."""
    con = _connect(db_path)
    try:
        has = _has_vectors(con)
    finally:
        con.close()
    if not has:
        return search(db_path, query, k=k, principles=principles, types=types)
    try:
        return search_hybrid(db_path, query, k=k, principles=principles, types=types,
                             base_url=base_url, embed_fn=embed_fn)
    except VectorsUnavailable as e:
        print(f"gloss: semantic channel off ({e}); lexical only", file=sys.stderr)
        return search(db_path, query, k=k, principles=principles, types=types)
