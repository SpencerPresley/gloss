"""Second-pass question top-up: widen each unit's retrieval-question set with
differently-angled questions. The semantic net is only as wide as the question set
(one vector per question), and a single generation pass yields 3-6 questions from
one angle family — this pass adds 4-6 more from angles the first pass didn't cover.

Operates on the build checkpoints (``units.jsonl``), never the db: for each enriched
row it appends a row copy with ``questions = old + new`` under the same key, which
supersedes the original on read-back (last-wins). A subsequent
``docq build --resume`` then ships the merged questions with zero re-enrichment;
re-embed after — a rebuild wipes the vectors table.
"""
from __future__ import annotations
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from .build import estimate_num_ctx, load_prompt
from .enrich import rows_by_key
from .extract import OllamaExtractor, StructuredExtractor
from .taxonomy import card_for, load_taxonomy, principle_for_chapter


class Questions(BaseModel):
    """Top-up retrieval questions for one already-enriched unit."""
    questions: list[str] = Field(description="4-6 NEW short questions this passage answers, each from a different angle than every existing question. No paraphrases of existing ones.")


def build_topup_prompt(row: dict, card: str, template: str) -> str:
    """Render the top-up user prompt for one checkpoint row."""
    existing = "\n".join(f"- {q}" for q in row.get("questions") or []) or "(none)"
    return template.format(card=card, passage=row["text"], questions=existing)


def pending_topups(build_dir: Path, taxonomy: dict) -> list[tuple[Path, dict, str]]:
    """(checkpoint, row, card) for every enriched row not yet topped up.

    Reads each chapter checkpoint through ``rows_by_key`` (last-wins), skipping
    failed rows (``needs_enrich=1`` — they have no questions to widen and a build
    resume should re-enrich them first) and rows whose current version already
    carries ``topup_model``. The card comes from the chapter->principle taxonomy
    mapping, like the build pass — not from the row's stored principle.
    """
    tasks: list[tuple[Path, dict, str]] = []
    for checkpoint in sorted(Path(build_dir).glob("*/units.jsonl")):
        for row in rows_by_key(checkpoint).values():
            if row.get("needs_enrich") or row.get("topup_model"):
                continue
            principle = principle_for_chapter(taxonomy, row["chapter"])
            card = card_for(taxonomy, principle) if principle else ""
            tasks.append((checkpoint, row, card))
    return tasks


def _topup_one(row: dict, card: str, extractor: StructuredExtractor, *,
               template: str, system: str, model: str, retries: int = 2) -> dict | None:
    """One unit's superseding row (original + merged questions), or None on
    persistent failure — nothing is appended then, so the next run re-attempts it.

    New questions that duplicate an existing one after whitespace/case
    normalization are dropped: the prompt forbids paraphrases, but an exact
    restatement costs a wasted vector, so it is also filtered mechanically.
    """
    prompt = build_topup_prompt(row, card, template)
    for attempt in range(retries + 1):
        try:
            new = extractor.extract(prompt, Questions, system=system).questions
            break
        except Exception:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return None
    merged = list(row.get("questions") or [])
    seen = {" ".join(q.split()).lower() for q in merged}
    for q in new:
        norm = " ".join(q.split()).lower()
        if norm and norm not in seen:
            seen.add(norm)
            merged.append(q.strip())
    return {**row, "questions": merged, "topup_model": model}


def topup_questions(build_dir: Path, taxonomy: dict, extractor: StructuredExtractor, *,
                    template: str, system: str, max_workers: int = 1) -> dict:
    """Top up every pending checkpoint row under ``build_dir``; returns counts.

    Same concurrency shape as ``enrich_units``: the first pending row runs
    serially (pinning the extractor's method and warming its client) before the
    rest go to a thread pool; appends are serialized by a lock. Rows are appended
    as they complete, so an interrupted run resumes by the ``topup_model`` marker.
    """
    model = getattr(extractor, "model", "stub")
    tasks = pending_topups(build_dir, taxonomy)
    lock = threading.Lock()
    counts = {"pending": len(tasks), "topped_up": 0, "failed": 0}

    def work(task: tuple[Path, dict, str]) -> None:
        checkpoint, row, card = task
        new_row = _topup_one(row, card, extractor, template=template, system=system, model=model)
        with lock:
            if new_row is None:
                counts["failed"] += 1
                return
            with checkpoint.open("a") as handle:
                handle.write(json.dumps(new_row) + "\n")
            counts["topped_up"] += 1

    if max_workers > 1 and tasks:
        work(tasks[0])                                    # warmup: pin method serially
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for _ in pool.map(work, tasks[1:]):
                pass
    else:
        for task in tasks:
            work(task)
    return counts


def run_topup(model: str, build_dir: Path, instance: Path,
              extractor=None, workers: int = 1) -> dict:
    """Top up a build's checkpoints end-to-end (CLI entry).

    Loads the instance's ``prompt-questions.md`` + taxonomy, sizes num_ctx from
    the actual prompts, and reminds about the shipping step: the db only picks up
    the merged questions on the next ``build --resume``, and the vectors only on
    the ``embed`` after that.
    """
    taxonomy = load_taxonomy(Path(instance) / "taxonomy.yaml")
    system, template = load_prompt(instance, "prompt-questions.md")
    tasks = pending_topups(Path(build_dir), taxonomy)
    print(f"units to top up: {len(tasks)} model={model}")
    if not tasks:
        return {"pending": 0, "topped_up": 0, "failed": 0}
    if extractor is None:
        prompts = [build_topup_prompt(row, card, template) for _, row, card in tasks]
        extractor = OllamaExtractor(model, num_ctx=estimate_num_ctx(prompts, system))
    counts = topup_questions(Path(build_dir), taxonomy, extractor, template=template,
                             system=system, max_workers=workers)
    print(f"topped up {counts['topped_up']} units ({counts['failed']} failed) in {build_dir}")
    print(f"ship it: docq build --resume --db <db> --build-dir {build_dir} "
          f"&& docq embed --db <db>")
    return counts
