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
    """Render one search hit as a citation header + the verbatim passage."""
    citation = f"{hit['principle']} §{hit['section']} p.{hit['page']}"
    return f"[{citation}] ({hit['type']})\n{hit['text']}\n"


def cmd_retrieve(args) -> None:
    """Print passages matching a design situation (``--json`` for structured output)."""
    hits = search(Path(args.db), args.query, k=args.k, principles=args.principle, types=args.type)
    if args.json:
        print(json.dumps(hits, indent=2))
    else:
        print("\n".join(_format_hit(h) for h in hits) or "(no matches)")


def cmd_build(args) -> None:
    """Build the corpus db (lazy import: build-only deps stay off the retrieve path)."""
    from .build import run_build
    run_build(chapter=args.chapter, model=args.model, db=Path(args.db),
              resume=args.resume, workers=args.workers, build_dir=Path(args.build_dir))


def cmd_eval(args) -> None:
    """Score retrieval against eval cases (lazy import)."""
    from .evalrun import run_eval
    run_eval(Path(args.db), Path(args.cases))


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
    r.set_defaults(func=cmd_retrieve)

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
    e.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
