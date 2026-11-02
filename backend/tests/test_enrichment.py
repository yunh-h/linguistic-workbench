"""Candidate/reconciliation regressions. No credentials, models or network needed."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from test_contract import fixture_analysis
from workbench import enrichment
from workbench.schema import Analysis, Annotation, Source, present, unavailable
from workbench.validation import AnalysisValidationError, validate_analysis


def draft(**changes):
    data = dict(translations=[{"sentence_id": "s1", "text": "測試翻譯"}],
                glosses=[{"word_id": f"w{i}", "text": f"gloss {i}"} for i in range(1, 4)],
                constructions=[], proposals=[])
    data.update(changes)
    return enrichment.EnrichmentDraft.model_validate(data)


def proposal(field, value, word_id="w1"):
    return dict(word_id=word_id, field=field, value_json=json.dumps(value), rationale="待確認的替代分析")


def respond(monkeypatch, *responses):
    calls = []

    def request(analysis, repair=False):
        calls.append(repair)
        response = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response, {"model": "fixture", "id": "request-fixture"}

    monkeypatch.setattr(enrichment, "_request", request)
    return calls


@pytest.mark.parametrize("field,value", [
    ("upos", "INVALID"), ("upos", "noun"), ("lemma", "  "), ("lemma", None),
    ("morphology", {"Number": "Sing"}), ("morphology", {"Number": []}),
    ("morphology", {"Number": [" "]}), ("morphology", {"Number": ["Sing", "Sing"]}),
    ("morphology", {"InventedFeature": ["Yes"]}), ("morphology", {"Number": ["Invented"]}),
    ("dependency", {"head_word_id": "w1", "relation": "dep"}),
    ("dependency", {"head_word_id": "absent", "relation": "dep"}),
    ("dependency", {"head_word_id": None, "relation": "obj"}),
    ("dependency", {"head_word_id": "w2", "relation": "root"}),
    ("dependency", {"head_word_id": "w2", "relation": ""}),
    ("dependency", {"head_word_id": "w2", "relation": "invented"}),
    ("dependency", {"head_word_id": "w2", "relation": "nsubj:invented"}),
    ("dependency", {"head_word_id": "w2", "relation": "obj", "extra": True}),
])
def test_invalid_proposals_are_rejected_without_partial_writes(monkeypatch, field, value):
    analysis = fixture_analysis()
    before = analysis.model_copy(deep=True)
    calls = respond(monkeypatch, draft(proposals=[proposal(field, value)]))
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert calls == [False, True]
    assert analysis.sentences == before.sentences
    assert analysis.sources == before.sources
    assert not analysis.annotations and not analysis.disagreements
    assert analysis.status == "partial"
    assert [w.code for w in analysis.warnings] == ["ENRICHMENT_FAILED"]


@pytest.mark.parametrize("proposals", [
    [proposal("dependency", {"head_word_id": "w1", "relation": "dep"}, "w2")],
    [proposal("dependency", {"head_word_id": None, "relation": "root"})],
    [proposal("dependency", {"head_word_id": "w3", "relation": "dep"}),
     proposal("dependency", {"head_word_id": "w1", "relation": "dep"}, "w3")],
])
def test_candidate_dependency_graph_cannot_cycle_or_add_roots(monkeypatch, proposals):
    analysis = fixture_analysis()
    respond(monkeypatch, draft(proposals=proposals))
    enrichment.enrich(analysis)
    assert not analysis.disagreements
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


def test_joint_root_change_is_valid_but_does_not_replace_parser(monkeypatch):
    analysis = fixture_analysis()
    before = analysis.model_copy(deep=True)
    respond(monkeypatch, draft(proposals=[
        proposal("dependency", {"head_word_id": None, "relation": "root"}, "w2"),
        proposal("dependency", {"head_word_id": "w2", "relation": "det"}, "w3"),
    ]))
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert len(analysis.disagreements) == 2
    assert [w.dependency.value for w in analysis.sentences[0].words] == [w.dependency.value for w in before.sentences[0].words]


def test_one_repair_only_and_no_invalid_first_batch_leaks(monkeypatch):
    analysis = fixture_analysis()
    calls = respond(monkeypatch, draft(glosses=[{"word_id": "missing", "text": "bad"}]), draft())
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert calls == [False, True]
    assert analysis.status == "complete"
    assert len(analysis.annotations) == 4
    assert len([s for s in analysis.sources if s.kind == "llm"]) == 1


@pytest.mark.parametrize("error", [httpx.ReadTimeout("private error"), httpx.ConnectError("private error")])
def test_provider_errors_are_not_retried(monkeypatch, error):
    analysis = fixture_analysis()
    calls = respond(monkeypatch, error)
    enrichment.enrich(analysis)
    assert calls == [False]
    assert analysis.status == "partial"
    assert "private error" not in analysis.model_dump_json()


def test_application_validation_is_atomic(monkeypatch):
    analysis = fixture_analysis()
    before = analysis.model_copy(deep=True)
    respond(monkeypatch, draft())
    real_apply = enrichment._apply

    def corrupt(*args):
        real_apply(*args)
        args[-1].annotations[-1].word_ids = ["nonexistent"]

    monkeypatch.setattr(enrichment, "_apply", corrupt)
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert analysis.sentences == before.sentences
    assert analysis.sources == before.sources
    assert not analysis.annotations
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


def add_resources(analysis):
    analysis.sources.append(Source(id="llm-1", kind="resource", name="fixture resource", version="1",
                                   locator=None, metadata={}))
    analysis.annotations.extend([
        Annotation(id="translation-1", kind="translation", sentence_id="s1", word_ids=[],
                   language="zh-TW", text=present("資源翻譯", "llm-1", "supported")),
        Annotation(id="gloss-1", kind="lexical_gloss", sentence_id="s1", word_ids=["w1"],
                   language="en", text=present("resource gloss", "llm-1", "supported")),
        Annotation(id="construction-1", kind="construction", sentence_id="s1", word_ids=["w1", "w2"],
                   language="zh-TW", text=present("資源構式", "llm-1", "supported")),
    ])


def test_resources_win_for_all_annotation_kinds_and_ids_do_not_collide(monkeypatch):
    analysis = fixture_analysis()
    add_resources(analysis)
    before = analysis.model_copy(deep=True)
    respond(monkeypatch, draft(constructions=[{"sentence_id": "s1", "word_ids": ["w2", "w1"], "text": "模型構式"}]))
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert analysis.annotations[:3] == before.annotations
    assert len(analysis.annotations) == 5  # 3 resource + 2 missing glosses
    assert analysis.status == "complete"
    assert len({s.id for s in analysis.sources}) == len(analysis.sources)


def test_equal_candidates_do_not_create_conflicts_or_upgrade_certainty(monkeypatch):
    analysis = fixture_analysis()
    word = analysis.sentences[0].words[0]
    word.morphology = present({"Number": ["Sing", "Plur"]}, "parser-fr")
    before = analysis.model_copy(deep=True)
    respond(monkeypatch, draft(proposals=[proposal("lemma", "🍵"), proposal("morphology", {"Number": ["Plur", "Sing"]})]))
    enrichment.enrich(analysis)
    assert not analysis.disagreements
    assert analysis.sentences == before.sentences


def test_unknown_parser_value_stays_unknown(monkeypatch):
    analysis = fixture_analysis()
    analysis.sentences[0].words[0].lemma = unavailable("parser missing")
    respond(monkeypatch, draft(proposals=[proposal("lemma", "茶")]))
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    conflict = analysis.disagreements[0]
    assert conflict.resolution == "unresolved"
    assert conflict.selected.status == "unknown" and conflict.selected.value is None
    assert conflict.alternatives[0].certainty == "tentative"


@pytest.mark.parametrize("changes", [
    {"translations": []}, {"glosses": []},
    {"translations": [], "glosses": [], "constructions": [], "proposals": []},
])
def test_missing_content_is_incomplete_not_success(monkeypatch, changes):
    analysis = fixture_analysis()
    calls = respond(monkeypatch, draft(**changes))
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert calls == [False]
    assert analysis.status == "partial"
    assert analysis.warnings[-1].code == "ENRICHMENT_INCOMPLETE"


@pytest.mark.parametrize("word_ids", [["w1", "w1"], [], ["missing"]])
def test_invalid_construction_scope_rejected(monkeypatch, word_ids):
    analysis = fixture_analysis()
    respond(monkeypatch, draft(constructions=[{"sentence_id": "s1", "word_ids": word_ids, "text": "說明"}]))
    enrichment.enrich(analysis)
    assert not analysis.annotations
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


def test_duplicate_construction_scopes_rejected_even_if_order_differs(monkeypatch):
    analysis = fixture_analysis()
    respond(monkeypatch, draft(constructions=[
        {"sentence_id": "s1", "word_ids": ["w1", "w2"], "text": "說明一"},
        {"sentence_id": "s1", "word_ids": ["w2", "w1"], "text": "說明二"},
    ]))
    enrichment.enrich(analysis)
    assert not analysis.annotations
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


def two_sentences():
    analysis = fixture_analysis()
    sentence = analysis.sentences[0].model_copy(deep=True)
    offset = len(analysis.input.text) + 1
    analysis.input.text += " " + analysis.input.text
    sentence.id = "s2"
    sentence.span.start += offset
    sentence.span.end += offset
    for segment in sentence.segments:
        segment.id += "b"
        segment.word_ids = [wid + "b" for wid in segment.word_ids]
        segment.span.start += offset
        segment.span.end += offset
    for word in sentence.words:
        word.id += "b"
        word.segment_id += "b"
        if word.span:
            word.span.start += offset
            word.span.end += offset
        if word.dependency.value.head_word_id:
            word.dependency.value.head_word_id += "b"
    analysis.sentences.append(sentence)
    validate_analysis(analysis)
    return analysis


@pytest.mark.parametrize("changes", [
    {"constructions": [{"sentence_id": "s1", "word_ids": ["w1b"], "text": "跨句"}]},
    {"proposals": [proposal("dependency", {"head_word_id": "w1b", "relation": "obj"})]},
    {"translations": [{"sentence_id": "absent", "text": "錯誤"}]},
    {"translations": [{"sentence_id": "s1", "text": "一"}, {"sentence_id": "s1", "text": "二"}]},
    {"glosses": [{"word_id": "w1", "text": "one"}, {"word_id": "w1", "text": "two"}]},
    {"proposals": [proposal("lemma", "茶"), proposal("lemma", "tea")]},
    {"proposals": [{**proposal("lemma", "茶"), "rationale": "  "}]},
    {"translations": [{"sentence_id": "s1", "text": "  "}]},
    {"glosses": [{"word_id": "w1", "text": "  "}]},
    {"constructions": [{"sentence_id": "s1", "word_ids": ["w1"], "text": "  "}]},
    {"constructions": [{"sentence_id": "s1", "word_ids": ids, "text": "note"}
                       for ids in (["w1"], ["w2"], ["w3"], ["w1", "w2"])]},
])
def test_scope_limits_and_cross_sentence_references(monkeypatch, changes):
    analysis = two_sentences()
    calls = respond(monkeypatch, draft(**changes))
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert calls == [False, True]
    assert not analysis.annotations and not analysis.disagreements
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


@pytest.mark.parametrize("encoded", ['{"Number":["Sing"],"Number":["Plur"]}', 'NaN', '{bad json'])
def test_value_json_rejects_ambiguous_or_invalid_json(monkeypatch, encoded):
    analysis = fixture_analysis()
    respond(monkeypatch, draft(proposals=[{**proposal("morphology", {}), "value_json": encoded}]))
    enrichment.enrich(analysis)
    assert not analysis.disagreements and not analysis.annotations
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


@pytest.mark.parametrize("field,value", [("lemma", "茶"), ("upos", "NOUN"),
                                        ("morphology", {"Number": ["Sing"]}),
                                        ("dependency", {"head_word_id": "w3", "relation": "nsubj"})])
@pytest.mark.parametrize("language", ["fr", "ja"])
def test_valid_alternatives_survive_json_round_trip(monkeypatch, field, value, language):
    analysis = fixture_analysis(language=language)
    original = getattr(analysis.sentences[0].words[0], field).model_copy(deep=True)
    respond(monkeypatch, draft(proposals=[proposal(field, value)]))
    enrichment.enrich(analysis)
    restored = Analysis.model_validate_json(analysis.model_dump_json())
    validate_analysis(restored)
    conflict = restored.disagreements[0]
    assert conflict.selected.model_dump()["value"] == original.model_dump()["value"]
    assert conflict.selected.certainty == "tentative"
    assert conflict.selected.source_ids == original.source_ids
    assert conflict.resolution == "retain_parser"


@pytest.mark.parametrize("mutation", ["value", "certainty", "resolution", "id", "source", "duplicate_target"])
def test_final_validator_defends_disagreement_contract(monkeypatch, mutation):
    analysis = fixture_analysis()
    respond(monkeypatch, draft(proposals=[proposal("upos", "NOUN")]))
    enrichment.enrich(analysis)
    conflict = analysis.disagreements[0]
    if mutation == "value":
        conflict.alternatives[0].value = "MADE_UP"
    elif mutation == "certainty":
        conflict.alternatives[0].certainty = "supported"
    elif mutation == "resolution":
        conflict.resolution = "unresolved"
    elif mutation == "id":
        conflict.id = analysis.annotations[0].id
    elif mutation == "source":
        conflict.alternatives[0].source_ids = ["missing"]
    else:
        analysis.disagreements.append(conflict.model_copy(update={"id": "other"}))
    with pytest.raises(AnalysisValidationError):
        validate_analysis(analysis)


def test_nonpresent_resource_placeholder_does_not_satisfy_coverage(monkeypatch):
    analysis = fixture_analysis()
    analysis.annotations.append(Annotation(id="placeholder", kind="translation", sentence_id="s1",
                                          word_ids=[], language="zh-TW", text=unavailable("缺翻譯")))
    respond(monkeypatch, draft(translations=[]))
    enrichment.enrich(analysis)
    assert analysis.warnings[-1].code == "ENRICHMENT_INCOMPLETE"
    # A later independent request with a usable translation may replace the placeholder.
    analysis = fixture_analysis()
    analysis.annotations.append(Annotation(id="placeholder", kind="translation", sentence_id="s1",
                                          word_ids=[], language="zh-TW", text=unavailable("缺翻譯")))
    respond(monkeypatch, draft())
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert analysis.status == "complete"
    assert len([a for a in analysis.annotations if a.kind == "translation"]) == 1


def test_punctuation_does_not_require_gloss(monkeypatch):
    analysis = fixture_analysis()
    analysis.sentences[0].words[0].upos = present("PUNCT", "parser-fr")
    respond(monkeypatch, draft(glosses=[{"word_id": "w2", "text": "to"}, {"word_id": "w3", "text": "the"}]))
    enrichment.enrich(analysis)
    assert analysis.status == "complete"


def provider(monkeypatch, response_body, status=200):
    requests = []
    client_type = httpx.Client

    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(status, json=response_body)

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-secret")
    monkeypatch.setenv("WORKBENCH_LLM_MODEL", "fixture")
    monkeypatch.setattr(enrichment.httpx, "Client", lambda **kwargs:
                        client_type(transport=httpx.MockTransport(transport), **kwargs))
    return requests


def completed(candidate):
    return {"status": "completed", "model": "fixture", "id": "request-fixture", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(candidate)}]},
    ]}


@pytest.mark.parametrize("body", [
    {"status": "incomplete", "output": []},
    {"status": "failed", "output": []},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "declined"}]}]},
    {"status": "completed", "output": None},
    {"status": "completed", "output": [None]},
    {"status": "completed", "output": [{"type": "message", "content": None}]},
    {"status": "completed", "output": [{"type": "message", "content": [None]}]},
    [],
])
def test_unavailable_provider_output_is_not_retried(monkeypatch, body):
    calls = provider(monkeypatch, body)
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert len(calls) == 1
    assert not analysis.annotations and len(analysis.sources) == 1
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_errors_do_not_retry(monkeypatch, status):
    calls = provider(monkeypatch, {"error": "private detail"}, status)
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert len(calls) == 1
    assert "private detail" not in analysis.model_dump_json()
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


@pytest.mark.parametrize("extra", ["sources", "extensions", "capabilities", "status", "sentences"])
def test_provider_cannot_inject_core_fields(monkeypatch, extra):
    data = draft().model_dump()
    data[extra] = []
    calls = provider(monkeypatch, completed(data))
    analysis = fixture_analysis()
    enrichment.enrich(analysis)
    assert len(calls) == 2
    assert not analysis.annotations
    assert analysis.warnings[-1].code == "ENRICHMENT_FAILED"


def test_provider_schema_prompt_and_local_models_stay_aligned(monkeypatch):
    calls = provider(monkeypatch, completed(draft().model_dump()))
    analysis = fixture_analysis()
    add_resources(analysis)
    enrichment.enrich(analysis)
    assert analysis.status == "complete"
    assert len(calls) == 1 and calls[0]["store"] is False
    schema = calls[0]["text"]["format"]["schema"]
    assert schema == enrichment.ReadingDraft.model_json_schema()
    assert schema["properties"]["proposals"]["maxItems"] == 0
    assert schema["properties"]["constructions"]["maxItems"] == 0
    for model in [schema, *schema["$defs"].values()]:
        assert model["additionalProperties"] is False
        assert set(model["required"]) == set(model["properties"])
    prompt = json.loads(calls[0]["input"])
    assert len(prompt["existing_annotations"]) == 3
    assert "candidate_vocabulary" not in prompt
    assert "繁體中文" in calls[0]["instructions"]
    assert "test-only-not-a-secret" not in json.dumps(calls[0])


def test_proposal_field_enum_rejects_unsupported_fields():
    with pytest.raises(ValidationError):
        draft(proposals=[proposal("extensions", {})])


@pytest.mark.parametrize("mode", ["off", "on"])
def test_api_bad_candidate_returns_valid_parser_instead_of_500(monkeypatch, mode):
    from types import SimpleNamespace
    from workbench import pipeline
    from workbench.adapters.base import Parsed
    from workbench.api import app

    original = fixture_analysis()
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("WORKBENCH_LLM_MODEL", "fixture")
    monkeypatch.setattr(pipeline, "ready", lambda language: True)
    monkeypatch.setattr(pipeline, "get_adapter", lambda language: SimpleNamespace(
        parse=lambda text: Parsed(original.sentences, original.sources, [])))
    monkeypatch.setattr(app.state, "runtime", SimpleNamespace(analyze=pipeline.analyze), raising=False)
    calls = respond(monkeypatch, draft(proposals=[proposal("upos", "INVALID")]))
    response = TestClient(app).post("/api/v0.1/analyses", json={"text": "🍵 au", "language": "fr", "enrichment": mode})
    assert response.status_code == 200
    result = Analysis.model_validate(response.json())
    validate_analysis(result)
    assert result.sentences == original.sentences
    assert calls == ([False, True] if mode == "on" else [])
    assert result.status == ("partial" if mode == "on" else "complete")
