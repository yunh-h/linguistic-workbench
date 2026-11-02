"""Stanza French adapter, preserving multiword-token surface segments."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from workbench.adapters.base import Parsed
from workbench.schema import Dependency, FieldStatus, Segment, Sentence, Source, Span, Warning, Word, present, unavailable


RESOURCE_VERSION = "1.11.0"
PACKAGE = "combined"


def model_package() -> str:
    package = os.getenv("WORKBENCH_STANZA_PACKAGE", PACKAGE)
    if package not in {"sequoia", "combined"}:
        raise ValueError("French Stanza package must be sequoia or combined")
    return package


def model_files(package: str) -> tuple[str, ...]:
    return (f"tokenize/{package}.pt", f"mwt/{package}.pt", f"pos/{package}_charlm.pt",
            f"lemma/{package}_nocharlm.pt", f"depparse/{package}_charlm.pt", "pretrain/conll17.pt",
            "forward_charlm/newswiki.pt", "backward_charlm/newswiki.pt")


def model_directory() -> Path:
    return Path(os.getenv("WORKBENCH_STANZA_DIR") or
                os.getenv("STANZA_RESOURCES_DIR") or Path.home() / "stanza_resources")


def model_files_available() -> bool:
    directory = model_directory()
    return ((directory / "resources.json").is_file() and
            all((directory / "fr" / name).is_file() for name in model_files(model_package())))


def _model():
    if not model_files_available():
        raise FileNotFoundError("Provision the French Stanza models before starting analysis")
    return _load_model(str(model_directory()), model_package())


@lru_cache(maxsize=1)
def _load_model(directory: str, package: str):
    import stanza

    kwargs = {"lang": "fr", "package": package, "processors": "tokenize,mwt,pos,lemma,depparse",
              "resources_version": RESOURCE_VERSION, "dir": directory,
              "download_method": None, "use_gpu": False}
    return stanza.Pipeline(**kwargs)


class FrenchAdapter:
    language = "fr"
    model_name = "stanza-fr"

    def load(self) -> None:
        _model()

    def parse(self, text: str) -> Parsed:
        import stanza

        pipeline = _model()
        doc = pipeline(text)
        source_id = "parser-fr"
        source = Source(
            id=source_id, kind="parser", name=f"stanza-fr-{model_package()}", version=RESOURCE_VERSION,
            locator="https://huggingface.co/stanfordnlp/stanza-fr/tree/v1.11.0/models",
            metadata={"framework": "Stanza", "package_version": stanza.__version__, "package": model_package(),
                      "processors": "tokenize,mwt,pos,lemma,depparse"},
        )
        clitic_source = Source(id="rule-fr-clitic", kind="rule", name="French en/y form marker",
                               version="0.1.0", locator=None, metadata={"scope": "form only"})
        sentences: list[Sentence] = []
        for si, sent in enumerate(doc.sentences, 1):
            if not sent.tokens:
                continue
            sid = f"s{si}"
            segments: list[Segment] = []
            words: list[Word] = []
            parser_to_normalized = {word.id: f"{sid}w{wi}" for wi, word in enumerate(sent.words, 1)}
            for ti, token in enumerate(sent.tokens, 1):
                gid = f"{sid}g{ti}"
                surface = text[token.start_char:token.end_char]
                segment_word_ids = [parser_to_normalized[w.id] for w in token.words]
                segments.append(Segment(id=gid, span=Span(start=token.start_char, end=token.end_char),
                                        surface=surface, word_ids=segment_word_ids))
                for word in token.words:
                    wid = parser_to_normalized[word.id]
                    direct_span = (Span(start=token.start_char, end=token.end_char)
                                   if len(token.words) == 1 and surface == word.text else None)
                    feats = {}
                    if word.feats:
                        for pair in word.feats.split("|"):
                            if "=" in pair:
                                key, value = pair.split("=", 1)
                                feats[key] = value.split(",")
                    if word.deprel:
                        head = None if word.head == 0 else parser_to_normalized.get(word.head)
                        dep = (present(Dependency(head_word_id=head, relation="root" if word.head == 0 else word.deprel), source_id)
                               if word.head == 0 or head else unavailable("parser 指向句外節點"))
                    else:
                        dep = unavailable("parser 未提供 dependency")
                    words.append(Word(
                        id=wid, segment_id=gid, form=word.text, span=direct_span,
                        lemma=present(word.lemma, source_id) if word.lemma else unavailable("parser 未提供 lemma"),
                        upos=present(word.upos, source_id) if word.upos else unavailable("parser 未提供 UPOS"),
                        xpos=present(word.xpos, source_id) if word.xpos else unavailable("parser 未提供 XPOS"),
                        morphology=present(feats, source_id) if word.upos else unavailable("parser 未提供 morphology"),
                        dependency=dep,
                        extensions=({"fr": {"clitic_type": present(word.text.lower(), clitic_source.id).model_dump()}}
                                    if word.upos == "PRON" and word.text.lower() in {"en", "y"} else {}),
                    ))
            complete = all(w.dependency.status == FieldStatus.present for w in words)
            sentences.append(Sentence(
                id=sid, span=Span(start=sent.tokens[0].start_char, end=sent.tokens[-1].end_char),
                segments=segments, words=words, dependency_status="complete" if complete else "partial",
            ))
        warning = Warning(
            code="MODEL_LIMITATION",
            message="法文模型仍可能誤判 partitive du、en/y 依存及 emoji 斷句；lemma 遵循語料標註慣例，不一定等於教學字典詞頭。",
            sentence_id=None, word_ids=[], source_ids=[source_id],
        )
        return Parsed(sentences=sentences, sources=[source, clitic_source], warnings=[warning])
