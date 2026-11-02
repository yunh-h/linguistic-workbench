import pytest
from fastapi.testclient import TestClient

from workbench.adapters.base import Parsed
from workbench.api import app
from workbench.schema import Analysis, Dependency, Input, Segment, Sentence, Source, Span, Word, present, unavailable
from workbench.validation import AnalysisValidationError, validate_analysis


SOURCE = Source(id="parser-fr", kind="parser", name="fixture", version="1", locator=None, metadata={})


@pytest.fixture(autouse=True)
def inline_runtime(monkeypatch):
    """API/core contracts avoid real processes/models; runtime tests cover supervision."""
    import workbench.pipeline as pipeline
    from types import SimpleNamespace

    monkeypatch.setattr(app.state, "runtime", SimpleNamespace(
        refresh=lambda: None,
        ready=lambda language: pipeline.ready(language),
        analyze=lambda text, language, enrichment: pipeline.analyze(text, language, enrichment),
    ), raising=False)


def fixture_analysis(text="🍵 au", language="fr"):
    offset = len(text) - 2
    words = [
        Word(id="w1", segment_id="g1", form="🍵", span=Span(start=0, end=1),
             lemma=present("🍵", SOURCE.id), upos=present("SYM", SOURCE.id),
             xpos=unavailable("無資料"), morphology=present({}, SOURCE.id),
             dependency=present(Dependency(head_word_id="w2", relation="dep"), SOURCE.id), extensions={}),
        Word(id="w2", segment_id="g2", form="à", span=None,
             lemma=present("à", SOURCE.id), upos=present("ADP", SOURCE.id),
             xpos=unavailable("無資料"), morphology=present({}, SOURCE.id),
             dependency=present(Dependency(head_word_id="w3", relation="case"), SOURCE.id), extensions={}),
        Word(id="w3", segment_id="g2", form="le", span=None,
             lemma=present("le", SOURCE.id), upos=present("DET", SOURCE.id),
             xpos=unavailable("無資料"), morphology=present({}, SOURCE.id),
             dependency=present(Dependency(head_word_id=None, relation="root"), SOURCE.id), extensions={}),
    ]
    return Analysis(
        analysis_id="a1", input=Input(text=text, language=language), status="complete", capabilities=[],
        sentences=[Sentence(id="s1", span=Span(start=0, end=len(text)),
                            segments=[Segment(id="g1", span=Span(start=0, end=1), surface="🍵", word_ids=["w1"]),
                                      Segment(id="g2", span=Span(start=offset, end=len(text)), surface="au", word_ids=["w2", "w3"])],
                            words=words, dependency_status="complete")],
        sources=[SOURCE], annotations=[], disagreements=[], warnings=[],
    )


def test_surface_segment_can_have_multiple_analytic_words():
    analysis = fixture_analysis()
    validate_analysis(analysis)
    assert analysis.sentences[0].segments[1].word_ids == ["w2", "w3"]


@pytest.mark.parametrize("mutation", ["bad_offset", "missing_word", "cycle", "bad_source"])
def test_invalid_cross_references_are_rejected(mutation):
    analysis = fixture_analysis()
    sentence = analysis.sentences[0]
    if mutation == "bad_offset":
        sentence.segments[1].span.start = 1
    elif mutation == "missing_word":
        sentence.segments[1].word_ids = ["absent"]
    elif mutation == "cycle":
        sentence.words[2].dependency = present(Dependency(head_word_id="w1", relation="dep"), SOURCE.id)
        sentence.dependency_status = "partial"
    else:
        sentence.words[0].lemma.source_ids = ["invented"]
    with pytest.raises(AnalysisValidationError):
        validate_analysis(analysis)


def test_api_returns_parser_only_result(monkeypatch):
    import workbench.pipeline as pipeline

    class FakeAdapter:
        def parse(self, text):
            value = fixture_analysis(text)
            return Parsed(value.sentences, value.sources, [])

    monkeypatch.setattr(pipeline, "ready", lambda language: True)
    monkeypatch.setattr(pipeline, "get_adapter", lambda language: FakeAdapter())
    response = TestClient(app).post("/api/v0.1/analyses", json={"text": "🍵 au", "language": "fr", "enrichment": "off"})
    assert response.status_code == 200
    assert response.json()["status"] == "complete"


def test_invalid_parser_tree_downgrades_without_losing_words(monkeypatch):
    import workbench.pipeline as pipeline

    class FakeAdapter:
        def parse(self, text):
            value = fixture_analysis(text)
            value.sentences[0].words[0].dependency = present(Dependency(head_word_id="missing", relation="dep"), SOURCE.id)
            return Parsed(value.sentences, value.sources, [])

    monkeypatch.setattr(pipeline, "ready", lambda language: True)
    monkeypatch.setattr(pipeline, "get_adapter", lambda language: FakeAdapter())
    response = TestClient(app).post("/api/v0.1/analyses", json={"text": "🍵 au", "language": "fr", "enrichment": "off"})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "partial"
    assert result["sentences"][0]["dependency_status"] == "unavailable"
    assert len(result["sentences"][0]["words"]) == 3


def test_english_rejects_enrichment():
    response = TestClient(app).post("/api/v0.1/analyses", json={"text": "Hello.", "language": "en", "enrichment": "on"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_ENRICHMENT"


def test_stanza_package_without_models_is_unavailable(monkeypatch, tmp_path):
    import workbench.pipeline as pipeline

    monkeypatch.setenv("WORKBENCH_FRENCH_PARSER", "stanza")
    monkeypatch.setenv("WORKBENCH_STANZA_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(pipeline, "get_adapter", lambda language: pytest.fail("must not load missing models"))
    client = TestClient(app)
    capabilities = client.get("/api/v0.1/capabilities").json()
    assert not next(item for item in capabilities["languages"] if item["language"] == "fr")["ready"]
    response = client.post("/api/v0.1/analyses", json={
        "text": "Je vais au marché.", "language": "fr", "enrichment": "off",
    })
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ADAPTER_UNAVAILABLE"


def test_frontend_assets_load_from_app_and_file_paths():
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert 'href="./style.css"' in page.text
    assert 'src="./app.js"' in page.text
    assert client.get("/style.css").status_code == 200
    assert client.get("/app.js").status_code == 200


def test_enrichment_keeps_parser_value_and_records_conflict(monkeypatch):
    import workbench.enrichment as enrichment

    analysis = fixture_analysis()
    draft = enrichment.EnrichmentDraft.model_validate({
        "translations": [{"sentence_id": "s1", "text": "一杯茶"}],
        "glosses": [{"word_id": "w1", "text": "tea"}, {"word_id": "w2", "text": "to"},
                    {"word_id": "w3", "text": "the"}],
        "constructions": [],
        "proposals": [{"word_id": "w2", "field": "lemma", "value_json": '"de"', "rationale": "測試分歧"}],
    })
    monkeypatch.setattr(enrichment, "_request", lambda analysis, repair=False: (draft, {"model": "fixture", "id": "r1"}))
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert analysis.sentences[0].words[1].lemma.value == "à"
    assert analysis.disagreements[0].alternatives[0].value == "de"


def test_enrichment_rejects_unknown_word_id(monkeypatch):
    import workbench.enrichment as enrichment

    analysis = fixture_analysis()
    draft = enrichment.EnrichmentDraft.model_validate({
        "translations": [], "glosses": [{"word_id": "invented", "text": "wrong"}],
        "constructions": [], "proposals": [],
    })
    monkeypatch.setattr(enrichment, "_request", lambda analysis, repair=False: (draft, {"model": "fixture"}))
    enrichment.enrich(analysis)
    validate_analysis(analysis)
    assert analysis.status == "partial"
    assert analysis.annotations == []
    assert analysis.warnings[0].code == "ENRICHMENT_FAILED"
