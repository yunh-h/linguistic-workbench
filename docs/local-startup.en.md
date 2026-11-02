# Local startup and configuration (Linux / WSL)

[繁體中文](local-startup.md) · **English**

This guide covers the Linux / Windows WSL launcher. Native Windows and macOS installation and real-model workflows have not been fully verified. For first-time installation and model provisioning, follow the [English README](../README.en.md#-quick-start).

## Start the workbench

Open a terminal in the repository root and run:

```bash
./scripts/start.sh
```

If the script is not executable, run `bash scripts/start.sh` instead. Once the server reports that startup is complete, open <http://127.0.0.1:8000>. Keep the terminal open; press `Ctrl+C` to stop the server. The launcher does not install a startup service or background daemon.

The launcher uses the project's `.venv/bin/python`; activating the virtual environment is not required. If the port is already in use, stop the other service or select another port with `--port`.

```bash
./scripts/start.sh --check        # Check configuration, packages, and French model files only
./scripts/start.sh --parser-only  # Disable LLM features for this run
```

`--check` does not load the models, connect to the LLM, or check database integrity. It does not guarantee model readiness. After starting, check each model's status in the interface.

## Local files

| Item | Default path relative to the repository | Tracked by Git |
| --- | --- | --- |
| Python environment and installed Japanese model | `.venv/` | No |
| French Stanza models | `data/models/stanza/` | No |
| History database and SQLite sidecar files | `data/workbench.sqlite3*` | No |
| Machine-specific configuration | `config.local.toml` | No |
| Example configuration and launcher | `config.example.toml`, `scripts/start.*` | Yes |

## Configuration and privacy

For persistent non-secret configuration, copy `config.example.toml` to `config.local.toml`, taking care not to overwrite an existing file. Paths are relative to the repository root. This is a TOML data file, not a shell script.

The precedence order is launcher flags such as `--port` or `--parser-only`, then shell environment variables, then `config.local.toml`, then defaults. The launcher prints the database and parser model paths it will use.

The app does not automatically read `.env`. Set API keys through environment variables; do not put credentials in TOML or Git. Starting uvicorn directly does not load `config.local.toml`.

The service binds to `127.0.0.1` by default. It has no account isolation, and clients using the same backend share the same history. Do not expose it directly to the public internet.

## Optional LM Studio connection

Load a model and start the LM Studio server, then configure the model ID and a service address that the backend can reach. The app can start without LM Studio; leave LLM enrichment unchecked or use `--parser-only`.

The diagnostic script reads environment variables rather than the TOML file:

```bash
WORKBENCH_LLM_PROVIDER=lmstudio \
WORKBENCH_LLM_BASE_URL=http://127.0.0.1:1234/v1 \
WORKBENCH_LLM_MODEL=qwen/qwen3-14b \
.venv/bin/python scripts/check_lmstudio.py
```

The example model ID is illustrative; use the full ID returned by your server. Localhost works only when it is reachable from the backend's network environment. If the backend runs in WSL and LM Studio runs on Windows, use an address the WSL backend can reach. The launcher does not load models or discover addresses automatically. See the [English LM Studio guide](lmstudio.en.md).

## Rebuild and backups

A new machine needs Python 3.12 and venv support. Reinstall and provision models using the [quick start](../README.en.md#-quick-start); do not copy another machine's entire `.venv`. Keep existing configuration and history files until their replacements are verified.

History is not backed up by Git. The [SQLite guide](sqlite-history.md) (in Traditional Chinese) describes backups, restoration, and tests. Do not replace a live database while the backend is running.
