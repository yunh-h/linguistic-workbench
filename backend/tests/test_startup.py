"""Local launcher configuration tests: no server, downloads or production DB access."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("workbench_local_start", ROOT / "scripts/start.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_defaults_are_persistent_project_paths_independent_of_cwd(tmp_path, monkeypatch):
    root = tmp_path / "project with spaces"
    monkeypatch.chdir(tmp_path)
    env, host, port = launcher.settings(root, {}, {})
    assert env["WORKBENCH_DB_PATH"] == str(root / "data/workbench.sqlite3")
    assert env["WORKBENCH_STANZA_DIR"] == str(root / "data/models/stanza")
    assert env["PYTHONPATH"] == str(root / "backend/src")
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["WORKBENCH_LLM_MODEL"] == ""
    assert (host, port) == ("127.0.0.1", 8000)


def test_shell_overrides_config_and_cli_overrides_port_and_model(tmp_path):
    config = {"server": {"port": 9000}, "environment": {
        "WORKBENCH_DB_PATH": "data/config.sqlite3", "WORKBENCH_LLM_MODEL": "configured-model"}}
    inherited = {"WORKBENCH_DB_PATH": str(tmp_path / "isolated.sqlite3"),
                 "WORKBENCH_LLM_MODEL": "shell-model", "WORKBENCH_LLM_API_KEY": "private-token",
                 "PYTHONPATH": "/tmp/obsolete-source", "OMP_NUM_THREADS": "3"}
    env, _, port = launcher.settings(tmp_path, config, inherited, parser_only=True, port=8765)
    assert env["WORKBENCH_DB_PATH"] == inherited["WORKBENCH_DB_PATH"]
    assert env["WORKBENCH_LLM_API_KEY"] == "private-token"
    assert env["WORKBENCH_LLM_MODEL"] == ""
    assert env["OMP_NUM_THREADS"] == "3" and port == 8765
    assert env["PYTHONPATH"] != inherited["PYTHONPATH"]
    assert inherited["WORKBENCH_LLM_MODEL"] == "shell-model"  # Caller is not mutated.
    normal, _, port = launcher.settings(tmp_path, config, {})
    assert normal["WORKBENCH_LLM_MODEL"] == "configured-model" and port == 9000


@pytest.mark.parametrize("config", [
    {"unknown": {}}, {"server": []}, {"server": {"port": True}}, {"server": {"port": 0}},
    {"server": {"port": 65536}}, {"server": {"host": ""}}, {"server": {"typo": "value"}},
    {"environment": {"PYTHONPATH": "/tmp/unsafe"}},
    {"environment": {"WORKBENCH_LLM_API_KEY": "private-token"}},
    {"environment": {"OMP_NUM_THREADS": 2}}, {"environment": {"OMP_NUM_THREADS": "0"}},
    {"environment": {"WORKBENCH_DB_PATH": ""}},
])
def test_invalid_config_fails_without_echoing_values(tmp_path, config):
    with pytest.raises(ValueError) as exc:
        launcher.settings(tmp_path, config, {})
    assert "private-token" not in str(exc.value)


def test_config_is_data_not_shell_and_bad_toml_is_sanitized(tmp_path):
    path = tmp_path / "config.local.toml"
    assert launcher.load_config(path) == {}
    path.write_text('[environment]\nWORKBENCH_LLM_MODEL = "$(touch should-not-exist)"\n')
    env, _, _ = launcher.settings(tmp_path, launcher.load_config(path), {})
    assert env["WORKBENCH_LLM_MODEL"] == "$(touch should-not-exist)"
    assert not (tmp_path / "should-not-exist").exists()
    path.write_text('[environment]\nWORKBENCH_LLM_MODEL = "private-token\n')
    with pytest.raises(ValueError) as exc:
        launcher.load_config(path)
    assert "private-token" not in str(exc.value)


def test_shell_launcher_from_another_directory_with_spaces(tmp_path):
    root = tmp_path / "project with spaces"
    (root / "scripts").mkdir(parents=True)
    for name in ("start.sh", "start.py"):
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    command = ["bash", str(root / "scripts/start.sh"), "--help"]
    missing = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert missing.returncode == 1 and ".venv" in missing.stderr
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(sys.executable)
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "--parser-only" in result.stdout
    assert not (root / "data").exists()


def test_check_mode_does_not_exec_or_initialize_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "check_installation", lambda: None)
    monkeypatch.setattr(launcher.os, "environ", {})
    monkeypatch.setattr(launcher.sys, "path", list(sys.path))
    monkeypatch.setattr(launcher.os, "execv", lambda *args: pytest.fail("check must not start server"))
    assert launcher.main(["--check", "--parser-only"]) == 0
    assert "LLM：停用" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()
