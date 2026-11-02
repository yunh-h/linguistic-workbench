"""Small, source-linked pack notes with deliberately narrow matching rules."""

from __future__ import annotations

from workbench.schema import Analysis, Annotation, Source, present


_ENTRIES = {
    ("fr", "en"): (
        "fr-en-form", "https://universaldependencies.org/fr/",
        "「en」的指涉與句法功能需結合上下文；不要只憑詞形認定它是賓語。",
    ),
    ("fr", "y"): (
        "fr-y-form", "https://universaldependencies.org/fr/",
        "「y」的指涉與句法功能需結合上下文；請同時參照依存分析。",
    ),
    ("ja", "は"): (
        "ja-wa-topic", "https://universaldependencies.org/ja/dep/dislocated.html",
        "「は」可標示話題；話題不必等於句法主語。",
    ),
    ("ja", "を"): (
        "ja-o-case", "https://universaldependencies.org/ja/dep/case.html",
        "「を」是格助詞；相關名詞片語的句法角色仍應看整句分析。",
    ),
}


def lookup_resources(analysis: Analysis) -> None:
    if analysis.input.language == "en":
        return
    for sentence in analysis.sentences:
        for word in sentence.words:
            entry = _ENTRIES.get((analysis.input.language, word.form.lower()))
            if entry is None or word.upos.value not in {"PRON", "ADP", "PART"}:
                continue
            entry_id, locator, note = entry
            source_id = f"resource-{entry_id}"
            if not any(source.id == source_id for source in analysis.sources):
                analysis.sources.append(Source(
                    id=source_id, kind="resource", name="Workbench pack note",
                    version="0.1-draft", locator=locator,
                    metadata={"entry_id": entry_id, "review_status": "draft"},
                ))
            analysis.annotations.append(Annotation(
                id=f"note-{word.id}", kind="construction", sentence_id=sentence.id,
                word_ids=[word.id], language="zh-TW", text=present(note, source_id),
            ))
