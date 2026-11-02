"""Keep the published schema aligned with the analysis model."""

import json
from pathlib import Path

from workbench.schema import Analysis


def test_published_analysis_schema_matches_model():
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "analysis-v0.1.json"
    published = json.loads(schema_path.read_text(encoding="utf-8"))

    assert published == Analysis.model_json_schema(), (
        "Regenerate schemas/analysis-v0.1.json after changing the analysis model"
    )

