"""LM Studio and cloud transport/config contracts, using mock HTTP only."""

import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from test_contract import fixture_analysis
from test_enrichment import draft, proposal
from workbench import enrichment, pipeline, providers
from workbench.adapters.base import Parsed
from workbench.api import app
from workbench.llm_config import ConfigurationError, LLMConfig, configured_llm
from workbench.runtime import AnalysisRuntime, Budgets
from workbench.schema import Analysis
from workbench.validation import validate_analysis


def local(monkeypatch, model="qwen3-14b"):
    monkeypatch.setenv("WORKBENCH_LLM_PROVIDER", "lmstudio")
    monkeypatch.setenv("WORKBENCH_LLM_MODEL", model)


def completion(candidate=None, **message_changes):
    message = {"role": "assistant", "content": (candidate or draft()).model_dump_json()}
    message.update(message_changes)
    return {"id": "local-request", "model": "local-actual-model", "choices": [
        {"finish_reason": "stop", "message": message},
    ]}


def mock_provider(monkeypatch, *responses):
    requests, options = [], []
    original = httpx.Client

    def handle(request):
        requests.append(request)
        response = responses[min(len(requests) - 1, len(responses) - 1)]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=response)

    def client(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(providers.httpx, "Client", client)
    return requests, options


@pytest.mark.parametrize("model", ["qwen3-14b", "google/gemma-4-e4b", "user/custom-id-Q4_K_M"])
def test_lmstudio_payload_no_cloud_key_and_valid_result(monkeypatch, model):
    local(monkeypatch, model)
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-secret-must-never-be-used")
    requests, options = mock_provider(monkeypatch, completion(reasoning_content="private reasoning"))
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert analysis.status == "complete"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "http://127.0.0.1:1234/v1/chat/completions"
    assert "authorization" not in request.headers
    payload = json.loads(request.content)
    assert payload["model"] == model
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert json.loads(payload["messages"][1]["content"])["original_text"] == analysis.input.text
    assert payload["response_format"]["json_schema"]["schema"] == enrichment._OUTPUT_SCHEMA
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["stream"] is False and payload["max_tokens"] == 4096
    assert "text" not in payload and "store" not in payload
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert options[0]["timeout"].read == 90
    assert analysis.sources[-1].metadata["provider"] == "lmstudio"
    assert analysis.sources[-1].name == "local-actual-model"
    assert "private reasoning" not in analysis.model_dump_json()


def test_local_token_and_custom_url_are_explicit(monkeypatch):
    local(monkeypatch)
    monkeypatch.setenv("WORKBENCH_LLM_BASE_URL", "http://192.0.2.1:4321/v1/")
    monkeypatch.setenv("WORKBENCH_LLM_API_KEY", "local-token")
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-token")
    monkeypatch.setenv("WORKBENCH_LLM_MAX_TOKENS", "2048")
    requests, _ = mock_provider(monkeypatch, completion())
    enrichment.enrich(fixture_analysis())
    assert str(requests[0].url) == "http://192.0.2.1:4321/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer local-token"
    assert json.loads(requests[0].content)["max_tokens"] == 2048
    assert "local-token" not in repr(LLMConfig.from_env())


@pytest.mark.parametrize("error", [httpx.ConnectError("private diagnostic"), httpx.ReadTimeout("private diagnostic")])
def test_local_failure_never_falls_back_to_cloud_or_retries(monkeypatch, error):
    local(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-token")
    requests, _ = mock_provider(monkeypatch, error)
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert len(requests) == 1 and requests[0].url.host == "127.0.0.1"
    assert analysis.status == "partial" and not analysis.annotations
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"
    assert "private diagnostic" not in analysis.model_dump_json()


@pytest.mark.parametrize("finish", ["length", "tool_calls", "content_filter", None])
def test_incomplete_completion_is_not_retried_even_if_content_is_valid(monkeypatch, finish):
    local(monkeypatch)
    response = completion()
    response["choices"][0]["finish_reason"] = finish
    requests, _ = mock_provider(monkeypatch, response)
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert len(requests) == 1 and not analysis.annotations
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


@pytest.mark.parametrize("change", [
    {"refusal": "refused"}, {"tool_calls": [{"id": "tool"}]},
    {"function_call": {"name": "tool"}}, {"role": "user"},
])
def test_nonanswer_messages_are_not_retried(monkeypatch, change):
    local(monkeypatch)
    requests, _ = mock_provider(monkeypatch, completion(**change))
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert len(requests) == 1 and not analysis.annotations


@pytest.mark.parametrize("response", [None, [], {}, {"choices": []}, {"choices": [None]},
                                      {"choices": [{"finish_reason": "stop", "message": None}]}])
def test_malformed_envelope_downgrades_once(monkeypatch, response):
    local(monkeypatch)
    requests, _ = mock_provider(monkeypatch, httpx.Response(200, json=response) if response is not None
                                else httpx.Response(200, content=b"not-json"))
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert len(requests) == 1 and not analysis.annotations
    assert analysis.status == "partial"


@pytest.mark.parametrize("content", [None, "", "```json\n{}\n```", "<think>reason</think>{}", "{}"])
def test_bad_structured_content_can_repair_once(monkeypatch, content):
    local(monkeypatch)
    requests, _ = mock_provider(monkeypatch, completion(content=content), completion())
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert len(requests) == 2 and analysis.status == "complete"
    assert "previous candidate" in json.loads(requests[1].content)["messages"][0]["content"]


def test_local_semantic_failure_rolls_back_and_keeps_parser(monkeypatch):
    local(monkeypatch)
    requests, _ = mock_provider(monkeypatch, completion(draft(proposals=[proposal("upos", "INVALID")])))
    analysis = fixture_analysis()
    original = analysis.model_copy(deep=True)
    enrichment.enrich(analysis)
    assert len(requests) == 2 and not analysis.annotations
    assert analysis.sentences == original.sentences and analysis.sources == original.sources


@pytest.mark.parametrize("status", [302, 401, 404, 429, 500])
def test_http_errors_and_redirects_do_not_retry_or_fallback(monkeypatch, status):
    local(monkeypatch)
    requests, _ = mock_provider(monkeypatch, httpx.Response(status, headers={"Location": "https://api.openai.com/v1/responses"}))
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert len(requests) == 1
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


@pytest.mark.parametrize("url", ["file:///tmp/model", "http://user:secret@localhost:1234/v1",
                               "http://localhost:1234/v1?key=secret", "http://localhost:1234/v1#secret",
                               "http://localhost:1234/v1/chat/completions", "", "http://localhost:bad/v1",
                               "http://localhost:0/v1", "http://local host/v1"])
def test_invalid_local_url_disables_enrichment_without_echoing_secrets(monkeypatch, url):
    local(monkeypatch)
    monkeypatch.setenv("WORKBENCH_LLM_BASE_URL", url)
    with pytest.raises(ConfigurationError) as error:
        LLMConfig.from_env()
    assert "secret" not in str(error.value)
    assert configured_llm() is None


@pytest.mark.parametrize("name,value", [("WORKBENCH_LLM_PROVIDER", "unknown"),
    ("WORKBENCH_LLM_TIMEOUT_SECONDS", "nan"), ("WORKBENCH_LLM_TIMEOUT_SECONDS", "0"),
    ("WORKBENCH_LLM_TIMEOUT_SECONDS", "301"), ("WORKBENCH_LLM_MAX_TOKENS", "0")])
def test_invalid_settings_disable_only_optional_enrichment(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert configured_llm() is None
    assert Budgets.from_env().total == 30


def test_missing_model_and_cloud_credentials_are_not_inferred(monkeypatch):
    assert not LLMConfig.from_env().available
    monkeypatch.setenv("WORKBENCH_LLM_MODEL", "fixture")
    assert not LLMConfig.from_env().available
    monkeypatch.setenv("WORKBENCH_LLM_PROVIDER", "lmstudio")
    assert LLMConfig.from_env().available
    monkeypatch.delenv("WORKBENCH_LLM_MODEL")
    assert not LLMConfig.from_env().available


@pytest.mark.parametrize("provider,override,total,client_ms", [("openai", None, 30, 35000),
    ("lmstudio", None, 100, 105000), ("lmstudio", "120", 130, 135000)])
def test_runtime_and_capabilities_share_deadline(monkeypatch, provider, override, total, client_ms):
    monkeypatch.setenv("WORKBENCH_LLM_PROVIDER", provider)
    monkeypatch.setenv("WORKBENCH_LLM_MODEL", "fixture")
    if override:
        monkeypatch.setenv("WORKBENCH_LLM_TIMEOUT_SECONDS", override)
    runtime = AnalysisRuntime()
    try:
        assert runtime.budgets.total == total
        assert runtime.workers["fr"].budgets.enrichment == total - 10
        monkeypatch.setattr(app.state, "runtime", SimpleNamespace(
            budgets=runtime.budgets, refresh=lambda: None, ready=lambda language: True), raising=False)
        payload = TestClient(app).get("/api/v0.1/capabilities").json()
        assert payload["analysis_timeout_ms"] == client_ms
        assert payload["enrichment_available"] is (provider == "lmstudio")
        assert payload["enrichment_provider"] == provider
        assert "api_key" not in payload and "base_url" not in payload
    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ["off", "on"])
def test_local_pipeline_api_works_without_openai_key(monkeypatch, mode):
    local(monkeypatch)
    requests, _ = mock_provider(monkeypatch, completion())
    original = fixture_analysis()
    monkeypatch.setattr(pipeline, "ready", lambda language: True)
    monkeypatch.setattr(pipeline, "get_adapter", lambda language: SimpleNamespace(
        parse=lambda text: Parsed(original.sentences, original.sources, [])))
    monkeypatch.setattr(app.state, "runtime", SimpleNamespace(analyze=pipeline.analyze), raising=False)
    response = TestClient(app).post("/api/v0.1/analyses", json={"text": "🍵 au", "language": "fr", "enrichment": mode})
    assert response.status_code == 200
    result = Analysis.model_validate(response.json())
    validate_analysis(result)
    assert result.status == "complete"
    assert len(requests) == (1 if mode == "on" else 0)


def test_cloud_mode_ignores_local_address_and_token(monkeypatch):
    monkeypatch.setenv("WORKBENCH_LLM_MODEL", "fixture")
    monkeypatch.setenv("OPENAI_API_KEY", "cloud-test-key")
    monkeypatch.setenv("WORKBENCH_LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("WORKBENCH_LLM_API_KEY", "local-test-key")
    requests, _ = mock_provider(monkeypatch, {"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": draft().model_dump_json()}]}]})
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert requests[0].url.host == "api.openai.com"
    assert requests[0].headers["Authorization"] == "Bearer cloud-test-key"
    assert analysis.sources[-1].metadata["provider"] == "openai"


@pytest.mark.parametrize("response,model,expected", [
    ({"data": [{"id": "qwen3-14b"}, {"id": "gemma-4-e4b"}]}, "qwen3-14b", 0),
    ({"data": [{"id": "qwen3-14b"}]}, "not-installed", 1),
    ({"data": []}, "", 0),
    ({"data": [None]}, "qwen3-14b", 1),
    (httpx.Response(401, content=b"private auth diagnostic"), "qwen3-14b", 1),
])
def test_diagnostic_lists_ids_without_inference_or_secrets(monkeypatch, capsys, response, model, expected):
    local(monkeypatch, model)
    monkeypatch.setenv("WORKBENCH_LLM_API_KEY", "private-token")
    requests, _ = mock_provider(monkeypatch, response)
    script = Path(__file__).resolve().parents[2] / "scripts/check_lmstudio.py"
    check = runpy.run_path(str(script))["main"]
    assert check() == expected
    assert len(requests) == 1 and requests[0].method == "GET"
    assert requests[0].url.path == "/v1/models"
    output = capsys.readouterr()
    assert "private" not in output.out + output.err
