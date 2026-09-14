"""Command-line interface for docq.

``retrieve`` is the query-time path and depends only on the stdlib store. ``build``
and ``eval`` lazily import the build-only modules so ``retrieve`` never pulls them in.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from .config import ConfigError, LoadedConfig, load_config
from .rerank import OllamaUnavailable
from .store import get_unit, search


_DEFAULTS = {
    "db": None,
    "ollama-url": "http://localhost:11434",
    "retrieve": {
        "k": 5,
        "mode": "auto",
        "rerank": False,
        "rerank-model": "gemma4:e2b",
        "rerank-prompt": None,
    },
    "embed": {"model": "embeddinggemma:latest", "batch": 32},
    "build": {
        "instance": None,
        "model": "minimax-m3:cloud",
        "workers": 1,
        "build-dir": "build",
    },
    "enrich-questions": {
        "instance": None,
        "model": "minimax-m3:cloud",
        "workers": 1,
        "build-dir": None,
    },
    "eval": {
        "cases": None,
        "k": 5,
        "mode": "lexical",
        "vs": None,
        "rerank": False,
        "rerank-model": "gemma4:e2b",
        "rerank-prompt": None,
    },
}


def _merge(target: dict, incoming: dict) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _effective(values: dict) -> dict:
    result = deepcopy(_DEFAULTS)
    _merge(result, values)
    return result


def _extract_config(argv: list[str]) -> tuple[list[str], str | None]:
    """Remove one --config option wherever it appears in argv."""
    clean: list[str] = []
    explicit = None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--config":
            if explicit is not None:
                raise ConfigError("--config may only be passed once")
            if index + 1 == len(argv):
                raise ConfigError("--config requires a path")
            explicit = argv[index + 1]
            index += 2
            continue
        if item.startswith("--config="):
            if explicit is not None:
                raise ConfigError("--config may only be passed once")
            explicit = item.split("=", 1)[1]
            if not explicit:
                raise ConfigError("--config requires a path")
            index += 1
            continue
        clean.append(item)
        index += 1
    return clean, explicit


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
    verbatim text is ``docq show <id>``.
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
            raise SystemExit(f"docq retrieve --mode {args.mode}: {e}")
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
        raise SystemExit(f"docq show: no unit with id={args.id} in {args.db}")
    out = _format_hit(unit)
    if unit.get("applies_when"):
        out += f"applies when: {unit['applies_when']}\n"
    print(out)


def cmd_build(args) -> None:
    """Build the corpus db (lazy import: build-only deps stay off the retrieve path)."""
    from .build import run_build
    run_build(chapter=args.chapter, model=args.model, db=Path(args.db),
              resume=args.resume, instance=Path(args.instance), workers=args.workers,
              build_dir=Path(args.build_dir))


def cmd_enrich_questions(args) -> None:
    """Top up checkpoint questions (lazy import: build-only deps stay off the retrieve path)."""
    from .topup import run_topup
    run_topup(model=args.model, build_dir=Path(args.build_dir), instance=Path(args.instance),
              workers=args.workers)


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
        raise SystemExit("docq eval: --vs compares two different configurations "
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


def cmd_config(args) -> None:
    """Print effective values and the files considered to produce them."""
    print(json.dumps({
        "sources": [str(path) for path in args.loaded_config.sources],
        "candidates": [str(path) for path in args.loaded_config.candidates],
        "config": args.effective_config,
    }, indent=2))


def _require_configured(parser: argparse.ArgumentParser, args, loaded: LoadedConfig) -> None:
    requirements = {
        "retrieve": (("db", "database", "--db"),),
        "show": (("db", "database", "--db"),),
        "embed": (("db", "database", "--db"),),
        "build": (
            ("db", "database", "--db"),
            ("instance", "corpus instance", "--instance"),
        ),
        "enrich-questions": (
            ("build_dir", "build directory", "--build-dir"),
            ("instance", "corpus instance", "--instance"),
        ),
        "eval": (
            ("db", "database", "--db"),
            ("cases", "evaluation cases", "--cases"),
        ),
    }
    for attribute, label, option in requirements.get(args.command, ()):
        if getattr(args, attribute) is None:
            inspected = ", ".join(str(path) for path in loaded.candidates)
            parser.error(
                f"{args.command}: no {label} configured; pass {option} or set it in "
                f"a config file (inspected: {inspected})"
            )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``docq`` console script."""
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        clean, explicit = _extract_config(raw)
        loaded = load_config(explicit=explicit)
    except ConfigError as error:
        raise SystemExit(f"docq: configuration error: {error}") from None
    config = _effective(loaded.values)

    parser = argparse.ArgumentParser(prog="docq")
    parser.add_argument("--config", metavar="FILE",
                        help="use only FILE plus CLI flags (accepted before or after the command)")
    sub = parser.add_subparsers(required=True, dest="command")

    c = sub.add_parser("config", help="print loaded files and effective configuration")
    c.set_defaults(func=cmd_config)

    r = sub.add_parser("retrieve", help="retrieve passages for a design situation")
    r.add_argument("query")
    r.add_argument("--db", default=config["db"])
    r.add_argument("-k", type=int, default=config["retrieve"]["k"])
    r.add_argument("--principle", action="append")
    r.add_argument("--type", action="append")
    r.add_argument("--json", action="store_true")
    r.add_argument("--compact", action="store_true",
                   help="text mode only: full passage for hit #1, one 'more: id=N …' "
                        "preview line per runner-up (expand with 'docq show'); "
                        "--json output is unaffected")
    r.add_argument("--mode", choices=["auto", "lexical", "hybrid", "semantic"],
                   default=config["retrieve"]["mode"],
                   help="auto = hybrid when the db has vectors and the embedder is up, "
                        "else lexical (default); hybrid/semantic fail loudly instead of degrading")
    r.add_argument("--ollama-url", default=config["ollama-url"],
                   help="Ollama base URL for query embedding (hybrid/semantic/auto modes)")
    r.add_argument("--rerank", action=argparse.BooleanOptionalAction,
                   default=config["retrieve"]["rerank"],
                   help="LLM-rerank the top candidates when fusion's #1 isn't dual-backed "
                        "(one Ollama chat call); transport/setup failures are errors")
    r.add_argument("--rerank-model", default=config["retrieve"]["rerank-model"])
    r.add_argument("--rerank-prompt", metavar="FILE",
                   default=config["retrieve"]["rerank-prompt"],
                   help="corpus-specific rerank prompt template file with {query}, "
                        "{candidates}, {n} placeholders (default: built-in generic)")
    r.set_defaults(func=cmd_retrieve)

    s = sub.add_parser("show", help="print one unit in full by id (expands a --compact preview)")
    s.add_argument("id", type=int)
    s.add_argument("--db", default=config["db"])
    s.set_defaults(func=cmd_show)

    m = sub.add_parser("embed", help="precompute unit vectors into the db (semantic channel)")
    m.add_argument("--db", default=config["db"])
    m.add_argument("--model", default=config["embed"]["model"])
    m.add_argument("--ollama-url", default=config["ollama-url"])
    m.add_argument("--batch", type=int, default=config["embed"]["batch"],
                   help="texts per embedding request")
    m.set_defaults(func=cmd_embed)

    b = sub.add_parser("build", help="build the corpus db from the source document")
    b.add_argument("--chapter")
    b.add_argument("--model", default=config["build"]["model"])
    b.add_argument("--db", default=config["db"])
    b.add_argument("--instance", default=config["build"]["instance"],
                   help="corpus profile/taxonomy/prompt directory")
    b.add_argument("--resume", action="store_true")
    b.add_argument("--workers", type=int, default=config["build"]["workers"],
                   help="concurrent enrichment requests")
    b.add_argument("--build-dir", default=config["build"]["build-dir"],
                   help="root for per-chapter JSONL checkpoints; use a distinct dir per "
                        "model (e.g. build/minimax) so builds don't clobber each other")
    b.set_defaults(func=cmd_build)

    q = sub.add_parser("enrich-questions",
                       help="append differently-angled retrieval questions to every enriched "
                            "checkpoint row (widens the semantic net; ship with a build "
                            "--resume + embed)")
    q.add_argument("--build-dir", default=config["enrich-questions"]["build-dir"],
                   help="checkpoint root of the build to top up (e.g. build/minimax-v2)")
    q.add_argument("--instance", default=config["enrich-questions"]["instance"],
                   help="corpus profile/taxonomy/prompt directory")
    q.add_argument("--model", default=config["enrich-questions"]["model"])
    q.add_argument("--workers", type=int, default=config["enrich-questions"]["workers"],
                   help="concurrent top-up requests")
    q.set_defaults(func=cmd_enrich_questions)

    e = sub.add_parser("eval", help="score retrieval against eval cases")
    e.add_argument("--db", default=config["db"])
    e.add_argument("--cases", default=config["eval"]["cases"])
    e.add_argument("-k", type=int, default=config["eval"]["k"])
    e.add_argument("-v", "--verbose", action="store_true",
                   help="print every case whose expected unit is not ranked #1")
    e.add_argument("--mode", choices=["lexical", "hybrid", "semantic"],
                   default=config["eval"]["mode"],
                   help="retrieval mode to score (no auto: an eval must not silently degrade)")
    e.add_argument("--vs", choices=["lexical", "hybrid", "semantic"],
                   default=config["eval"]["vs"],
                   help="second mode to compare against: prints both scores plus a paired "
                        "sign-flip p-value on per-case reciprocal rank")
    e.add_argument("--ollama-url", default=config["ollama-url"])
    e.add_argument("--rerank", action=argparse.BooleanOptionalAction,
                   default=config["eval"]["rerank"],
                   help="LLM-rerank the PRIMARY --mode leg only, so `--mode hybrid --rerank "
                        "--vs hybrid` isolates the reranker's contribution")
    e.add_argument("--rerank-model", default=config["eval"]["rerank-model"])
    e.add_argument("--rerank-prompt", metavar="FILE",
                   default=config["eval"]["rerank-prompt"],
                   help="corpus-specific rerank prompt template file (see retrieve --rerank-prompt)")
    e.set_defaults(func=cmd_eval)

    args = parser.parse_args(clean)
    args.loaded_config = loaded
    args.effective_config = config
    _require_configured(parser, args, loaded)
    try:
        args.func(args)
    except OllamaUnavailable as error:
        parser.exit(1, f"docq: rerank failed: {error}\n")


if __name__ == "__main__":
    main()
