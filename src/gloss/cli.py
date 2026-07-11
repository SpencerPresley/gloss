"""Command-line interface for gloss.

``retrieve`` is the query-time path and depends only on the stdlib store. ``build``
and ``eval`` lazily import the build-only modules so ``retrieve`` never pulls them in.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from .store import get_unit, search


def _header(hit: dict) -> str:
    """Citation + type + channel tags, e.g. ``[deep-modules §4.6 p.45] (red_flag via lex#1+sem#1)``.

    Hybrid hits carry per-channel ranks; showing them makes reliability visible:
    ``via lex#2+sem#1`` = two independent signals agree, ``via sem#4`` = one
    channel only — read the passage with more care.
    """
    citation = f"{hit['principle']} §{hit['section']} p.{hit['page']}"
    via = ""
    if hit.get("channels"):
        via = " via " + "+".join(f"{name[:3]}#{rank}" for name, rank in hit["channels"].items())
    return f"[{citation}] ({hit['type']}{via})"


def _format_hit(hit: dict) -> str:
    """Render one search hit as a citation header + the verbatim passage."""
    return f"{_header(hit)}\n{hit['text']}\n"


def _format_preview(hit: dict) -> str:
    """Render a runner-up as one pointer line for --compact output.

    Uses the generated ``context_line`` — a paraphrase, acceptable only because it
    is labeled as a pointer, never presented as the passage. Expanding it into the
    verbatim text is ``gloss show <id>``.
    """
    line = " ".join((hit.get("context_line") or "").split())
    if len(line) > 140:
        line = line[:139].rstrip() + "…"
    return f"more: id={hit['id']} {_header(hit)} — {line}"


def cmd_retrieve(args) -> None:
    """Print passages matching a design situation (``--json`` for structured output)."""
    # A reranker needs a few candidates to choose among, even at -k 1.
    kk = max(args.k, 5) if args.rerank else args.k
    kw = dict(k=kk, principles=args.principle, types=args.type)
    if args.mode == "lexical":
        hits = search(Path(args.db), args.query, **kw)
    else:
        from .vectors import VectorsUnavailable, search_auto, search_hybrid, search_semantic
        fn = {"auto": search_auto, "hybrid": search_hybrid, "semantic": search_semantic}[args.mode]
        try:
            hits = fn(Path(args.db), args.query, base_url=args.ollama_url, **kw)
        except VectorsUnavailable as e:
            raise SystemExit(f"gloss retrieve --mode {args.mode}: {e}")
    if args.rerank:
        from .rerank import rerank
        template = Path(args.rerank_prompt).read_text() if args.rerank_prompt else None
        hits = rerank(args.query, hits, model=args.rerank_model,
                      base_url=args.ollama_url, template=template)[:args.k]
    if args.json:
        print(json.dumps(hits, indent=2))
    elif not hits:
        print("(no matches)")
    elif args.compact:
        print("\n".join([_format_hit(hits[0])] + [_format_preview(h) for h in hits[1:]]))
    else:
        print("\n".join(_format_hit(h) for h in hits))


def cmd_show(args) -> None:
    """Print one unit in full by id — expands a --compact preview line verbatim."""
    unit = get_unit(Path(args.db), args.id)
    if unit is None:
        raise SystemExit(f"gloss show: no unit with id={args.id} in {args.db}")
    out = _format_hit(unit)
    if unit.get("applies_when"):
        out += f"applies when: {unit['applies_when']}\n"
    print(out)


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


def _search_fn_for(mode: str, ollama_url: str, rerank_model: str | None = None,
                   rerank_template: str | None = None):
    """Resolve an eval retrieval mode to a score_cases-compatible search_fn.

    ``rerank_model`` wraps the mode's results in an LLM rerank (top-5 candidates
    in, top-k out) — applied to one leg only so a comparison isolates exactly
    the reranker's contribution.
    """
    if mode == "lexical":
        base = search
    else:
        from functools import partial
        from .vectors import search_hybrid, search_semantic
        base = partial(search_hybrid if mode == "hybrid" else search_semantic,
                       base_url=ollama_url)
    if not rerank_model:
        return base
    from .rerank import rerank

    def fn(db, query, k=5):
        hits = base(db, query, k=max(k, 5))
        return rerank(query, hits, model=rerank_model, base_url=ollama_url,
                      template=rerank_template)[:k]
    return fn


def cmd_eval(args) -> None:
    """Score retrieval against eval cases; --vs adds a paired significance test."""
    from .evalrun import paired_sign_flip, run_eval, score_cases
    primary_rr = args.rerank_model if args.rerank else None
    template = Path(args.rerank_prompt).read_text() if getattr(args, "rerank_prompt", None) else None
    if args.vs and (args.mode, primary_rr) == (args.vs, None):
        raise SystemExit("gloss eval: --vs compares two different configurations "
                         "(same mode needs --rerank on the primary leg)")
    if not args.vs:
        run_eval(Path(args.db), Path(args.cases), k=args.k, verbose=args.verbose,
                 search_fn=_search_fn_for(args.mode, args.ollama_url, primary_rr, template))
        return
    import yaml
    cases = yaml.safe_load(Path(args.cases).read_text())["cases"]
    results = {}
    # --rerank applies to the primary --mode leg only, so `--mode hybrid --rerank
    # --vs hybrid` isolates exactly the reranker's contribution.
    for mode, rr in ((args.mode, primary_rr), (args.vs, None)):
        label = f"{mode}+rr" if rr else mode
        results[label] = score_cases(Path(args.db), cases, k=args.k,
                                     search_fn=_search_fn_for(mode, args.ollama_url, rr, template))
        r = results[label]
        print(f"{label:10}: hit@{args.k}={r['hit_rate']:.2f} hit@1={r['hit1']:.2f} "
              f"mrr={r['mrr']:.2f} n={r['n']}")
    a, b = list(results)
    delta, p = paired_sign_flip(results[a]["ranks"], results[b]["ranks"])
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
    r.add_argument("--compact", action="store_true",
                   help="text mode only: full passage for hit #1, one 'more: id=N …' "
                        "preview line per runner-up (expand with 'gloss show'); "
                        "--json output is unaffected")
    r.add_argument("--mode", choices=["auto", "lexical", "hybrid", "semantic"], default="auto",
                   help="auto = hybrid when the db has vectors and the embedder is up, "
                        "else lexical (default); hybrid/semantic fail loudly instead of degrading")
    r.add_argument("--ollama-url", default="http://localhost:11434",
                   help="Ollama base URL for query embedding (hybrid/semantic/auto modes)")
    r.add_argument("--rerank", action="store_true",
                   help="LLM-rerank the top candidates when fusion's #1 isn't dual-backed "
                        "(one Ollama chat call); falls back to the original order on any failure")
    r.add_argument("--rerank-model", default="gemma4:e2b")
    r.add_argument("--rerank-prompt", metavar="FILE",
                   help="corpus-specific rerank prompt template file with {query}, "
                        "{candidates}, {n} placeholders (default: built-in generic)")
    r.set_defaults(func=cmd_retrieve)

    s = sub.add_parser("show", help="print one unit in full by id (expands a --compact preview)")
    s.add_argument("id", type=int)
    s.add_argument("--db", required=True)
    s.set_defaults(func=cmd_show)

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
    e.add_argument("--rerank", action="store_true",
                   help="LLM-rerank the PRIMARY --mode leg only, so `--mode hybrid --rerank "
                        "--vs hybrid` isolates the reranker's contribution")
    e.add_argument("--rerank-model", default="gemma4:e2b")
    e.add_argument("--rerank-prompt", metavar="FILE",
                   help="corpus-specific rerank prompt template file (see retrieve --rerank-prompt)")
    e.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
