"""Lock the query-time stdlib-only contract.

``gloss retrieve`` must run anywhere Python runs with nothing installed, so importing
the query path (``gloss.cli`` + ``gloss.store``) must not pull in any build-only
dependency. Checked in a fresh interpreter so unrelated test imports don't pollute
``sys.modules``.
"""
import subprocess
import sys

_BUILD_ONLY = ("pymupdf", "fitz", "langchain", "langchain_ollama", "pydantic", "yaml")


def test_retrieve_path_imports_no_build_deps():
    code = (
        "import gloss.cli, gloss.store, sys\n"
        f"bad = [m for m in sys.modules if m in {_BUILD_ONLY!r}]\n"
        "assert not bad, bad\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
