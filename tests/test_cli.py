import json
import subprocess
import sys

from gloss.store import build_db

_ROWS = [
    {"text": "A module with a complex interface for little functionality is shallow.",
     "principle": "deep-modules", "chapter": "4", "section": "4.5", "type": "red_flag",
     "page": 45, "context_line": "module depth", "applies_when": "thin wrapper",
     "key_terms": ["shallow module"], "questions": ["too shallow?"],
     "enrich_model": "stub", "needs_enrich": 0},
]


def test_retrieve_json(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(_ROWS, db)
    out = subprocess.run([sys.executable, "-m", "gloss.cli", "retrieve",
                          "module interface complex little functionality",
                          "--db", str(db), "--json"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    hits = json.loads(out.stdout)
    assert hits and hits[0]["section"] == "4.5"


def test_retrieve_text_no_matches(tmp_path):
    db = tmp_path / "aposd.db"
    build_db(_ROWS, db)
    out = subprocess.run([sys.executable, "-m", "gloss.cli", "retrieve",
                          "xyzzy nonexistent plugh", "--db", str(db)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "(no matches)" in out.stdout
