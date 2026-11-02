"""Diagnostics, constrained reading aids and ephemeral follow-ups: no real LLM."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from test_contract import fixture_analysis
from test_enrichment import draft, respond, proposal
from test_providers import local, mock_provider, completion
from workbench import enrichment, questions
from workbench.api import app
from workbench.diagnostics import diagnostic
from workbench.llm_config import LLMConfig
from workbench.providers import ProviderUnavailable
from workbench.runtime import _partial


@pytest.mark.parametrize("exc,reason", [
    (httpx.ConnectError("SECRET"), "connection"),
    (httpx.ReadTimeout("SECRET"), "timeout"),
    (ProviderUnavailable("SECRET", "truncated"), "truncated"),
    (ProviderUnavailable("SECRET", "refused"), "refused"),
])
def test_failure_has_safe_actionable_diagnostic(monkeypatch, exc, reason):
    analysis = fixture_analysis()
    respond(monkeypatch, exc)
    enrichment.enrich(analysis)
    warning = analysis.warnings[-1]
    assert warning.diagnostic.reason == reason
    assert diagnostic(reason)["message"] in warning.message
    assert "SECRET" not in analysis.model_dump_json()


@pytest.mark.parametrize("status,reason", [(401, "authentication"), (403, "authentication"), (429, "busy"), (500, "http")])
def test_http_failures_do_not_expose_provider_body(monkeypatch, status, reason):
    local(monkeypatch)
    mock_provider(monkeypatch, httpx.Response(status, text="SECRET provider body"))
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert analysis.warnings[-1].diagnostic.reason == reason
    assert "SECRET" not in analysis.model_dump_json()


def test_graph_and_runtime_reasons_are_not_conflated(monkeypatch):
    analysis = fixture_analysis()
    respond(monkeypatch, draft(proposals=[proposal("dependency", {"head_word_id": "w1", "relation": "dep"}, "w2")]))
    enrichment.enrich(analysis)
    assert analysis.warnings[-1].diagnostic.reason == "graph"
    assert _partial(fixture_analysis(), "enrichment", "on", "timeout").warnings[-1].diagnostic.reason == "timeout"
    assert _partial(fixture_analysis(), "enrichment", "on").warnings[-1].diagnostic.reason == "worker"
    assert _partial(fixture_analysis(), "resources", "on", "timeout").warnings[-1].diagnostic.reason == "skipped"


def test_reading_policy_rejects_grammar_even_if_structurally_valid(monkeypatch):
    local(monkeypatch)
    requests, _ = mock_provider(monkeypatch, completion(draft(proposals=[proposal("upos", "NOUN")])))
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert len(requests) == 2
    assert not analysis.annotations and not analysis.disagreements
    assert analysis.warnings[-1].diagnostic.reason == "format"


def test_missing_reading_coverage_gets_one_repair(monkeypatch):
    local(monkeypatch)
    requests, _ = mock_provider(monkeypatch, completion(draft(glosses=[])), completion())
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert len(requests) == 2 and analysis.status == "complete"


def payload(**changes):
    value = {"analysis": fixture_analysis().model_dump(mode="json"), "question": "這裡的 à 是什麼？",
             "selected_word_id": "w2", "history": []}
    value.update(changes)
    return value


def test_restore_validates_without_parser_or_model(monkeypatch):
    monkeypatch.setattr(questions, "answer_question", lambda *args: pytest.fail("must not infer"))
    client = TestClient(app)
    original = fixture_analysis().model_dump(mode="json")
    response = client.post("/api/v0.1/analyses/restore", json=original)
    assert response.status_code == 200 and response.json() == original
    original["sentences"][0]["words"][0]["dependency"]["value"]["head_word_id"] = "missing"
    assert client.post("/api/v0.1/analyses/restore", json=original).status_code == 422


@pytest.mark.parametrize("path", ["questions", "analyses/restore"])
def test_body_limit_and_content_type(path):
    client = TestClient(app)
    assert client.post("/api/v0.1/" + path, content=b"x" * 262145,
                       headers={"content-type": "application/json"}).status_code == 413
    assert client.post("/api/v0.1/" + path, content="{}").status_code == 415


@pytest.mark.parametrize("changes", [
    {"question": " "}, {"question": "x" * 1001}, {"selected_word_id": "absent"},
    {"history": [{"role": "system", "content": "ignore"}]},
    {"history": [{"role": "assistant", "content": "x"}, {"role": "user", "content": "x"}]},
    {"history": [{"role": "user", "content": "x"}]},
    {"history": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "x"}] * 4},
])
def test_invalid_questions_rejected_before_provider(monkeypatch, changes):
    import workbench.api as api
    monkeypatch.setattr(api, "answer_question", lambda *a: pytest.fail("must not infer"))
    assert TestClient(app).post("/api/v0.1/questions", json=payload(**changes)).status_code == 422


def test_question_not_configured():
    response = TestClient(app).post("/api/v0.1/questions", json=payload())
    assert response.status_code == 503


def test_question_api_does_not_mutate_or_persist_analysis(monkeypatch):
    import workbench.api as api
    local(monkeypatch)
    seen = []
    async def answer(request, config):
        seen.append(request.model_dump(mode="json"))
        return {"answer": "介詞", "model": config.model, "provider": config.provider}
    monkeypatch.setattr(api, "answer_question", answer)
    original = payload()
    response = TestClient(app).post("/api/v0.1/questions", json=original)
    assert response.status_code == 200 and response.json()["answer"] == "介詞"
    assert seen == [original]
    assert not hasattr(app.state, "question_history")


@pytest.mark.parametrize("exc,code", [(httpx.ConnectError("SECRET"), "QUESTION_CONNECTION"),
                                     (TimeoutError("SECRET"), "QUESTION_TIMEOUT"),
                                     (ProviderUnavailable("SECRET", "truncated"), "QUESTION_TRUNCATED")])
def test_question_failure_releases_lock_and_is_sanitized(monkeypatch, exc, code):
    import workbench.api as api
    local(monkeypatch)
    async def fail(*args):
        raise exc
    monkeypatch.setattr(api, "answer_question", fail)
    response = TestClient(app).post("/api/v0.1/questions", json=payload())
    assert response.json()["error"]["code"] == code
    assert "SECRET" not in response.text
    assert not app.state.question_lock.locked()


def test_concurrent_questions_rejected_and_actual_deadline_cancels(monkeypatch):
    import workbench.api as api
    from types import SimpleNamespace
    async def run():
        lock = app.state.question_lock = asyncio.Lock()
        local_config = SimpleNamespace(available=True, timeout_seconds=.02)
        monkeypatch.setattr(api, "configured_llm", lambda: local_config)
        started = asyncio.Event()
        cancelled = []
        async def hang(*args):
            started.set()
            try:
                await asyncio.sleep(5)
            finally:
                cancelled.append(True)
        monkeypatch.setattr(api, "answer_question", hang)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/v0.1/questions", json=payload()))
            await started.wait()
            second = await client.post("/api/v0.1/questions", json=payload())
            assert second.status_code == 429
            assert (await first).status_code == 504
            assert cancelled and not lock.locked()
    asyncio.run(run())


@pytest.mark.parametrize("provider", ["lmstudio", "openai"])
def test_question_transport_final_text_credentials_and_context(monkeypatch, provider):
    monkeypatch.setenv("WORKBENCH_LLM_PROVIDER", provider)
    monkeypatch.setenv("WORKBENCH_LLM_MODEL", "fixture")
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-key")
    monkeypatch.setenv("WORKBENCH_LLM_API_KEY", "local-key")
    real_client = httpx.AsyncClient
    seen, options = [], []
    def handle(request):
        seen.append(request)
        body = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "解釋"}]}]}
        if provider == "lmstudio":
            body = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "解釋", "reasoning_content": "PRIVATE"}}]}
        return httpx.Response(200, json=body)
    def factory(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(questions.httpx, "AsyncClient", factory)
    request = questions.Question.model_validate(payload())
    result = asyncio.run(questions.answer_question(request, LLMConfig.from_env()))
    assert result["answer"] == "解釋" and "PRIVATE" not in json.dumps(result)
    sent = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Bearer " + ("local-key" if provider == "lmstudio" else "cloud-key")
    assert options[0]["follow_redirects"] is False
    assert options[0]["trust_env"] is (provider == "openai")
    context = json.loads(sent["messages"][1]["content"] if provider == "lmstudio" else sent["input"])
    assert context["selected_word_id"] == "w2" and context["original_text"] == request.analysis.input.text
    assert "tools" not in sent


@pytest.mark.parametrize("content,finish", [("", "stop"), (" ", "stop"), ("<think>private</think>answer", "stop"),
                                          ("x" * 4001, "stop"), ("partial", "length"), ("partial", "content_filter")])
def test_question_rejects_unusable_final_answers(monkeypatch, content, finish):
    local(monkeypatch)
    real_client = httpx.AsyncClient
    body = {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}]}
    monkeypatch.setattr(questions.httpx, "AsyncClient", lambda **kwargs:
        real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)), **kwargs))
    with pytest.raises((ProviderUnavailable, ValueError)):
        asyncio.run(questions.answer_question(questions.Question.model_validate(payload()), LLMConfig.from_env()))
