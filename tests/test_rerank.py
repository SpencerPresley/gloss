"""Rerank tests — no Ollama: ``chat_fn`` is injected."""
import io
import urllib.error

import pytest

from docq.rerank import OllamaUnavailable, _parse_ids, _raise_ollama_error, rerank

_HITS = [
    {"id": 1, "principle": "deep-modules", "section": "4.5", "type": "red_flag",
     "context_line": "about shallow modules", "text": "A shallow module has a complex interface."},
    {"id": 2, "principle": "general-purpose", "section": "6.3", "type": "example",
     "context_line": "about general APIs", "text": "A general-purpose method covers many operations."},
    {"id": 3, "principle": "", "section": "10.7", "type": "definition",
     "context_line": "about exception aggregation", "text": "Handle many exceptions in one place."},
]


def test_rerank_reorders_and_tags():
    seen = {}

    def chat(prompt):
        seen["prompt"] = prompt
        return "[3, 1, 2]"

    out = rerank("try/catch everywhere", _HITS, chat_fn=chat)
    assert [h["id"] for h in out] == [3, 1, 2]
    assert all(h["reranked"] for h in out)
    assert [h["id"] for h in _HITS] == [1, 2, 3]        # input untouched
    assert "try/catch everywhere" in seen["prompt"]     # query present
    assert "id=3" in seen["prompt"]                     # candidates present


def test_rerank_tolerates_code_fences():
    out = rerank("q", _HITS, chat_fn=lambda p: "```json\n[2, 3, 1]\n```")
    assert [h["id"] for h in out] == [2, 3, 1]


@pytest.mark.parametrize("reply", [
    "the best one is id 3",          # no array
    '[{"id": 1}]',                   # not flat ints
    "[9, 8]",                        # only invented ids -> no ranking signal
])
def test_rerank_falls_back_on_bad_reply(reply, capsys):
    out = rerank("q", _HITS, chat_fn=lambda p: reply)
    assert out is _HITS                                 # unchanged, same object
    assert "rerank skipped" in capsys.readouterr().err


@pytest.mark.parametrize("reply,expected", [
    ("[3]", [3, 1, 2]),              # subset: picks first, rest keep fused order
    ("[2, 2, 3]", [2, 3, 1]),        # duplicates deduped, first occurrence wins
    ("[3, 99, 1, 2]", [3, 1, 2]),    # invented id ignored, rest honored
])
def test_rerank_salvages_imperfect_replies(reply, expected):
    """Small models return their top picks and drop the rest (observed with
    gemma4:e2b) — that's a ranking signal, not a failure."""
    out = rerank("q", _HITS, chat_fn=lambda p: reply)
    assert [h["id"] for h in out] == expected
    assert all(h["reranked"] for h in out)


def test_rerank_does_not_hide_transport_error():
    def down(prompt):
        raise OllamaUnavailable("cannot reach Ollama")

    with pytest.raises(OllamaUnavailable, match="cannot reach Ollama"):
        rerank("q", _HITS, chat_fn=down)


def test_ollama_404_names_missing_local_model_and_pull_command():
    error = urllib.error.HTTPError(
        "http://localhost:11434/api/chat", 404, "Not Found", {},
        io.BytesIO(b'{"error":"model \'gemma3:1b\' not found"}'),
    )

    with pytest.raises(OllamaUnavailable) as raised:
        _raise_ollama_error(error, model="gemma3:1b", base_url="http://localhost:11434")

    message = str(raised.value)
    assert "model 'gemma3:1b' not found" in message
    assert "ollama pull gemma3:1b" in message


def test_connection_refused_names_ollama_url():
    error = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    with pytest.raises(OllamaUnavailable) as raised:
        _raise_ollama_error(error, model="gemma4:e2b", base_url="http://localhost:11434")

    assert "cannot reach Ollama at http://localhost:11434" in str(raised.value)


@pytest.mark.parametrize(("code", "body", "expected"), [
    (401, b'{"error":"Unauthorized"}', "ollama signin"),  # observed signed out, 2026-09-14
    (403, b'{"error":"cloud account rejected the request"}', "ollama signin"),
    (402, b'{"error":"cloud account rejected the request"}', "no available usage"),
    (429, b'{"error":"cloud account rejected the request"}', "usage limit"),
])
def test_cloud_account_errors_are_distinguished(code, body, expected):
    error = urllib.error.HTTPError(
        "http://localhost:11434/api/chat", code, "Cloud error", {},
        io.BytesIO(body),
    )

    with pytest.raises(OllamaUnavailable, match=expected):
        _raise_ollama_error(error, model="glm-5.3:cloud", base_url="http://localhost:11434")


def test_rerank_short_circuits_below_two():
    def boom(prompt):
        raise AssertionError("must not be called")

    assert rerank("q", _HITS[:1], chat_fn=boom) == _HITS[:1]
    assert rerank("q", [], chat_fn=boom) == []


def test_gate_keeps_dual_backed_top1_without_model_call():
    """Fusion's #1 with lexical#1 + semantic<=5 is trusted: no chat call at all."""
    def boom(prompt):
        raise AssertionError("must not be called")

    hits = [dict(h, channels={"lexical": 1, "semantic": 3} if h["id"] == 1 else {"lexical": 2})
            for h in _HITS]
    assert rerank("q", hits, chat_fn=boom) == hits
    # gate=False forces the model call even for a dual-backed #1
    out = rerank("q", hits, gate=False, chat_fn=lambda p: "[3, 2, 1]")
    assert [h["id"] for h in out] == [3, 2, 1]


def test_gate_lets_weakly_backed_top1_be_judged():
    """#1 without dual backing (or without channels at all) goes to the model."""
    hits = [dict(h, channels={"semantic": 1}) for h in _HITS]     # no lexical backing
    out = rerank("q", hits, chat_fn=lambda p: "[2, 1, 3]")
    assert [h["id"] for h in out] == [2, 1, 3]
    out = rerank("q", _HITS, chat_fn=lambda p: "[2, 1, 3]")       # lexical-mode hits: no channels
    assert [h["id"] for h in out] == [2, 1, 3]


def test_custom_template_is_used():
    seen = {}

    def chat(prompt):
        seen["prompt"] = prompt
        return "[1, 2, 3]"

    rerank("my query", _HITS, template="CUSTOM {n} :: {query} :: {candidates}",
           chat_fn=chat)
    assert seen["prompt"].startswith("CUSTOM 3 :: my query ::")
    assert "id=1" in seen["prompt"]


def test_parse_ids_extracts_array_amid_prose():
    assert _parse_ids("Sure! Here you go: [5, 2, 9] hope that helps") == [5, 2, 9]
