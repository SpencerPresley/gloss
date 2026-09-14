# Testing

The test suite pins the engine's behavior with no model and no network. Every
LLM call is replaced by an injected stub; the only optional input is the source
PDF, which gates a handful of integration tests and is skipped when absent.

## Running

```bash
uv run --extra build pytest -q
```

The `build` extra is **mandatory** even though tests need no model and no
corpus: the test modules import `pydantic`, `pymupdf` (`fitz`), `yaml`, and
`langchain` transitively. `pytest` itself comes from the `dev` dependency-group
(`pyproject.toml:22`), which `uv run` installs by default — but a sync that
prunes the build extra fails at collection with `ImportError` (e.g.
`uv run --exact pytest` → `6 errors during collection`, "ImportError while
importing test module ... test_extract.py"). Always pass `--extra build`.

No Ollama model, no running server, and no API key are required. The corpus PDF
is the only thing that can be missing, and the suite stays green without it (see
[Corpus-gated tests](#corpus-gated-tests)).

There is **no pytest config** anywhere — `pyproject.toml` has no
`[tool.pytest.ini_options]`, and there is no `pytest.ini`/`tox.ini`/`setup.cfg`.
pytest uses its defaults: it auto-discovers `tests/` via `test_*.py`. Run from
the repo root: several tests open `corpora/aposd/...` by **relative** path
(`tests/test_build.py:4`, `tests/test_eval.py:33`, `tests/test_taxonomy.py:39`,
`tests/test_segment.py:123`).

## Outcome (real run)

| Condition | Result |
| --- | --- |
| Corpus PDF present (this machine) | `59 passed` in ~8s |
| Corpus PDF absent (`APOSD_CORPUS=/nonexistent`) | `50 passed, 9 skipped` in ~3s |

59 tests total. The 5 warnings are SwigPy/`fitz` `DeprecationWarning`s from
importing pymupdf, unrelated to docq.

## File layout

| File | Tests | Covers |
| --- | --- | --- |
| `tests/conftest.py` | — | The shared `corpus_path` fixture (PDF path + skip) |
| `tests/test_parse.py` | 3 | Font classification; PDF → ordered `Element`s (headings/code/para/figure) |
| `tests/test_segment.py` | 11 | `Element`s → `RawUnit`s; code-attach + inline-span-heal rules; section text; chapter splitting/boundaries |
| `tests/test_enrich.py` | 9 | `enrich_units` checkpointing, resume, stale-row filtering, code-type forcing, failure flagging, concurrency |
| `tests/test_extract.py` | 4 | `StubExtractor`; `OllamaExtractor` method auto-discovery/pinning/override/failure |
| `tests/test_taxonomy.py` | 3 | YAML load; `principle_for_chapter`; `card_for` rendering; real-taxonomy integrity |
| `tests/test_build.py` | 8 | `load_prompt`/`load_profile`/`estimate_num_ctx`; full `run_build` via stub |
| `tests/test_store.py` | 3 | FTS5 build/search, principle filter, MATCH-query rewrite |
| `tests/test_vectors.py` | 10 | Embed/pack/chunk; mean-centering + uncentered-index back-compat; semantic match by meaning; RRF fusion + channel tags; filter pass-through; loud-fail vs auto-degrade |
| `tests/test_cli.py` | 2 | `docq retrieve` end-to-end via subprocess (JSON + no-match) |
| `tests/test_eval.py` | 5 | `score_cases` hit/miss; rank metrics (hit@1/MRR) via injected search; `paired_sign_flip` properties; `cases.yaml` well-formedness/coverage |
| `tests/test_stdlib_contract.py` | 1 | Query path imports pull in **no** build-only dependency |

`tests/__init__.py` is empty (package marker).

## The extractor stub seam

The whole pipeline depends on one protocol, `StructuredExtractor`
(`src/docq/extract.py:12`), whose only method is
`extract(prompt, schema, *, system=None) -> BaseModel`. This is the seam that
lets every test run without a model.

**`StubExtractor`** (`src/docq/extract.py:23`) is a deterministic test double:
constructed with a payload dict, its `extract` ignores the prompt and returns
`schema(**payload)`. Tests build one with a fixed enrichment payload, e.g.
`tests/test_build.py:32`.

**Injection into `run_build`.** `run_build` takes an `extractor=` parameter
(`src/docq/build.py:47-48`); when `None` it constructs an `OllamaExtractor`
(`src/docq/build.py:104-105`), otherwise it uses the one passed in. The
docstring is explicit: *"Optional pre-built StructuredExtractor (tests inject a
stub)"* (`src/docq/build.py:61`). All five `run_build` tests pass
`extractor=stub`, so no model is ever contacted — e.g. `tests/test_build.py:36`.

**Injection into `enrich_units`.** Below `run_build`, `enrich_units` takes the
extractor positionally; tests call it directly with a `StubExtractor` or a small
inline fake (`tests/test_enrich.py:27`). Failure paths use a `_Boom`/`_CapExtractor`
class whose `extract` raises (`tests/test_enrich.py:45`, `:137`).

**Injection into `OllamaExtractor` itself.** The adapter takes a
`chat_factory=` callback (`src/docq/extract.py:51`) that builds the LangChain
chat model; tests pass a `_FakeChat` so `OllamaExtractor`'s real logic
(method auto-discovery, pinning, warmup-once) is exercised with **zero**
network. `tests/test_extract.py:33` asserts it probes `json_schema` then falls
back to `function_calling` and pins it; `tests/test_enrich.py:120` uses the
factory to prove the chat is built exactly once before the worker pool starts.

## The embed_fn seam (vector channel)

`vectors.py` mirrors the extractor pattern one level down: every entry point
(`embed_corpus`, `search_semantic`, `search_hybrid`, `search_auto`) takes an
optional `embed_fn(texts) -> list[vector]`, defaulting to the real Ollama HTTP
call. `tests/test_vectors.py` injects a deterministic keyword→axis fake (cat
words on one axis, engine words on another) so cosine behaves *semantically* —
the suite asserts a query with **zero** token overlap still retrieves the right
unit, that document/query instruction prefixes are applied on the right sides,
and that fusion tags per-channel ranks. No Ollama, no network, anywhere.

## Parsing/segmentation without a PDF

Two layers, tested differently:

- **Segmentation is PDF-free.** `tests/test_segment.py` hand-builds
  `Element(...)` lists inline and feeds them to `segment`/`split_chapters`, so
  the deterministic grouping rule is tested in isolation (no PDF, no corpus) —
  `tests/test_segment.py:21-105`. The `Profile` is also built inline
  (`tests/test_segment.py:15`) to keep the engine corpus-agnostic, never
  imported from `corpora/`.
- **Parsing needs the real PDF.** Font classification (`classify_font`) is
  unit-tested with literal font strings and no file (`tests/test_parse.py:20`),
  but anything that calls `parse_pdf` requires the actual document.

## Corpus-gated tests

The `corpus_path` fixture (`tests/conftest.py:13`) resolves the APOSD PDF from
`$APOSD_CORPUS` (default
`resources/2018-john-ousterhout-a-philosophy-of-software-design_compress.pdf`)
and calls `pytest.skip(...)` when it is absent. The PDF is gitignored
(`.gitignore`: `resources/*.pdf`) — large + copyrighted — so a fresh checkout
skips these 9 tests rather than erroring. Any test taking the `corpus_path`
parameter is gated:

| Test | File:line |
| --- | --- |
| `test_parse_ch6_has_heading_code_para` | `tests/test_parse.py:28` |
| `test_parse_detects_figure` | `tests/test_parse.py:40` |
| `test_split_chapters_real_pdf_finds_all_21` | `tests/test_segment.py:119` |
| `test_last_chapter_span_excludes_back_matter` | `tests/test_segment.py:129` |
| `test_run_build_whole_book_accumulates_all_chapters` | `tests/test_build.py:28` |
| `test_run_build_single_chapter_still_works` | `tests/test_build.py:46` |
| `test_run_build_indexes_summary_appendices` | `tests/test_build.py:64` |
| `test_run_build_sets_principle_from_taxonomy_not_llm` | `tests/test_build.py:82` |
| `test_run_build_unknown_chapter_raises` | `tests/test_build.py:106` |

These are the only end-to-end parse→build tests; they use a real PDF + a
`StubExtractor`, so they validate parsing/segmentation/store wiring without a
model. To get full coverage locally, drop the PDF at the default path (see
docs/STARTUP_GUIDE.md).

## Notable invariants the suite pins

- **Verbatim text is never LLM-rewritten:** failure rows keep the original text
  (`tests/test_enrich.py:52`).
- **Coarse `principle` comes from the taxonomy, not the LLM:** a stub returning
  `"bogus_invented_slug"` is overridden — carded chapter → its slug, null
  chapter/appendix → `""` (`tests/test_build.py:82-103`).
- **Resume re-enriches prior failures without duplicate rows** (deduped by
  `key`) (`tests/test_enrich.py:127-159`); concurrent resume likewise
  (`tests/test_enrich.py:74-91`).
- **Code units force `type="code"`** regardless of the model
  (`tests/test_enrich.py:29-30`).
- **`num_ctx` floor/cap** (8192 / 32768) (`tests/test_build.py:21-25`).
- **Query path stays stdlib-only:** asserted in a *fresh interpreter*
  subprocess so unrelated test imports don't pollute `sys.modules`
  (`tests/test_stdlib_contract.py:14`); the guarded modules are pymupdf, fitz,
  langchain, langchain_ollama, pydantic, yaml — and the import set now includes
  `docq.vectors`, so the semantic channel is held to the same contract.
- **The semantic channel degrades, never blocks:** explicit hybrid mode raises
  `VectorsUnavailable` on a vector-less db; `search_auto` returns lexical
  results — silently for a vector-less db, with a stderr note when vectors
  exist but the embedder is down (`tests/test_vectors.py`).

## Coverage gaps (not tested)

- **No real LLM/Ollama path.** Every test injects a stub or `_FakeChat`. The
  live `OllamaExtractor` → `ChatOllama` path (`src/docq/extract.py:64-65`) and
  real structured-output behavior are never exercised — A/B model quality is
  evaluated operationally via `docq eval`, not in pytest (see docs/BUILDS.md).
- **`cli.py` is barely covered.** Only `docq retrieve` (default mode) is run
  end-to-end (`tests/test_cli.py`); `docq embed`, `retrieve --mode`, `docq
  build` and `docq eval` argument parsing, `--workers`/`--resume`/`--build-dir`
  flag wiring, and error/exit paths are not invoked through the CLI. (The logic
  behind the modes is covered at the `vectors.py` level.)
- **The real Ollama `/api/embed` path** (`ollama_embed`) is never exercised in
  pytest — verified operationally via `docq embed` + `docq eval --mode hybrid`.
- **`estimate_num_ctx` warning branch** prints but is not asserted; the
  cap-exceeded WARNING string is not checked.
- **`--workers` concurrency** is tested at the `enrich_units` level
  (`max_workers=4`), not through `run_build`/CLI.
- **No coverage measurement.** `pytest-cov` is not a dependency and no coverage
  threshold is configured; the numbers above are test *counts*, not line
  coverage.
- **Corpus-dependent assertions are APOSD-specific** (chapter numbers 1–21,
  page 178 back-matter boundary, fixed figure on page 21); they would not hold
  for another corpus instance.
