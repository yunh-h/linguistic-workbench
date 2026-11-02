"""Manual M0 parser inspection. Cases are prompts for review, not gold labels."""

import json
import time
from pathlib import Path

from workbench.pipeline import AdapterUnavailable, ParserInvalid, analyze


ROOT = Path(__file__).resolve().parents[1]


def main():
    cases = json.loads((ROOT / "eval/cases/spike.json").read_text(encoding="utf-8"))
    for case in cases:
        start = time.perf_counter()
        try:
            result = analyze(case["text"], case["language"], "off")
            elapsed = time.perf_counter() - start
            print(json.dumps({
                "language": case["language"], "text": case["text"],
                "phenomenon": case["phenomenon"], "status": result.status,
                "seconds": round(elapsed, 3),
                "sentences": [{
                    "segments": [segment.surface for segment in sentence.segments],
                    "words": [{"form": word.form, "lemma": word.lemma.value,
                               "upos": word.upos.value,
                               "dependency": word.dependency.value.model_dump() if word.dependency.value else None}
                              for word in sentence.words],
                } for sentence in result.sentences],
            }, ensure_ascii=False))
        except (AdapterUnavailable, ParserInvalid) as exc:
            print(json.dumps({"language": case["language"], "text": case["text"],
                              "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
