from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from sentinel.config import Settings
from sentinel.llm.anthropic_client import AnthropicClient
from sentinel.llm.base import LLMClient, LLMError, LLMResponse, Message, StructuredOutputError, extract_json
from sentinel.llm.fake import FakeLLM
from sentinel.llm.openai_client import OpenAIClient
from sentinel.llm.router import CircuitBreaker, LLMRouter, build_client, build_llm


class Plan(BaseModel):
    steps: list[str]
    n: int


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": [1,2]}} done') == {"a": {"b": [1, 2]}}
    assert extract_json("[1, 2]") == [1, 2]
    with pytest.raises(StructuredOutputError):
        extract_json("no json here")


def test_fake_extractive_cites_context():
    llm = FakeLLM()
    prompt = "QUESTION: who owns acme\n\nCONTEXT:\n[d#0] Acme is owned by Zed Corp. The sky is blue.\n[d#1] Other text."
    out = llm.complete([Message("user", prompt)]).text
    assert "Zed Corp" in out and "[d#0]" in out and "sky" not in out
    assert llm.complete([Message("user", "QUESTION: x\nCONTEXT:\n")]).text.startswith("INSUFFICIENT_EVIDENCE")
    assert "does not address" in llm.complete([Message("user", "QUESTION: zebra\nCONTEXT:\n[a#0] cats")]).text


def test_structured_output_default_and_scripted():
    llm = FakeLLM(scripted={"Plan": lambda _m: {"steps": ["a"], "n": 2}})
    assert llm.complete_structured([Message("user", "x")], Plan) == Plan(steps=["a"], n=2)
    default = FakeLLM().complete_structured([Message("user", "x")], Plan)
    assert default == Plan(steps=[], n=0)


def test_structured_output_self_repair():
    class Flaky(LLMClient):
        provider, model = "flaky", "m"

        def __init__(self):
            self.calls = 0

        def complete(self, messages, *, system=None, max_tokens=1024, temperature=0.0):
            self.calls += 1
            text = "oops" if self.calls == 1 else '{"steps": ["s"], "n": 1}'
            return LLMResponse(text, "flaky", "m")

    f = Flaky()
    assert f.complete_structured([Message("user", "x")], Plan).n == 1 and f.calls == 2

    class Broken(Flaky):
        def complete(self, messages, **kw):
            return LLMResponse('{"steps": "not-a-list"}', "b", "m")

    with pytest.raises(StructuredOutputError):
        Broken().complete_structured([Message("user", "x")], Plan, repair_attempts=1)


class _FailLLM(LLMClient):
    provider, model = "fail", "m"

    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kw):
        self.calls += 1
        raise LLMError("down")


def test_router_fallback_and_circuit_breaker():
    bad, good = _FailLLM(), FakeLLM()
    r = LLMRouter([bad, good], breaker_threshold=2, reset_after_s=60)
    for _ in range(3):
        assert r.complete([Message("user", "QUESTION: a\nCONTEXT:\n[x#0] a b c")]).provider == "fake"
    assert bad.calls == 2  # circuit opened after 2 failures, third call skipped the bad provider
    with pytest.raises(LLMError, match="All LLM providers failed"):
        LLMRouter([_FailLLM()]).complete([Message("user", "x")])
    with pytest.raises(ValueError):
        LLMRouter([])


def test_circuit_breaker_half_open():
    cb = CircuitBreaker(failure_threshold=1, reset_after_s=0.0)
    cb.record_failure()
    assert cb.allow()  # reset window elapsed -> half-open trial allowed
    cb.record_success()
    assert cb.failures == 0


def test_anthropic_client_with_stub_and_retry(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    class Err(Exception):
        status_code = 529

    def create(**kw):
        calls["n"] += 1
        assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
        if calls["n"] == 1:
            raise Err("overloaded")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
            stop_reason="end_turn",
        )

    stub = SimpleNamespace(messages=SimpleNamespace(create=create))
    c = AnthropicClient("k", "claude-x", client=stub, max_retries=2)
    r = c.complete([Message("user", "hi")], system="sys")
    assert r.text == "hello" and r.usage.total == 7 and calls["n"] == 2


def test_anthropic_client_non_retryable():
    class Err(Exception):
        status_code = 400

    def create(**kw):
        raise Err("bad request")

    c = AnthropicClient("k", "m", client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    with pytest.raises(LLMError):
        c.complete([Message("user", "x")])


def test_openai_client_stub():
    def create(**kw):
        assert kw["messages"][0]["role"] == "system"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
        )

    c = OpenAIClient(
        "k", "gpt", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    )
    assert c.complete([Message("user", "x")], system="s").text == "ok"

    def boom(**kw):
        raise RuntimeError("x")

    c2 = OpenAIClient(
        "k", "gpt", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    )
    with pytest.raises(LLMError):
        c2.complete([Message("user", "x")])


def test_build_llm_factory():
    s = Settings(_env_file=None, llm_provider="fake")
    assert isinstance(build_llm(s), FakeLLM)
    with pytest.raises(LLMError):
        build_client("anthropic", s)
    with pytest.raises(LLMError):
        build_client("openai", s)
    with pytest.raises(LLMError):
        build_client("vertex", s)
    with pytest.raises(ValueError):
        build_client("nope", s)
    s2 = Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="k", llm_fallbacks=["fake"])
    r = build_llm(s2)
    assert isinstance(r, LLMRouter) and [c.provider for c in r.clients] == ["anthropic", "fake"]
