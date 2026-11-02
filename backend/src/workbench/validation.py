"""Cross-field checks that JSON Schema cannot express."""

from __future__ import annotations

from workbench.schema import Analysis, FieldStatus
from workbench.candidates import validate_dependency_overlay, validate_value, values_equal


class AnalysisValidationError(ValueError):
    pass


def _fail(message: str) -> None:
    raise AnalysisValidationError(message)


def validate_analysis(analysis: Analysis) -> None:
    text = analysis.input.text
    if not analysis.sentences:
        _fail("analysis has no sentences")
    source_ids = {source.id for source in analysis.sources}
    if len(source_ids) != len(analysis.sources) or not source_ids or any(not sid for sid in source_ids):
        _fail("source IDs must be unique and nonempty")
    if not analysis.analysis_id or analysis.analysis_id in source_ids:
        _fail("invalid analysis ID")
    all_ids: set[str] = source_ids | {analysis.analysis_id}
    sentence_words: dict[str, set[str]] = {}
    word_by_id = {}
    last_sentence_end = -1

    for sentence in analysis.sentences:
        if sentence.id in all_ids or not sentence.id:
            _fail("duplicate or empty sentence ID")
        all_ids.add(sentence.id)
        if sentence.span.end > len(text) or sentence.span.start < last_sentence_end:
            _fail("sentence span outside input or out of order")
        last_sentence_end = sentence.span.end
        if not sentence.segments or not sentence.words:
            _fail("sentence needs segments and words")
        segments = {}
        last_end = sentence.span.start
        for segment in sentence.segments:
            if not segment.id or segment.id in all_ids:
                _fail("duplicate or empty segment ID")
            all_ids.add(segment.id)
            if not segment.word_ids or len(set(segment.word_ids)) != len(segment.word_ids):
                _fail("segment must point to distinct words")
            if not (sentence.span.start <= segment.span.start < segment.span.end <= sentence.span.end):
                _fail("segment outside sentence")
            if segment.span.start < last_end or text[last_end:segment.span.start].strip():
                _fail("overlapping segments or uncovered non-whitespace input")
            if text[segment.span.start:segment.span.end] != segment.surface:
                _fail("segment does not match original text")
            last_end = segment.span.end
            segments[segment.id] = segment
        if text[last_end:sentence.span.end].strip():
            _fail("uncovered text at end of sentence")

        local_words = {}
        for word in sentence.words:
            if not word.id or word.id in all_ids:
                _fail("duplicate or empty word ID")
            all_ids.add(word.id)
            if word.segment_id not in segments:
                _fail("word points to missing segment")
            segment = segments[word.segment_id]
            if word.id not in segment.word_ids:
                _fail("word not listed by its segment")
            if word.span and (word.span.start < segment.span.start or word.span.end > segment.span.end
                              or text[word.span.start:word.span.end] != word.form):
                _fail("word span does not match original text")
            if any(key != analysis.input.language for key in word.extensions):
                _fail("extension namespace does not match language")
            if analysis.input.language == "en" and word.extensions:
                _fail("English parser-only pack has no extensions")
            for field_name in ("lemma", "upos", "xpos", "morphology", "dependency"):
                field = getattr(word, field_name)
                if not set(field.source_ids) <= source_ids:
                    _fail("word field refers to missing source")
            for extension in word.extensions.values():
                if isinstance(extension, dict):
                    for value in extension.values():
                        if isinstance(value, dict) and not set(value.get("source_ids", [])) <= source_ids:
                            _fail("extension refers to missing source")
            local_words[word.id] = word
            word_by_id[word.id] = word
        for segment in sentence.segments:
            if set(segment.word_ids) != {word.id for word in sentence.words if word.segment_id == segment.id}:
                _fail("segment/word membership mismatch")
        sentence_words[sentence.id] = set(local_words)

        edges = {}
        root_count = 0
        present_count = 0
        for word in sentence.words:
            dep = word.dependency
            if dep.status != FieldStatus.present:
                continue
            present_count += 1
            value = dep.value
            assert value is not None
            if value.head_word_id is None:
                root_count += 1
                if value.relation != "root":
                    _fail("null head must be root")
            else:
                if value.head_word_id not in local_words or value.head_word_id == word.id or value.relation == "root":
                    _fail("invalid dependency head or relation")
                edges[word.id] = value.head_word_id
        if root_count > 1:
            _fail("more than one root")
        for start in edges:
            seen = set()
            at = start
            while at in edges:
                if at in seen:
                    _fail("dependency cycle")
                seen.add(at)
                at = edges[at]
        complete = present_count == len(local_words) and root_count == 1
        if sentence.dependency_status == "complete" and not complete:
            _fail("incomplete dependency marked complete")
        if sentence.dependency_status == "unavailable" and present_count:
            _fail("unavailable dependency has edges")
        if sentence.dependency_status == "partial" and complete:
            _fail("complete dependency marked partial")

    if text[:analysis.sentences[0].span.start].strip() or text[last_sentence_end:].strip():
        _fail("uncovered non-whitespace input outside sentences")
    for left, right in zip(analysis.sentences, analysis.sentences[1:]):
        if text[left.span.end:right.span.start].strip():
            _fail("uncovered non-whitespace input between sentences")

    annotation_scopes = set()
    for annotation in analysis.annotations:
        if annotation.id in all_ids or not annotation.id:
            _fail("duplicate or empty annotation ID")
        all_ids.add(annotation.id)
        if annotation.sentence_id not in sentence_words or not set(annotation.word_ids) <= sentence_words[annotation.sentence_id]:
            _fail("annotation points outside sentence")
        if annotation.kind == "lexical_gloss" and (len(annotation.word_ids) != 1 or annotation.language != "en"):
            _fail("invalid lexical gloss scope or language")
        if annotation.kind == "translation" and (annotation.word_ids or annotation.language != "zh-TW"):
            _fail("invalid translation scope or language")
        if annotation.kind == "construction" and (not annotation.word_ids or annotation.language != "zh-TW"):
            _fail("invalid construction scope or language")
        scope = (annotation.kind, annotation.sentence_id, frozenset(annotation.word_ids))
        if len(annotation.word_ids) != len(set(annotation.word_ids)) or scope in annotation_scopes:
            _fail("duplicate annotation scope or word")
        annotation_scopes.add(scope)
        if annotation.text.status == FieldStatus.present and not annotation.text.value.strip():
            _fail("empty annotation text")
        if not set(annotation.text.source_ids) <= source_ids:
            _fail("annotation refers to missing source")
    if analysis.input.language == "en" and analysis.annotations:
        _fail("English parser-only result has annotations")
    disagreement_targets = set()
    dependency_alternatives = {}
    word_sentence = {wid: sid for sid, words in sentence_words.items() for wid in words}
    for disagreement in analysis.disagreements:
        if not disagreement.id or disagreement.id in all_ids or not disagreement.reason.strip():
            _fail("invalid disagreement ID or reason")
        all_ids.add(disagreement.id)
        if disagreement.word_id not in word_by_id or not disagreement.alternatives:
            _fail("invalid disagreement target")
        target = (disagreement.word_id, disagreement.field)
        if target in disagreement_targets:
            _fail("duplicate disagreement target")
        disagreement_targets.add(target)
        selected = getattr(word_by_id[disagreement.word_id], disagreement.field)
        # Any-valued fields deserialize nested Dependency as a dict, not a model.
        if disagreement.selected.model_dump() != selected.model_dump():
            _fail("disagreement selected value differs from word")
        expected_resolution = "retain_parser" if selected.status == FieldStatus.present else "unresolved"
        if disagreement.resolution != expected_resolution:
            _fail("disagreement resolution differs from selected state")
        if selected.status == FieldStatus.present and selected.certainty != "tentative":
            _fail("conflicting selected value must be tentative")
        for candidate in [disagreement.selected, *disagreement.alternatives]:
            if not set(candidate.source_ids) <= source_ids:
                _fail("disagreement refers to missing source")
        for alternative in disagreement.alternatives:
            if alternative.status != FieldStatus.present or alternative.certainty != "tentative":
                _fail("alternative must be present and tentative")
            try:
                value = validate_value(disagreement.field, alternative.value, analysis.input.language,
                                       disagreement.word_id, sentence_words[word_sentence[disagreement.word_id]])
            except ValueError as exc:
                raise AnalysisValidationError("invalid disagreement alternative") from exc
            if values_equal(disagreement.field, value, selected.value):
                _fail("alternative repeats selected value")
            if disagreement.field == "dependency" and len(disagreement.alternatives) == 1:
                dependency_alternatives[disagreement.word_id] = value
    try:
        validate_dependency_overlay(analysis, dependency_alternatives)
    except ValueError as exc:
        raise AnalysisValidationError("invalid disagreement dependency graph") from exc
    for warning in analysis.warnings:
        if warning.sentence_id is not None and warning.sentence_id not in sentence_words:
            _fail("warning refers to missing sentence")
        if not set(warning.word_ids) <= set(word_by_id) or not set(warning.source_ids) <= source_ids:
            _fail("warning refers to missing word or source")
