"""Score retrieval against eval cases (top-k hit-rate)."""
from __future__ import annotations
from pathlib import Path

from .store import search


def _matches(results: list[dict], case: dict) -> bool:
    """True if any result satisfies the case's expect_section / expect_principle."""
    exp_section = case.get("expect_section")
    exp_principle = case.get("expect_principle")
    for r in results:
        if exp_section and r["section"] == exp_section:
            return True
        if exp_principle and r["principle"] == exp_principle:
            return True
    return False


def score_cases(db: Path, cases: list[dict], k: int = 5) -> dict:
    """Return {'hit_rate', 'n'}: fraction of cases whose top-k contains the expected unit."""
    hits = sum(_matches(search(db, c["query"], k=k), c) for c in cases)
    return {"hit_rate": hits / len(cases) if cases else 0.0, "n": len(cases)}


def run_eval(db: Path, cases_path: Path) -> dict:
    """Load cases.yaml and print/return the hit-rate."""
    import yaml
    cases = yaml.safe_load(Path(cases_path).read_text())["cases"]
    result = score_cases(Path(db), cases)
    print(f"hit_rate={result['hit_rate']:.2f} over n={result['n']}")
    return result
