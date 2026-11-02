"""Opt-in real-model checks; development probes, not language-accuracy gold.

WORKBENCH_TEST_MODELS=1 WORKBENCH_STANZA_DIR=/path/to/models pytest backend/tests
"""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench.api import app
from workbench.pipeline import analyze


pytestmark = pytest.mark.skipif(os.getenv("WORKBENCH_TEST_MODELS") != "1",
                                reason="requires explicitly provisioned parser models")
CASES = json.loads((Path(__file__).resolve().parents[2] / "eval/cases/french-parser-spike.json")
                   .read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_stanza_preserves_original_text_and_expands_contractions(case, monkeypatch):
    monkeypatch.setenv("WORKBENCH_FRENCH_PARSER", "stanza")
    result = analyze(case["text"], "fr", "off")
    assert result.input.text == case["text"]
    assert result.status == "complete"
    assert not any(source.kind == "llm" for source in result.sources)
    if "expansion" in case:
        expected = case["expansion"]
        matching = [(sentence, segment) for sentence in result.sentences for segment in sentence.segments
                    if segment.surface == expected["surface"]]
        assert len(matching) == 1
        sentence, segment = matching[0]
        words = {word.id: word for word in sentence.words}
        assert [words[wid].form for wid in segment.word_ids] == expected["forms"]
        assert all(words[wid].span is None for wid in segment.word_ids)


def test_ginza_and_stanza_coexist_without_llm(monkeypatch):
    monkeypatch.delenv("WORKBENCH_FRENCH_PARSER", raising=False)
    monkeypatch.delenv("WORKBENCH_STANZA_PACKAGE", raising=False)
    with TestClient(app) as client:
        languages = client.get("/api/v0.1/capabilities").json()["languages"]
        assert all(item["ready"] for item in languages if item["language"] in {"fr", "ja"})
        for text, language in (("Je vais au marché.", "fr"), ("昨日、本を三冊買った。", "ja"),
                               ("Elle est arrivée.", "fr")):
            response = client.post("/api/v0.1/analyses", json={
                "text": text, "language": language, "enrichment": "off",
            })
            assert response.status_code == 200
            result = response.json()
            assert result["status"] == "complete"
            assert result["input"]["text"] == text
            assert all(source["kind"] != "llm" for source in result["sources"])
            if language == "fr":
                assert result["sources"][0]["name"] == "stanza-fr-combined"
                assert any(warning["code"] == "MODEL_LIMITATION" for warning in result["warnings"])


def test_real_parsers_spawned_workers_and_local_http_provider(monkeypatch):
    """Real parser + worker + HTTP, but a fake LM server (not a real model eval)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    from workbench.schema import Analysis
    from workbench.validation import validate_analysis

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload, self.headers.get("Authorization")))
            prompt = json.loads(payload["messages"][1]["content"])
            candidate = {"translations": [{"sentence_id": s["id"], "text": "測試翻譯"}
                                          for s in prompt["sentences"]],
                         "glosses": [{"word_id": w["id"], "text": "fixture gloss"}
                                     for s in prompt["sentences"] for w in s["words"]],
                         "constructions": [], "proposals": []}
            body = json.dumps({"id": "fixture-http", "model": payload["model"], "choices": [
                {"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(candidate)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("WORKBENCH_LLM_PROVIDER", "lmstudio")
        monkeypatch.setenv("WORKBENCH_LLM_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
        monkeypatch.setenv("WORKBENCH_LLM_MODEL", "local-fixture")
        try:
            with TestClient(app) as client:
                capability = client.get("/api/v0.1/capabilities").json()
                assert capability["enrichment_available"]
                assert capability["analysis_timeout_ms"] == 105000
                for text, language in (("Je vais au marché.", "fr"), ("昨日、本を三冊買った。", "ja")):
                    response = client.post("/api/v0.1/analyses", json={"text": text, "language": language, "enrichment": "on"})
                    assert response.status_code == 200
                    result = Analysis.model_validate(response.json())
                    validate_analysis(result)
                    assert result.status == "complete"
                    assert result.sources[-1].metadata["provider"] == "lmstudio"
                    assert result.annotations
            assert len(requests) == 2
            assert all(path == "/v1/chat/completions" and auth is None for path, payload, auth in requests)
        finally:
            server.shutdown()
            thread.join(timeout=2)
