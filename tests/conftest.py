"""Shared pytest fixtures for the gloss engine tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_DEFAULT_CORPUS = REPO / "resources" / "2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf"


@pytest.fixture
def corpus_path() -> Path:
    """Return the APOSD source PDF path.

    The path is the engine's input, not a hardcoded constant: override it with
    the ``APOSD_CORPUS`` environment variable. Skips the test when the corpus is
    absent so the suite stays green on machines without the (gitignored) PDF.
    """
    path = Path(os.environ.get("APOSD_CORPUS", _DEFAULT_CORPUS))
    if not path.exists():
        pytest.skip(f"corpus PDF not present at {path} (set APOSD_CORPUS to override)")
    return path
