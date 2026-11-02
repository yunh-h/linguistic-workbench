"""Real subprocess failure injection; no NLP models or provider calls required."""

from concurrent.futures import ThreadPoolExecutor
import multiprocessing as mp
import os
import signal
import struct
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_contract import fixture_analysis
from workbench.adapters.base import Parsed
from workbench.api import app
from workbench.pipeline import ParserInvalid, analyze
from workbench.runtime import Budgets, LanguageWorker, ParserTimeout
from workbench.validation import validate_analysis


def fake_worker(connection, language):
    if language == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    connection.send(("ready", None))
    while True:
        try:
            text, enrichment = connection.recv()
        except EOFError:
            return
        analysis = fixture_analysis()
        analysis.analysis_id = text
        if text == "crash":
            os._exit(1)
        if text == "hang":
            sleep(60)
        if text == "partial_frame":
            connection._send(struct.pack("!i", 100) + b"x")
            sleep(60)
        if text == "slow":
            sleep(.12)
        if text == "total":
            sleep(.15)
        connection.send(("resources", analysis.model_dump_json()))
        if text == "resource_crash":
            os._exit(1)
        if text == "resource_hang":
            sleep(60)
        if text == "total":
            sleep(.15)
        if enrichment == "on":
            connection.send(("enrichment", analysis.model_dump_json()))
            if text in {"enrichment_hang", "total"}:
                sleep(60)
        connection.send(("result", analysis.model_dump_json()))


@pytest.fixture
def worker():
    instance = LanguageWorker("fr", Budgets(parser=.5, resources=.15, enrichment=.15,
                                            total=1, startup=5), target=fake_worker)
    instance.warm()
    assert instance.ready
    yield instance
    instance.close()


@pytest.mark.parametrize("fault", ["hang", "partial_frame"])
def test_parser_timeout_kills_process_and_next_request_recovers(worker, fault):
    pid = worker._process.pid
    start = monotonic()
    with pytest.raises(ParserTimeout):
        worker.analyze(fault, "off")
    assert monotonic() - start < 2
    assert pid not in [child.pid for child in mp.active_children()]
    worker.warm()
    assert worker._process.pid != pid
    assert worker.analyze("after-timeout", "off").analysis_id == "after-timeout"


@pytest.mark.skipif(os.name == "nt", reason="Windows termination does not use catchable SIGTERM")
def test_shutdown_reaps_a_worker_ignoring_sigterm():
    worker = LanguageWorker("ignore-term", Budgets(startup=5), target=fake_worker)
    worker.warm()
    pid = worker._process.pid
    start = monotonic()
    worker.close()
    assert monotonic() - start < 2
    assert pid not in [child.pid for child in mp.active_children()]


@pytest.mark.parametrize("text, enrichment, code", [
    ("resource_hang", "off", "RESOURCE_UNAVAILABLE"),
    ("resource_crash", "off", "RESOURCE_UNAVAILABLE"),
    ("enrichment_hang", "on", "ENRICHMENT_FAILED"),
])
def test_failed_optional_stage_preserves_valid_parser_snapshot(worker, text, enrichment, code):
    result = worker.analyze(text, enrichment)
    validate_analysis(result)
    assert result.status == "partial"
    assert result.sentences == fixture_analysis().sentences
    assert [w.code for w in result.warnings] == [code]


def test_total_deadline_caps_later_stage_even_with_larger_stage_budget():
    worker = LanguageWorker("fr", Budgets(parser=2, resources=2, enrichment=2,
                                         total=.4, startup=5), target=fake_worker)
    worker.warm()
    try:
        start = monotonic()
        result = worker.analyze("total", "on")
        assert monotonic() - start < 1.2
        assert result.status == "partial"
        assert result.warnings[-1].code == "ENRICHMENT_FAILED"
    finally:
        worker.close()


def test_worker_crash_before_parser_result_fails_and_recovers(worker):
    with pytest.raises(ParserInvalid):
        worker.analyze("crash", "off")
    worker.warm()
    assert worker.analyze("retry", "off").analysis_id == "retry"


def test_waiting_request_timeout_does_not_kill_busy_worker(worker):
    pid = worker._process.pid
    with worker._lock:
        with pytest.raises(ParserTimeout):
            worker.analyze("queued", "off")
        assert worker.ready
        assert worker._process.pid == pid
    assert worker.analyze("retry", "off").analysis_id == "retry"


def test_concurrent_requests_do_not_exchange_results(worker):
    with ThreadPoolExecutor(max_workers=2) as threads:
        first = threads.submit(worker.analyze, "slow", "off")
        second = threads.submit(worker.analyze, "second", "off")
        assert first.result(timeout=3).analysis_id == "slow"
        assert second.result(timeout=3).analysis_id == "second"


@pytest.mark.parametrize("fault", ["throw_after_mutation", "invalid_reference", "changed_core"])
def test_resource_failure_rolls_back_all_partial_writes(monkeypatch, fault):
    import workbench.pipeline as pipeline

    original = fixture_analysis()
    monkeypatch.setattr(pipeline, "ready", lambda language: True)
    monkeypatch.setattr(pipeline, "get_adapter", lambda language: SimpleNamespace(
        parse=lambda text: Parsed(original.sentences, original.sources, [])))

    def failing_lookup(candidate):
        if fault == "changed_core":
            candidate.sentences[0].words[0].lemma.value = "changed"
        else:
            candidate.sources.clear()
            if fault == "throw_after_mutation":
                raise RuntimeError("private diagnostic must not reach the response")

    monkeypatch.setattr(pipeline, "lookup_resources", failing_lookup)
    result = analyze("🍵 au", "fr", "off")
    validate_analysis(result)
    assert result.sentences == original.sentences
    assert result.sources == original.sources
    assert result.status == "partial"
    assert result.warnings[0].code == "RESOURCE_UNAVAILABLE"
    assert "private" not in result.model_dump_json()


@pytest.mark.parametrize("exception, status, code", [
    (ParserTimeout(), 504, "PARSER_TIMEOUT"),
    (RuntimeError("private diagnostic"), 500, "INTERNAL_ERROR"),
])
def test_api_failure_envelopes_are_retryable(monkeypatch, exception, status, code):
    def fail(*args):
        raise exception

    monkeypatch.setattr(app.state, "runtime", SimpleNamespace(analyze=fail), raising=False)
    response = TestClient(app).post("/api/v0.1/analyses", json={
        "text": "Je vais au marché.", "language": "fr", "enrichment": "off",
    })
    assert response.status_code == status
    assert response.json()["request_id"]
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"]
    assert "private" not in response.text
