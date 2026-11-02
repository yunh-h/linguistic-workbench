from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from workbench.schema import Sentence, Source, Warning


@dataclass(frozen=True)
class Parsed:
    sentences: list[Sentence]
    sources: list[Source]
    warnings: list[Warning]


class LanguageAdapter(Protocol):
    language: str
    model_name: str

    def load(self) -> None: ...

    def parse(self, text: str) -> Parsed: ...
