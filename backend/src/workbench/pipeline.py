"""Small synchronous analysis pipeline. Parser output is always validated before display."""

from __future__ import annotations

import importlib.util
import os
from uuid import uuid4
from collections.abc import Callable

from workbench.adapters.french import FrenchAdapter, model_files_available
from workbench.adapters.spacy_bridge import SpacyAdapter
from workbench.schema import Analysis, Capability, FieldStatus, Input, Warning, unavailable
from workbench.resources import lookup_resources
from workbench.validation import AnalysisValidationError, validate_analysis
from workbench.llm_config import configured_llm


class AdapterUnavailable(RuntimeError):
    pass


class ParserInvalid(RuntimeError):
    pass


class TooManySentences(ValueError):
    pass


_BASE_CAPABILITIES = ("segmentation", "lemma", "upos", "morphology", "dependency")
_ENRICHMENT_CAPABILITIES = ("lexical_gloss", "translation", "construction")


def capabilities(language: str) -> list[Capability]:
    model_ready = ready(language)
    result = [Capability(name=name, support="available" if model_ready else "unsupported",
                         reason=None if model_ready else "模型尚未安裝") for name in _BASE_CAPABILITIES]
    for name in _ENRICHMENT_CAPABILITIES:
        support = "unsupported" if language == "en" else "limited"
        reason = "英文擴充僅支援 parser" if language == "en" else "需要已設定的 LLM provider"
        if name == "construction" and language != "en":
            reason = "僅顯示既有資源註解；自動模型構式補充暫停"
        result.append(Capability(name=name, support=support, reason=reason))
    if language == "fr":
        result.append(Capability(name="fr.clitic_type", support="limited", reason="僅部分形式可辨識"))
    if language == "ja":
        for name in ("ja.reading", "ja.normalized_form", "ja.conjugation"):
            result.append(Capability(name=name, support="limited", reason="依 parser 輸出而定"))
    return result


def ready(language: str) -> bool:
    if language == "fr":
        if os.getenv("WORKBENCH_FRENCH_PARSER", "stanza") == "stanza":
            return importlib.util.find_spec("stanza") is not None and model_files_available()
        return importlib.util.find_spec("fr_core_news_sm") is not None
    if language == "ja":
        return importlib.util.find_spec("ja_ginza") is not None
    if language == "en":
        return importlib.util.find_spec("en_core_web_sm") is not None
    return False


def get_adapter(language: str):
    if language == "fr":
        if os.getenv("WORKBENCH_FRENCH_PARSER", "stanza") == "stanza":
            return FrenchAdapter()
        return SpacyAdapter("fr", "fr_core_news_sm")
    if language == "ja":
        return SpacyAdapter("ja", "ja_ginza")
    if language == "en":
        return SpacyAdapter("en", "en_core_web_sm")
    raise AdapterUnavailable("不支援的語言")


def _repair_dependency_graph(parsed) -> None:
    """Keep trustworthy segmentation when a model returns an unusable tree."""
    for sentence in parsed.sentences:
        ids = {word.id for word in sentence.words}
        edges: dict[str, str] = {}
        roots = 0
        invalid = False
        for word in sentence.words:
            dep = word.dependency
            if dep.status != FieldStatus.present:
                continue
            value = dep.value
            if value.head_word_id is None:
                roots += 1
                invalid |= value.relation != "root"
            else:
                invalid |= value.head_word_id not in ids or value.head_word_id == word.id or value.relation == "root"
                edges[word.id] = value.head_word_id
        invalid |= roots > 1
        for start in edges:
            seen = set()
            current = start
            while current in edges:
                if current in seen:
                    invalid = True
                    break
                seen.add(current)
                current = edges[current]
        if invalid:
            for word in sentence.words:
                word.dependency = unavailable("parser 的依存圖無效")
            sentence.dependency_status = "unavailable"
            parsed.warnings.append(Warning(
                code="DEPENDENCY_INCOMPLETE", message="這句的依存分析無效，已保留其他分析資訊。",
                sentence_id=sentence.id, word_ids=[], source_ids=[parsed.sources[0].id],
            ))


def analyze(text: str, language: str, enrichment: str,
            checkpoint: Callable[[str, Analysis], None] | None = None) -> Analysis:
    """Run inside a supervised worker in the HTTP service; direct use is for spikes/tests."""
    if not ready(language):
        raise AdapterUnavailable(f"{language} 模型尚未安裝")
    try:
        parsed = get_adapter(language).parse(text)
    except (ImportError, OSError) as exc:
        raise AdapterUnavailable(f"{language} 模型無法載入") from exc
    except Exception as exc:
        raise ParserInvalid("parser 無法分析輸入") from exc
    if len(parsed.sentences) > 5:
        raise TooManySentences("最多支援五句")
    if not parsed.sentences:
        raise ParserInvalid("parser 沒有產生可用句子")
    _repair_dependency_graph(parsed)
    partial = any(
        sentence.dependency_status != "complete" or
        any(word.lemma.status != FieldStatus.present or word.upos.status != FieldStatus.present
            or word.morphology.status != FieldStatus.present for word in sentence.words)
        for sentence in parsed.sentences
    )
    analysis = Analysis(
        analysis_id=str(uuid4()), input=Input(text=text, language=language),
        status="partial" if partial else "complete", capabilities=capabilities(language),
        sentences=parsed.sentences, sources=parsed.sources, annotations=[],
        disagreements=[], warnings=parsed.warnings.copy(),
    )
    try:
        validate_analysis(analysis)
    except AnalysisValidationError as exc:
        raise ParserInvalid(str(exc)) from exc
    if checkpoint:
        checkpoint("resources", analysis)
    if language != "en":
        # The parser result is a rollback point, including when lookup partly mutates then fails.
        candidate = analysis.model_copy(deep=True)
        try:
            lookup_resources(candidate)
            validate_analysis(candidate)
            if (candidate.input != analysis.input or candidate.sentences != analysis.sentences or
                    candidate.disagreements != analysis.disagreements or
                    candidate.capabilities != analysis.capabilities or
                    candidate.sources[:len(analysis.sources)] != analysis.sources):
                raise AnalysisValidationError("resources changed the parser core")
        except Exception:
            analysis.status = "partial"
            analysis.warnings.append(Warning(
                code="RESOURCE_UNAVAILABLE", message="語言資源暫時無法使用，已保留基本分析。",
                sentence_id=None, word_ids=[], source_ids=[],
            ))
        else:
            analysis = candidate
    if enrichment == "on":
        if checkpoint:
            checkpoint("enrichment", analysis)
        llm = configured_llm()
        if llm is None or not llm.available:
            analysis.status = "partial"
            from workbench.diagnostics import enrichment_warning
            analysis.warnings.append(enrichment_warning("configuration"))
        else:
            from workbench.enrichment import enrich
            enrich(analysis)
    validate_analysis(analysis)
    return analysis
