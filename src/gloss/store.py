"""SQLite + FTS5 store. STDLIB ONLY — this is the query-time hot path and must run
anywhere Python runs with nothing installed. FTS5 is only an index; all data lives
in the ``units`` table.
"""
from __future__ import annotations
import re
import sqlite3
from pathlib import Path

_DDL = """
CREATE TABLE units (
  id INTEGER PRIMARY KEY,
  principle TEXT, chapter TEXT, section TEXT, type TEXT, page INTEGER,
  text TEXT, context_line TEXT, applies_when TEXT, key_terms TEXT, questions TEXT,
  enrich_model TEXT, needs_enrich INTEGER DEFAULT 0,
  CHECK (type IN ('definition','rationale','example','code','red_flag'))
);
CREATE VIRTUAL TABLE units_fts USING fts5(
  text, context_line, applies_when, key_terms, questions,
  content='units', content_rowid='id',
  tokenize="porter unicode61 tokenchars '_'"
);
CREATE TRIGGER units_ai AFTER INSERT ON units BEGIN
  INSERT INTO units_fts(rowid, text, context_line, applies_when, key_terms, questions)
  VALUES (new.id, new.text, new.context_line, new.applies_when, new.key_terms, new.questions);
END;
"""

# BM25 column weights: text, context_line, applies_when, key_terms, questions.
# Verbatim text weighted highest; generated fields moderate. Tuned later on the eval set.
_WEIGHTS = (10.0, 4.0, 5.0, 8.0, 4.0)


def to_match_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    FTS5 MATCH treats a bare string as an implicit AND of every token, so a
    natural-language query usually matches nothing. Tokenize, drop <=2-char
    tokens, and OR-join with prefix globs so any term can hit.
    """
    tokens = [t for t in re.findall(r"[A-Za-z0-9_]+", text) if len(t) > 2]
    return " OR ".join(f"{t}*" for t in tokens)


def build_db(rows: list[dict], db_path: Path) -> None:
    """(Re)build the SQLite/FTS5 store from enrichment rows. Overwrites db_path."""
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(_DDL)
        con.executemany(
            "INSERT INTO units(principle, chapter, section, type, page, text, context_line, "
            "applies_when, key_terms, questions, enrich_model, needs_enrich) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["principle"], r["chapter"], r["section"], r["type"], r["page"], r["text"],
              r["context_line"], r["applies_when"], " ".join(r["key_terms"]),
              " ".join(r["questions"]), r["enrich_model"], r.get("needs_enrich", 0))
             for r in rows],
        )
        con.execute("INSERT INTO units_fts(units_fts) VALUES ('optimize')")
        con.commit()
    finally:
        con.close()


def search(db_path: Path, query: str, k: int = 5,
           principles: list[str] | None = None, types: list[str] | None = None) -> list[dict]:
    """Return up to k units ranked by BM25, with optional metadata filters.

    Filters and ranking happen in one SQL statement. Returns [] if the query has
    no usable tokens. More-relevant rows have a more-negative bm25 score, so
    results are ordered ascending.
    """
    match = to_match_query(query)
    if not match:
        return []
    where = ["units_fts MATCH ?"]
    params: list = [match]
    if principles:
        where.append(f"u.principle IN ({','.join('?' * len(principles))})")
        params += principles
    if types:
        where.append(f"u.type IN ({','.join('?' * len(types))})")
        params += types
    sql = (f"SELECT bm25(units_fts, {', '.join(map(str, _WEIGHTS))}) AS score, u.* "
           f"FROM units_fts JOIN units u ON u.id = units_fts.rowid "
           f"WHERE {' AND '.join(where)} ORDER BY score LIMIT ?")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params + [k]).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]
