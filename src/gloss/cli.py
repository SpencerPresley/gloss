"""Command-line interface for gloss.

``retrieve`` is the query-time path and depends only on the stdlib store. ``build``
and ``eval`` lazily import the build-only modules so ``retrieve`` never pulls them in.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from .store import search


def _format_hit(hit: dict) -> str:
    """Render one search hit as a citation header + the verbatim passage.

    Hybrid hits carry per-channel ranks; showing them makes reliability visible:
    ``via lex#2+sem#1`` = two independent signals agree, ``via sem#4`` = one
    channel only — read the passage with more care.
    """
    citation = f"{hit['principle']} §{hit['section']} p.{hit['page']}"
    via = ""
    if hit.get("channels"):
        via = " via " + "+".join(f"{name[:3]}#{rank}" for name, rank in hit["channels"].items())
    return f"[{citation}] ({hit['type']}{via})\n{hit['text']}\n"


def cmd_retrieve(args) -> None:
    """Print passages matching a design situation (``--json`` for structured output)."""
    kw = dict(k=args.k, principles=args.principle, types=args.type)
    if args.mode == "lexical":
        hits = search(Path(args.db), args.query, **kw)
    else:
        from .vectors import VectorsUnavailable, search_auto, search_hybrid, search_semantic
        fn = {"auto": search_auto, "hybrid": search_hybrid, "semantic": search_semantic}[args.mode]
        try:
            hits = fn(Path(args.db), args.query, base_url=args.ollama_url, **kw)
        except VectorsUnavailable as e:
            raise SystemExit(f"gloss retrieve --mode {args.mode}: {e}")
    if args.json:
        print(json.dumps(hits, indent=2))
    else:
        print("\n".join(_format_hit(h) for h in hits) or "(no matches)")


def cmd_build(args) -> None:
    """Build the corpus db (lazy import: build-only deps stay off the retrieve path)."""
    from .build import run_build
    run_build(chapter=args.chapter, model=args.model, db=Path(args.db),
              resume=args.resume, workers=args.workers, build_dir=Path(args.build_dir))


def cmd_embed(args) -> None:
    """Embed every unit into the db's vectors table (needs a running Ollama)."""
    from .vectors import embed_corpus
    info = embed_corpus(Path(args.db), model=args.model, base_url=args.ollama_url,
                        batch=args.batch)
    print(f"embedded {info['units']} units -> {info['vectors']} vectors "
          f"(dim={info['dim']}, model={info['model']}) in {args.db}")


def _search_fn_for(mode: str, ollama_url: str):
    """Resolve an eval retrieval mode to a score_cases-compatible search_fn."""
    if mode == "lexical":
        return search
    from functools import partial
    from .vectors import search_hybrid, search_semantic
    return partial(search_hybrid if mode == "hybrid" else search_semantic,
                   base_url=ollama_url)


def cmd_eval(args) -> None:
    """Score retrieval against eval cases; --vs adds a paired significance test."""
    from .evalrun import paired_sign_flip, run_eval, score_cases
    if not args.vs:
        run_eval(Path(args.db), Path(args.cases), k=args.k, verbose=args.verbose,
                 search_fn=_search_fn_for(args.mode, args.ollama_url))
        return
    import yaml
    cases = yaml.safe_load(Path(args.cases).read_text())["cases"]
    results = {}
    for mode in (args.mode, args.vs):
        results[mode] = score_cases(Path(args.db), cases, k=args.k,
                                    search_fn=_search_fn_for(mode, args.ollama_url))
        r = results[mode]
        print(f"{mode:8}: hit@{args.k}={r['hit_rate']:.2f} hit@1={r['hit1']:.2f} "
              f"mrr={r['mrr']:.2f} n={r['n']}")
    delta, p = paired_sign_flip(results[args.mode]["ranks"], results[args.vs]["ranks"])
    print(f"Δmrr={delta:+.3f} p={p:.4f} "
          f"(paired sign-flip on per-case reciprocal rank, 10000 resamples)")


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``gloss`` console script."""
    parser = argparse.ArgumentParser(prog="gloss")
    sub = parser.add_subparsers(required=True)

    r = sub.add_parser("retrieve", help="retrieve passages for a design situation")
    r.add_argument("query")
    r.add_argument("--db", required=True)
    r.add_argument("-k", type=int, default=5)
    r.add_argument("--principle", action="append")
    r.add_argument("--type", action="append")
    r.add_argument("--json", action="store_true")
    r.add_argument("--mode", choices=["auto", "lexical", "hybrid", "semantic"], default="auto",
                   help="auto = hybrid when the db has vectors and the embedder is up, "
                        "else lexical (default); hybrid/semantic fail loudly instead of degrading")
    r.add_argument("--ollama-url", default="http://localhost:11434",
                   help="Ollama base URL for query embedding (hybrid/semantic/auto modes)")
    r.set_defaults(func=cmd_retrieve)

    m = sub.add_parser("embed", help="precompute unit vectors into the db (semantic channel)")
    m.add_argument("--db", required=True)
    m.add_argument("--model", default="embeddinggemma:latest")
    m.add_argument("--ollama-url", default="http://localhost:11434")
    m.add_argument("--batch", type=int, default=32, help="texts per embedding request")
    m.set_defaults(func=cmd_embed)

    b = sub.add_parser("build", help="build the corpus db from the source document")
    b.add_argument("--chapter")
    b.add_argument("--model", default="minimax-m3:cloud")
    b.add_argument("--db", required=True)
    b.add_argument("--resume", action="store_true")
    b.add_argument("--workers", type=int, default=1, help="concurrent enrichment requests")
    b.add_argument("--build-dir", default="build",
                   help="root for per-chapter JSONL checkpoints; use a distinct dir per "
                        "model (e.g. build/minimax) so builds don't clobber each other")
    b.set_defaults(func=cmd_build)

    e = sub.add_parser("eval", help="score retrieval against eval cases")
    e.add_argument("--db", required=True)
    e.add_argument("--cases", default="corpora/aposd/cases.yaml")
    e.add_argument("-k", type=int, default=5)
    e.add_argument("-v", "--verbose", action="store_true",
                   help="print every case whose expected unit is not ranked #1")
    e.add_argument("--mode", choices=["lexical", "hybrid", "semantic"], default="lexical",
                   help="retrieval mode to score (no auto: an eval must not silently degrade)")
    e.add_argument("--vs", choices=["lexical", "hybrid", "semantic"],
                   help="second mode to compare against: prints both scores plus a paired "
                        "sign-flip p-value on per-case reciprocal rank")
    e.add_argument("--ollama-url", default="http://localhost:11434")
    e.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
