"""Optional LLM rerank of retrieved candidates. STDLIB ONLY — query path.

Rank fusion (vectors.py) never reads a passage; a reranker does, so it can
separate surface-similar distractors from the passage that actually answers the
situation. Opt-in (``--rerank``): one local-Ollama chat call per query is fine
inside an agent workflow, unacceptable as a silent default.

Contract: reranking may only *reorder* — on any failure (embedder down, model
missing, malformed reply, non-permutation) the original hits are returned
unchanged with a stderr note. Retrieval never breaks because a reranker had a
bad day.
"""
from __future__ import annotations

import json
import sys
import urllib.request

DEFAULT_MODEL = "gemma4:e2b"
DEFAULT_BASE_URL = "http://localhost:11434"


def ollama_chat(prompt: str, model: str, base_url: str = DEFAULT_BASE_URL,
                timeout: float = 120.0) -> str:
    """One non-streaming chat completion via Ollama's ``/api/chat``, temperature 0."""
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "stream": False, "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(f"{base_url}/api/chat", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["message"]["content"]


# Corpus-agnostic default. Override per corpus with a template file (--rerank-prompt)
# containing {query}, {candidates}, and {n} placeholders — a corpus-tuned instruction
# ("this is a software-design book; a 'situation' is a code smell…") can outperform
# the generic one. The trailing keep-given-order clause is load-bearing: it damps
# rerankers from churning near-ties.
_DEFAULT_TEMPLATE = """\
You are reranking search results retrieved from a reference text.
A reader describes their situation: {query!r}

Candidate passages:
{candidates}

Which passage most directly addresses the reader's situation — the one you would
show them first? Judge by what each passage is actually about, not by words it
happens to share with the situation. If you are unsure between two passages,
keep them in their given order.
Reply with ONLY a JSON array of all {n} ids, best first. No other text."""


def _prompt(query: str, hits: list[dict], keep_text: int, template: str | None = None) -> str:
    lines = []
    for h in hits:
        head = " ".join(h["text"][:keep_text].split())
        lines.append(f"- id={h['id']} [{h['principle']} §{h['section']}] ({h['type']})")
        lines.append(f"  gist: {h['context_line']}")
        lines.append(f"  text: {head}")
    return (template or _DEFAULT_TEMPLATE).format(
        query=query, candidates="\n".join(lines), n=len(hits))


def _parse_ids(reply: str) -> list[int]:
    """Extract the id array; tolerate code fences or stray prose around it."""
    start, end = reply.find("["), reply.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array in reply")
    ids = json.loads(reply[start:end + 1])
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise ValueError("array is not a flat list of ints")
    return ids


def _normalize(raw: list[int], candidates: list[int]) -> list[int] | None:
    """Salvage a ranking from an imperfect reply.

    Small local models routinely return a *subset* — their top picks, dropping
    candidates they judged irrelevant despite the include-everything
    instruction (observed with gemma4:e2b). A subset is still a ranking
    signal: keep known ids (first occurrence wins), ignore invented ones, and
    append unmentioned candidates in their original fused order. Returns None
    when the reply mentions no candidate at all — that's a failure, not a
    ranking.
    """
    kept: list[int] = []
    cand = set(candidates)
    for i in raw:
        if i in cand and i not in kept:
            kept.append(i)
    if not kept:
        return None
    return kept + [c for c in candidates if c not in kept]


def fusion_trusts_top1(hits: list[dict]) -> bool:
    """True when fusion's #1 is dual-backed: lexical #1 AND semantic top-5.

    Measured (2026-07-10, n=31): every reranker tried — local 2B and cloud —
    systematically demoted exactly the #1s carrying this signature while its
    promotions came from weakly-backed #1s. Two independent channels agreeing
    is stronger evidence than one model's read, so a dual-backed #1 is kept and
    the reranker only judges the uncertain cases. (Policy selected on the
    31-case set — revalidate when the eval expands.)
    """
    ch = hits[0].get("channels") or {} if hits else {}
    sem = ch.get("semantic")
    return ch.get("lexical") == 1 and sem is not None and sem <= 5


def rerank(query: str, hits: list[dict], *, model: str = DEFAULT_MODEL,
           base_url: str = DEFAULT_BASE_URL, keep_text: int = 600,
           template: str | None = None, gate: bool = True, chat_fn=None) -> list[dict]:
    """Reorder hits by LLM judgment of which passage answers the situation.

    ``gate=True`` (default) skips the model call entirely when fusion's #1 is
    dual-backed (``fusion_trusts_top1``) — cheaper and measurably safer.
    Candidates are never dropped or invented: reply ids are normalized against
    the candidate set (see ``_normalize``), and anything unsalvageable — no
    array, no known ids, transport error — returns the hits unchanged with a
    stderr note. Reordered hits carry ``reranked: True``. ``chat_fn`` is
    injectable for tests, mirroring the ``embed_fn`` seam.
    """
    if len(hits) < 2 or (gate and fusion_trusts_top1(hits)):
        return hits
    fn = chat_fn or (lambda p: ollama_chat(p, model, base_url))
    try:
        raw = _parse_ids(fn(_prompt(query, hits, keep_text, template)))
    except Exception as e:  # noqa: BLE001 — the contract IS fall back on any failure
        print(f"gloss: rerank skipped ({e}); original order kept", file=sys.stderr)
        return hits
    order = _normalize(raw, [h["id"] for h in hits])
    if order is None:
        print("gloss: rerank skipped (reply named no candidate); original order kept",
              file=sys.stderr)
        return hits
    by_id = {h["id"]: h for h in hits}
    out = [dict(by_id[i]) for i in order]
    for h in out:
        h["reranked"] = True
    return out
