"""Tests opt into providers explicitly; never inherit real credentials/configuration."""

import pytest


@pytest.fixture(autouse=True)
def isolated_llm_environment(monkeypatch):
    for name in ("OPENAI_API_KEY", "WORKBENCH_LLM_PROVIDER", "WORKBENCH_LLM_MODEL",
                 "WORKBENCH_LLM_BASE_URL", "WORKBENCH_LLM_API_KEY",
                 "WORKBENCH_LLM_TIMEOUT_SECONDS", "WORKBENCH_LLM_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolated_history_database(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_DB_PATH", str(tmp_path / "history.sqlite3"))
