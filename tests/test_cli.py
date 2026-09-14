import json
import subprocess
import sys

from docq.store import build_db

_ROWS = [
    {"text": "A module with a complex interface for little functionality is shallow.",
     "principle": "deep-modules", "chapter": "4", "section": "4.5", "type": "red_flag",
     "page": 45, "context_line": "module depth", "applies_when": "thin wrapper",
     "key_terms": ["shallow module"], "questions": ["too shallow?"],
     "enrich_model": "stub", "needs_enrich": 0},
    {"text": "A general-purpose changePosition method covers many UI operations.",
     "principle": "general-purpose", "chapter": "6", "section": "6.3", "type": "example",
     "page": 52, "context_line": "Ch.6 general-purpose API.", "applies_when": "special-purpose smell",
     "key_terms": ["general-purpose"], "questions": ["is this API too specialized?"],
     "enrich_model": "stub", "needs_enrich": 0},
]

# Matches both rows (module/interface/complex hit row 1, changePosition hits row 2)
# with row 1 clearly first — three matched terms vs one.
_QUERY = "module interface complex changePosition"


def test_retrieve_json(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(_ROWS, db)
    out = subprocess.run([sys.executable, "-m", "docq.cli", "retrieve", _QUERY,
                          "--db", str(db), "--json"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    hits = json.loads(out.stdout)
    assert hits and hits[0]["section"] == "4.5"


def test_retrieve_text_no_matches(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(_ROWS, db)
    out = subprocess.run([sys.executable, "-m", "docq.cli", "retrieve",
                          "xyzzy nonexistent plugh", "--db", str(db)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "(no matches)" in out.stdout


def test_retrieve_compact(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(_ROWS, db)
    out = subprocess.run([sys.executable, "-m", "docq.cli", "retrieve", _QUERY,
                          "--db", str(db), "--compact"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    # hit #1: full verbatim passage
    assert "A module with a complex interface for little functionality" in out.stdout
    # runner-up: one preview line with id + citation + context_line paraphrase...
    assert "more: id=2 [general-purpose §6.3 p.52] (example) — Ch.6 general-purpose API." in out.stdout
    # ...and NOT its verbatim text
    assert "changePosition method covers many UI operations" not in out.stdout


def test_retrieve_compact_json_unchanged(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(_ROWS, db)
    base = [sys.executable, "-m", "docq.cli", "retrieve", _QUERY, "--db", str(db), "--json"]
    plain = subprocess.run(base, capture_output=True, text=True)
    compact = subprocess.run(base + ["--compact"], capture_output=True, text=True)
    assert plain.returncode == compact.returncode == 0
    assert plain.stdout == compact.stdout   # compact is a text-mode concern only


def test_show(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(_ROWS, db)
    out = subprocess.run([sys.executable, "-m", "docq.cli", "show", "1", "--db", str(db)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "[deep-modules §4.5 p.45] (red_flag)" in out.stdout
    assert "A module with a complex interface for little functionality" in out.stdout
    assert "applies when: thin wrapper" in out.stdout


def test_show_unknown_id_errors(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(_ROWS, db)
    out = subprocess.run([sys.executable, "-m", "docq.cli", "show", "999", "--db", str(db)],
                         capture_output=True, text=True)
    assert out.returncode != 0
    assert "id=999" in out.stderr


def test_configured_db_is_used_and_cli_db_overrides_it(tmp_path):
    configured = tmp_path / "configured.db"
    override = tmp_path / "override.db"
    build_db(_ROWS[:1], configured)
    build_db(_ROWS[1:], override)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"db": str(configured)}))

    base = [sys.executable, "-m", "docq.cli", "retrieve", _QUERY,
            "--config", str(config), "--json"]
    from_config = subprocess.run(base, capture_output=True, text=True)
    from_override = subprocess.run(base + ["--db", str(override)], capture_output=True, text=True)

    assert from_config.returncode == from_override.returncode == 0
    assert json.loads(from_config.stdout)[0]["section"] == "4.5"
    assert json.loads(from_override.stdout)[0]["section"] == "6.3"
