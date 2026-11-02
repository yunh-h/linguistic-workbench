# 🔎 Linguistic Workbench

**Take a sentence apart. See how language works.**

A local linguistic analysis workbench for language learners and linguistics enthusiasts. Explore French and Japanese sentences word by word, inspect grammatical relationships and sources, and optionally add translations and glosses with an LLM.

[繁體中文](README.md) · **English**

> 🚧 **v0.1 local preview**
>
> The app runs locally and is opened in a browser. The interface is currently in Traditional Chinese. Analyses and LLM output may be incorrect and have not undergone broad linguistic quality evaluation.

## ✨ What can it do?

- **Explore sentence structure:** inspect tokenization, word forms, parts of speech, dependency relations, and their sources for French and Japanese.
- **Add optional LLM enrichment:** connect a local LM Studio model or the OpenAI API for translations, word-level glosses, and follow-up questions.
- **Keep your own version:** save sentences and analysis snapshots, and edit your own translation.
- **Take your notes with you:** export one, selected, or all history entries to Markdown. Each sentence uses your saved translation when available, otherwise the model translation.

💡 Parser analysis works without an LLM. Translations, glosses, and follow-up questions require a configured model service. English is an optional parser-only extension and requires a separately installed model.

## 🧭 Current limitations

- The primary tested setup is **Linux / Windows WSL with Python 3.12**. Complete native Windows and macOS installation and model workflows have not yet been verified; there is no one-click installer.
- Parsers and LLMs can make mistakes. Passing schema validation does not establish linguistic or translation accuracy.
- Automatic LLM enrichment is currently limited to translations and glosses; automatic construction and grammar candidates are paused. Follow-up conversations are not saved or exported.
- History is stored locally in SQLite. Everyone using the same backend shares that history; **there is no account isolation**. The server listens locally by default and should not be exposed directly to the public internet.

## 🚀 Quick start

The following setup is for Linux / WSL. Run the commands from the repository root. Ubuntu requires Python 3.12 and `python3.12-venv`.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install torch==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -c eval/constraints-linux-py312.txt -e './backend[dev]'
export WORKBENCH_STANZA_DIR="$(pwd)/data/models/stanza"
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
.venv/bin/python scripts/download_french_model.py --model-dir "$WORKBENCH_STANZA_DIR"
./scripts/start.sh
```

Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)**. For subsequent starts, run `./scripts/start.sh` from the repository root. If the script lacks execute permission, use `bash scripts/start.sh`. Stop the server with `Ctrl+C`.

For parser-only mode:

```bash
./scripts/start.sh --parser-only
```

📦 Installation downloads Python packages and language resources. The repository does not include parser model files or an LLM.

## 🤖 Model setup

- **LM Studio:** Load a model and start the server, then configure the provider, model ID, and an address reachable by the backend. See the [LM Studio guide](docs/lmstudio.en.md).
- **OpenAI API:** Set `WORKBENCH_LLM_PROVIDER=openai`, `WORKBENCH_LLM_MODEL`, and `OPENAI_API_KEY`. An account and API key are required; usage may incur charges.

The sample localhost address works only when the backend can reach the model service there. WSL-to-Windows connections may need another address.

🔐 Keep API keys in environment variables, not in Git. Inputs and relevant analysis data are sent to the selected provider when using LLM enrichment or follow-up questions.

For non-secret settings, copy `config.example.toml` to `config.local.toml` without overwriting an existing file. Direct uvicorn startup does not read this file.

## 🧪 Tests

```bash
.venv/bin/python -m pytest backend/tests -q
node frontend/tests/test_export.js
```

GitHub Actions runs lightweight backend and export checks on updates to main and pull requests targeting main. It does not download language models, run a browser, or call an LLM; passing CI does not establish model or linguistic quality.

## 📚 Documentation

- [Local startup](docs/local-startup.en.md)
- [LM Studio setup](docs/lmstudio.en.md)

The following detailed guides are currently in Traditional Chinese:
- [Reading workflow](docs/reading-workflow.md)
- [SQLite history, backups, and exports](docs/sqlite-history.md)
- [Schema and language adapters](docs/v0.1/01-normalized-schema.md)
- [Parser comparison](eval/2026-09-26-parser-comparison.md)

## 🤖 Development note

This project was developed with assistance from OpenAI Codex.

## 📄 License

This project is available under the [MIT License](LICENSE). Third-party packages, models, and dictionaries have their own licenses and terms.
