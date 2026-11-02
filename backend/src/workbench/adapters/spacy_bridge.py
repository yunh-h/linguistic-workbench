"""GiNZA and optional English spaCy model to normalized core data."""

from __future__ import annotations

from functools import lru_cache

from workbench.adapters.base import Parsed
from workbench.schema import (
    Dependency, FieldStatus, Segment, Sentence, Source, Span, Warning, Word, present, unavailable,
)


@lru_cache(maxsize=2)
def _model(name: str):
    import spacy

    return spacy.load(name)


class SpacyAdapter:
    def __init__(self, language: str, model_name: str):
        self.language = language
        self.model_name = model_name

    def load(self) -> None:
        _model(self.model_name)

    def parse(self, text: str) -> Parsed:
        import spacy

        nlp = _model(self.model_name)
        doc = nlp(text)
        source_id = f"parser-{self.language}"
        source = Source(
            id=source_id, kind="parser", name=self.model_name,
            version=str(nlp.meta.get("version", "unavailable")), locator=None,
            metadata={"framework": "spaCy", "package_version": spacy.__version__},
        )
        sources = [source]
        if self.language == "fr":
            sources.append(Source(id="rule-fr-clitic", kind="rule", name="French en/y form marker",
                                  version="0.1.0", locator=None, metadata={"scope": "form only"}))
        sentences: list[Sentence] = []
        for si, sent in enumerate(doc.sents, 1):
            tokens = [token for token in sent if not token.is_space]
            if not tokens:
                continue
            sid = f"s{si}"
            segments: list[Segment] = []
            words: list[Word] = []
            index_to_word = {token.i: f"{sid}w{ti}" for ti, token in enumerate(tokens, 1)}
            for ti, token in enumerate(tokens, 1):
                wid = f"{sid}w{ti}"
                gid = f"{sid}g{ti}"
                span = Span(start=token.idx, end=token.idx + len(token.text))
                segments.append(Segment(id=gid, span=span, surface=token.text, word_ids=[wid]))
                raw_feats = token.morph.to_dict()
                feats = {key: values.split(",") for key, values in raw_feats.items()
                         if self.language != "ja" or key not in {"Reading", "Inflection"}}
                if token.dep_:
                    if token.head.i == token.i:
                        dep = present(Dependency(head_word_id=None, relation="root"), source_id)
                    elif token.head.i in index_to_word:
                        head_id = index_to_word[token.head.i]
                        dep = present(Dependency(head_word_id=head_id, relation=token.dep_.lower()), source_id)
                    else:
                        dep = unavailable("parser 指向句外節點")
                else:
                    dep = unavailable("parser 未提供 dependency")
                extensions = {}
                if self.language == "fr" and token.pos_ == "PRON" and token.text.lower() in {"en", "y"}:
                    extensions = {"fr": {"clitic_type": present(token.text.lower(), "rule-fr-clitic").model_dump()}}
                if self.language == "ja":
                    inflection = raw_feats.get("Inflection", "")
                    conjugation = inflection.split(";", 1) if inflection else []
                    empty = unavailable("此詞沒有活用資訊", FieldStatus.not_applicable)
                    extensions = {"ja": {
                        "reading": (present(raw_feats["Reading"], source_id) if raw_feats.get("Reading") and token.pos_ != "PUNCT"
                                    else unavailable("此詞沒有讀音資訊", FieldStatus.not_applicable if token.pos_ == "PUNCT" else FieldStatus.unknown)).model_dump(),
                        "normalized_form": (present(token.norm_, source_id) if token.norm_
                                            else unavailable("parser 未提供正規形")).model_dump(),
                        "conjugation_type": (present(conjugation[0], source_id) if conjugation
                                             else empty).model_dump(),
                        "conjugation_form": (present(conjugation[1], source_id) if len(conjugation) > 1
                                             else empty).model_dump(),
                    }}
                words.append(Word(
                    id=wid, segment_id=gid, form=token.text, span=span,
                    lemma=present(token.lemma_, source_id) if token.lemma_ else unavailable("parser 未提供 lemma"),
                    upos=present(token.pos_, source_id) if token.pos_ else unavailable("parser 未提供 UPOS"),
                    xpos=present(token.tag_, source_id) if token.tag_ else unavailable("parser 未提供 XPOS"),
                    morphology=present(feats, source_id) if token.pos_ else unavailable("parser 未提供 morphology"),
                    dependency=dep, extensions=extensions,
                ))
            deps_complete = all(w.dependency.status == FieldStatus.present for w in words)
            sentences.append(Sentence(
                id=sid, span=Span(start=tokens[0].idx, end=tokens[-1].idx + len(tokens[-1].text)),
                segments=segments, words=words,
                dependency_status="complete" if deps_complete else "partial",
            ))
        warnings = []
        if self.language == "fr":
            warnings.append(Warning(
                code="MODEL_LIMITATION", message="法文模型對部分代名詞與複合時態可能誤判；請核對 lemma、詞性與依存關係。",
                sentence_id=None, word_ids=[], source_ids=[source_id],
            ))
        return Parsed(sentences=sentences, sources=sources, warnings=warnings)
