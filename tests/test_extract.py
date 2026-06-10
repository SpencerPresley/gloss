from pydantic import BaseModel
from gloss.extract import StubExtractor, OllamaExtractor


class _S(BaseModel):
    a: int


def test_stub_extractor_returns_schema_instance():
    out = StubExtractor({"a": 7}).extract("anything", _S)
    assert isinstance(out, _S) and out.a == 7


class _FakeRunnable:
    def __init__(self, ok: bool):
        self._ok = ok

    def invoke(self, messages):
        return ({"parsed": _S(a=1), "parsing_error": None} if self._ok
                else {"parsed": None, "parsing_error": "ignored schema, returned prose"})


class _FakeChat:
    """Simulates a model (like minimax) that ignores json_schema but does function_calling."""
    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema, method, include_raw):
        self.calls.append(method)
        return _FakeRunnable(ok=(method == "function_calling"))


def test_auto_discovers_and_pins_method():
    chat = _FakeChat()
    ex = OllamaExtractor("minimax-m3:cloud", chat_factory=lambda: chat)
    assert isinstance(ex.extract("p", _S), _S)
    assert chat.calls == ["json_schema", "function_calling"]          # tried, fell back
    ex.extract("p2", _S)
    assert chat.calls == ["json_schema", "function_calling", "function_calling"]  # pinned


def test_method_override_skips_probe():
    chat = _FakeChat()
    ex = OllamaExtractor("whatever", method="function_calling", chat_factory=lambda: chat)
    ex.extract("p", _S)
    assert chat.calls == ["function_calling"]                          # no json_schema probe


def test_raises_when_all_methods_fail():
    class _AllFail:
        def with_structured_output(self, schema, method, include_raw):
            return _FakeRunnable(ok=False)
    ex = OllamaExtractor("bad", chat_factory=lambda: _AllFail())
    import pytest
    with pytest.raises(ValueError):
        ex.extract("p", _S)
