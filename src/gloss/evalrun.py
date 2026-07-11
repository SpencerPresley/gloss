"""Score retrieval against eval cases (hit@k, hit@1, MRR)."""
from __future__ import annotations
from pathlib import Path

from .store import search


def _match_rank(results: list[dict], case: dict) -> int | None:
    """1-based rank of the first result satisfying the case's expectations, else None.

    A case may pin any of ``expect_section`` / ``expect_chapter`` /
    ``expect_principle``; a result matching ANY pinned field counts. Chapter and
    section pins exist because the null-principle chapters (10, 11, 14, 17-21)
    are unreachable through the principle facet.
    """
    for i, r in enumerate(results, 1):
        if case.get("expect_section") and r["section"] == case["expect_section"]:
            return i
        if case.get("expect_chapter") and r["chapter"] == case["expect_chapter"]:
            return i
        if case.get("expect_principle") and r["principle"] == case["expect_principle"]:
            return i
    return None


def score_cases(db: Path, cases: list[dict], k: int = 5, search_fn=search) -> dict:
    """Score cases; returns hit_rate (hit@k), hit1, mrr, n, and per-case ranks.

    ``hit_rate`` keeps its historical meaning — expected unit anywhere in top-k.
    ``hit1`` and ``mrr`` are rank-sensitive: they see the difference between the
    right passage at #1 and at #k, which hit_rate cannot. ``search_fn`` is
    injectable so alternate retrieval modes (and tests) can be scored.
    """
    ranks = [_match_rank(search_fn(db, c["query"], k=k), c) for c in cases]
    n = len(cases) or 1
    return {
        "hit_rate": sum(r is not None for r in ranks) / n,
        "hit1": sum(r == 1 for r in ranks) / n,
        "mrr": sum(1 / r for r in ranks if r) / n,
        "n": len(cases),
        "ranks": ranks,
    }


def paired_sign_flip(ranks_a: list[int | None], ranks_b: list[int | None],
                     resamples: int = 10_000, seed: int = 0) -> tuple[float, float]:
    """Paired randomization (sign-flip) test on per-case reciprocal ranks.

    Answers "is A actually better than B on this eval set, or is the delta the
    kind of thing coin flips produce?" — the guard against tuning knobs into
    small-n noise. A miss counts as reciprocal rank 0. Returns ``(delta, p)``:
    ``delta`` = mean(RR_A − RR_B); ``p`` = two-sided probability of a mean at
    least as extreme under the null that per-case A/B labels are exchangeable.
    """
    import random

    def rr(rank: int | None) -> float:
        return 0.0 if rank is None else 1.0 / rank

    deltas = [rr(a) - rr(b) for a, b in zip(ranks_a, ranks_b, strict=True)]
    observed = sum(deltas) / len(deltas)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(resamples):
        flipped = sum(d if rng.random() < 0.5 else -d for d in deltas) / len(deltas)
        if abs(flipped) >= abs(observed) - 1e-12:
            extreme += 1
    return observed, extreme / resamples


def run_eval(db: Path, cases_path: Path, k: int = 5, verbose: bool = False,
             search_fn=None) -> dict:
    """Load cases.yaml and print hit@k / hit@1 / MRR (plus sub-#1 cases when verbose)."""
    import yaml
    cases = yaml.safe_load(Path(cases_path).read_text())["cases"]
    result = score_cases(Path(db), cases, k=k, search_fn=search_fn or search)
    if verbose:
        for case, rank in zip(cases, result["ranks"]):
            if rank != 1:
                exp = {f: case[f] for f in ("expect_section", "expect_chapter", "expect_principle") if f in case}
                print(f"  rank={str(rank or 'miss'):>4}  {case['query']!r} expected {exp}")
    print(f"hit@{k}={result['hit_rate']:.2f} hit@1={result['hit1']:.2f} "
          f"mrr={result['mrr']:.2f} n={result['n']}")
    return result
