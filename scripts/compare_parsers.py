"""Reproducible CPU spike. Snapshots are observations, never linguistic gold.

Run each parser in a fresh process so cold load and peak RSS are comparable.
No downloads, LLM calls, or network access are performed by this script.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import time

from workbench.pipeline import analyze, get_adapter


ROOT = Path(__file__).resolve().parents[1]


def peak_rss_mib():
    try:
        import resource
    except ImportError:
        return None  # Windows: use an external process monitor.
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 ** 2 if platform.system() == "Darwin" else 1024)


def summary(samples):
    return {"runs": len(samples), "p50_seconds": statistics.median(samples),
            "p95_seconds": sorted(samples)[math.ceil(len(samples) * .95) - 1],
            "samples_seconds": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parser", choices=["stanza", "spacy", "ginza"], required=True)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--torch-threads", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 1 or args.torch_threads < 1:
        parser.error("runs and torch-threads must be positive")
    if args.parser == "stanza" and not os.getenv("WORKBENCH_STANZA_DIR"):
        parser.error("set WORKBENCH_STANZA_DIR to the provisioned model directory")
    language = "ja" if args.parser == "ginza" else "fr"
    os.environ["WORKBENCH_FRENCH_PARSER"] = args.parser
    # No enrichment calls; never read or include API keys in reports.
    os.environ["OMP_NUM_THREADS"] = str(args.torch_threads)
    os.environ["MKL_NUM_THREADS"] = str(args.torch_threads)

    if language == "fr":
        cases = json.loads((ROOT / "eval/cases/french-parser-spike.json").read_text(encoding="utf-8"))
    else:
        cases = [dict(case, id=f"ja-{i}") for i, case in enumerate(
            json.loads((ROOT / "eval/cases/spike.json").read_text(encoding="utf-8")))
            if case["language"] == "ja"]

    start = time.perf_counter()
    if args.parser == "stanza":
        import torch
        torch.set_num_threads(args.torch_threads)
        from workbench.adapters.french import _model
        _model()
    else:
        from workbench.adapters.spacy_bridge import _model
        _model(get_adapter(language).model_name)
    load_seconds = time.perf_counter() - start

    snapshots = []
    for case in cases:
        start = time.perf_counter()
        try:
            result = analyze(case["text"], language, "off")
            entry = {**case, "seconds": time.perf_counter() - start,
                     "structural_validation": "passed", "analysis": result.model_dump(mode="json")}
            if "expansion" in case:
                expected = case["expansion"]
                actual = [([w.form for wid in segment.word_ids for w in sentence.words if w.id == wid])
                          for sentence in result.sentences for segment in sentence.segments
                          if segment.surface == expected["surface"]]
                entry["expansion_passed"] = actual == [expected["forms"]]
        except Exception as exc:
            entry = {**case, "structural_validation": "failed", "error": f"{type(exc).__name__}: {exc}"}
        snapshots.append(entry)
        print(f"{args.parser}: {case['id']} {entry['structural_validation']}", flush=True)

    single = cases[0]["text"]
    paragraph = ("Aujourd’hui, elle va au marché avec ses amis pour acheter du pain, des fruits et du café. "
                 "Il y a beaucoup de monde, mais les commerçants prennent le temps de parler avec chaque client. "
                 "Elle leur demande des conseils, puis elle choisit trois pommes et quelques légumes pour le dîner. "
                 "Ses amis lui proposent de rentrer à pied en passant par le parc, où les enfants jouent près des arbres. "
                 "Ils en parlent encore pendant le trajet, car cette promenade leur permet de profiter du beau temps.")
    if language == "ja":
        paragraph = "昨日、本を三冊買った。私は学生です。私が読みました。本を読んでいます。食べた。"
    # French input-limit probe: append whitespace without changing the sentences.
    if language == "fr":
        assert len(paragraph) <= 500
        paragraph = paragraph.ljust(500)
    timings = {}
    for name, text in (("single", single), ("paragraph", paragraph)):
        warmup = analyze(text, language, "off")
        samples = []
        for _ in range(args.runs):
            start = time.perf_counter()
            analyze(text, language, "off")
            samples.append(time.perf_counter() - start)
        timings[name] = {"text": text, "code_points": len(text),
                         "sentences": len(warmup.sentences), **summary(samples)}
        print(f"{args.parser}: {name} p95={timings[name]['p95_seconds']:.3f}s", flush=True)

    versions = {dist.metadata["Name"]: dist.version for dist in metadata.distributions()}
    artifacts = []
    if args.parser == "stanza":
        model_dir = Path(os.environ["WORKBENCH_STANZA_DIR"])
        from workbench.adapters.french import model_files, model_package
        for name in model_files(model_package()):
            path = model_dir / "fr" / name
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            artifacts.append({"path": path.relative_to(model_dir).as_posix(),
                              "bytes": path.stat().st_size, "sha256": digest,
                              "url": "https://huggingface.co/stanfordnlp/stanza-fr/resolve/v1.11.0/models/" +
                                     path.relative_to(model_dir / "fr").as_posix()})
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "parser": args.parser,
        "environment": {"platform": platform.platform(), "python": platform.python_version(),
                        "logical_cpus": os.cpu_count(), "torch_threads": args.torch_threads,
                        "packages": dict(sorted(versions.items()))},
        "method": "fresh process; load includes framework imports; warm timings include mapping, validation and resources; enrichment off; nearest-rank p95",
        "load_seconds": load_seconds, "peak_process_rss_mib": peak_rss_mib(),
        "timings": timings, "artifacts": artifacts, "cases": snapshots,
        "limitations": "Development probes, not held-out or human gold. No general language-accuracy claim. RSS includes Python, frameworks, models and inference.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output}", flush=True)
    if any(case["structural_validation"] != "passed" for case in snapshots):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
