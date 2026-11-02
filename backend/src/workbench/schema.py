"""Normalized result types shared by every language adapter."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldStatus(StrEnum):
    present = "present"
    unknown = "unknown"
    not_applicable = "not_applicable"
    unsupported = "unsupported"


T = TypeVar("T")
UPOS = Literal[
    "ADJ", "ADP", "ADV", "AUX", "CCONJ", "DET", "INTJ", "NOUN", "NUM",
    "PART", "PRON", "PROPN", "PUNCT", "SCONJ", "SYM", "VERB", "X",
]


class AnalysisField(StrictModel, Generic[T]):
    status: FieldStatus
    value: T | None
    source_ids: list[str]
    certainty: Literal["unassessed", "tentative", "supported"]
    reason: str | None

    @model_validator(mode="after")
    def check_state(self):
        if self.status == FieldStatus.present:
            if self.value is None or not self.source_ids or self.reason is not None:
                raise ValueError("present field requires a value and source, without reason")
        elif self.value is not None or not self.reason or self.certainty != "unassessed":
            raise ValueError("non-present field requires null value, reason and unassessed certainty")
        return self


class Span(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("span end must exceed start")
        return self


class Input(StrictModel):
    text: str
    language: Literal["fr", "ja", "en"]


class Capability(StrictModel):
    name: str
    support: Literal["available", "limited", "unsupported"]
    reason: str | None

    @model_validator(mode="after")
    def check_reason(self):
        if self.support != "available" and not self.reason:
            raise ValueError("limited or unsupported capability needs a reason")
        return self


class Source(StrictModel):
    id: str
    kind: Literal["parser", "resource", "rule", "llm"]
    name: str
    version: str
    locator: str | None
    metadata: dict[str, str]


class Segment(StrictModel):
    id: str
    span: Span
    surface: str
    word_ids: list[str]


class Dependency(StrictModel):
    head_word_id: str | None
    relation: str


class Word(StrictModel):
    id: str
    segment_id: str
    form: str
    span: Span | None
    lemma: AnalysisField[str]
    upos: AnalysisField[UPOS]
    xpos: AnalysisField[str]
    morphology: AnalysisField[dict[str, list[str]]]
    dependency: AnalysisField[Dependency]
    extensions: dict[str, Any]


class Sentence(StrictModel):
    id: str
    span: Span
    segments: list[Segment]
    words: list[Word]
    dependency_status: Literal["complete", "partial", "unavailable"]


class Annotation(StrictModel):
    id: str
    kind: Literal["lexical_gloss", "translation", "construction"]
    sentence_id: str
    word_ids: list[str]
    language: Literal["en", "zh-TW"]
    text: AnalysisField[str]


class Disagreement(StrictModel):
    id: str
    word_id: str
    field: Literal["lemma", "upos", "morphology", "dependency"]
    selected: AnalysisField[Any]
    alternatives: list[AnalysisField[Any]]
    resolution: Literal["retain_parser", "unresolved"]
    reason: str


class Diagnostic(StrictModel):
    reason: Literal["connection", "timeout", "authentication", "busy", "http", "truncated", "refused",
                    "format", "candidate", "graph", "configuration", "provider", "worker", "skipped"]
    detail: str
    retryable: bool


class Warning(StrictModel):
    code: str
    message: str
    sentence_id: str | None
    word_ids: list[str]
    source_ids: list[str]
    diagnostic: Diagnostic | None = None


class Analysis(StrictModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    analysis_id: str
    input: Input
    status: Literal["complete", "partial"]
    capabilities: list[Capability]
    sentences: list[Sentence]
    sources: list[Source]
    annotations: list[Annotation]
    disagreements: list[Disagreement]
    warnings: list[Warning]


def present(value: T, source_id: str, certainty: str = "unassessed") -> AnalysisField[T]:
    return AnalysisField(status="present", value=value, source_ids=[source_id], certainty=certainty, reason=None)


def unavailable(reason: str, status: FieldStatus = FieldStatus.unknown) -> AnalysisField[Any]:
    return AnalysisField(status=status, value=None, source_ids=[], certainty="unassessed", reason=reason)
