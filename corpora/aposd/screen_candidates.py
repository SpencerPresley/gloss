#!/usr/bin/env python3
"""Leakage screen for eval-case candidates (stdlib only).

Guards the eval instrument against grading the index on its own training data:
candidate queries were drafted from unit *text* only, and this script verifies
none of them shares a word 4-gram with the stored retrieval metadata
(``questions``, ``key_terms``, ``context_line`` — plus ``applies_when``, which
the drafting firewall also covers). A metadata overlap is a hard FAIL: the
candidate must be dropped or rewritten.

Two softer checks are reported as warnings:
  - 4-gram overlap with unit ``text`` (the "no quoting the passage" rule);
  - 4-gram overlap with queries already in ``cases.yaml`` or with other
    candidates in the same file (near-duplicate guard).

An exact duplicate query within the candidates file is a hard FAIL.

Also fails on pins that don't resolve against the db (a typo'd
``expect_section`` would make a case silently unhittable).

The candidates file is parsed with a deliberately strict line parser rather
than a YAML library (keeps this stdlib-only, and enforces that the file stays
in the simple ``- query:`` / ``expect_*:`` subset that cases.yaml uses).

Usage:
    python3 corpora/aposd/screen_candidates.py --db build/minimax-v2.db \
        [--candidates corpora/aposd/cases-candidates.yaml]
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

PRINCIPLES = {"complexity", "deep-modules", "information-hiding",
              "general-purpose", "comments", "strategic-programming"}

_CASE_LINE = re.compile(r'^(\s*)- query:\s*"(.*)"\s*$')
_PIN_LINE = re.compile(r'^\s+(expect_section|expect_chapter|expect_principle):\s*"?([^"#]+?)"?\s*$')


def parse_cases(path: Path) -> list[dict]:
    """Parse the constrained cases schema; error loudly on anything else."""
    cases: list[dict] = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "cases:":
            continue
        if m := _CASE_LINE.match(line):
            cases.append({"query": m.group(2), "line": lineno})
        elif m := _PIN_LINE.match(line):
            if not cases:
                sys.exit(f"{path}:{lineno}: pin before any case")
            cases[-1][m.group(1)] = m.group(2).strip()
        else:
            sys.exit(f"{path}:{lineno}: unrecognized line (schema is "
                     f'`- query: "..."` plus expect_* pins): {line!r}')
    return cases


def ngrams(text: str, n: int = 4) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--candidates", type=Path,
                    default=Path(__file__).parent / "cases-candidates.yaml")
    ap.add_argument("--cases", type=Path,
                    default=Path(__file__).parent / "cases.yaml")
    args = ap.parse_args()

    cases = parse_cases(args.candidates)
    existing = parse_cases(args.cases) if args.cases.exists() else []

    con = sqlite3.connect(args.db)
    meta_grams: set[tuple[str, ...]] = set()
    text_grams: set[tuple[str, ...]] = set()
    for qs, kt, cl, aw, text in con.execute(
            "SELECT questions, key_terms, context_line, applies_when, text FROM units"):
        meta_grams |= ngrams(" . ".join(filter(None, (qs, kt, cl, aw))))
        text_grams |= ngrams(text or "")
    sections = {s for (s,) in con.execute("SELECT DISTINCT section FROM units")}
    chapters = {c for (c,) in con.execute("SELECT DISTINCT chapter FROM units")}
    existing_grams = set().union(*(ngrams(c["query"]) for c in existing)) if existing else set()

    fails = warns = 0
    seen_queries: dict[str, int] = {}
    seen_grams: dict[tuple[str, ...], int] = {}
    for case in cases:
        q, line = case["query"].lower(), case["line"]
        if q in seen_queries:
            fails += 1
            print(f"FAIL {args.candidates.name}:{line}: exact duplicate of "
                  f"line {seen_queries[q]} :: {case['query']!r}")
        seen_queries.setdefault(q, line)
        if dup_lines := {seen_grams[g] for g in ngrams(q) if g in seen_grams}:
            warns += 1
            print(f"WARN {args.candidates.name}:{line}: shares a 4-gram with "
                  f"candidate line(s) {sorted(dup_lines)} :: {case['query']!r}")
        for g in ngrams(q):
            seen_grams.setdefault(g, line)
    for case in cases:
        q, where = case["query"], f'{args.candidates.name}:{case["line"]}'
        grams = ngrams(q)
        if hit := grams & meta_grams:
            fails += 1
            print(f"FAIL {where}: metadata 4-gram overlap {sorted(hit)} :: {q!r}")
        if not any(k in case for k in ("expect_section", "expect_chapter", "expect_principle")):
            fails += 1
            print(f"FAIL {where}: no expect_* pin :: {q!r}")
        if (s := case.get("expect_section")) and s not in sections:
            fails += 1
            print(f"FAIL {where}: expect_section {s!r} not in db :: {q!r}")
        if (c := case.get("expect_chapter")) and c not in chapters:
            fails += 1
            print(f"FAIL {where}: expect_chapter {c!r} not in db :: {q!r}")
        if (p := case.get("expect_principle")) and p not in PRINCIPLES:
            fails += 1
            print(f"FAIL {where}: expect_principle {p!r} unknown :: {q!r}")
        if hit := grams & text_grams:
            warns += 1
            print(f"WARN {where}: quotes source text {sorted(hit)} :: {q!r}")
        if hit := grams & existing_grams:
            warns += 1
            print(f"WARN {where}: 4-gram shared with an existing case {sorted(hit)} :: {q!r}")

    print(f"\n{len(cases)} candidates screened: {fails} FAIL, {warns} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
