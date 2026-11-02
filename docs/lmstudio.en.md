# Using a local model with LM Studio

[繁體中文](lmstudio.md) · **English**

The workbench connects to LM Studio using `/v1/chat/completions` with JSON Schema structured output. The returned candidates are validated locally; the model does not overwrite the parser's analysis. Automatic enrichment currently covers translation and gloss; follow-up questions are separate and not saved.

A Qwen3-14B Q4 connection was tested on a small number of French and Japanese examples. Earlier tests revealed linguistic errors and a failed enrichment; later retesting worked on the same examples. This is not broad quality or performance validation, and Gemma has not been tested.

## 1. Prepare LM Studio

1. Install LM Studio, prepare a suitable model, and load it. Hardware requirements depend on the model, quantization, context and offload settings.
2. For Qwen3, disable thinking in the model settings. The workbench does not send an unverified thinking switch or strip `<think>` text mixed into JSON.
3. Start the server from LM Studio's Developer page. A common starting address on the same machine is `http://127.0.0.1:1234`; this is the server address, not the web-chat URL.

## 2. Configure and test the connection

First complete the [quick start](../README.en.md#-quick-start). In the terminal running the backend, configure the provider and server base URL:

```bash
export WORKBENCH_LLM_PROVIDER=lmstudio
export WORKBENCH_LLM_BASE_URL=http://127.0.0.1:1234/v1
.venv/bin/python scripts/check_lmstudio.py
```

The diagnostic sends `GET /v1/models`; it does not load, download, or run a model. Use the exact model ID from its output. Being listed does not guarantee a model is loaded or works for analysis.

```bash
export WORKBENCH_LLM_MODEL='replace-with-the-full-model-id'
./scripts/start.sh
```

Open <http://127.0.0.1:8000>, enable model enrichment in the interface, and submit a sentence. The UI is currently in Traditional Chinese. To change models, prepare the new model in LM Studio, update the model ID, and restart the backend. The workbench does not automatically choose or load a model.

For native Windows PowerShell, the equivalent variables and diagnostic command are shown below; **the complete native Windows model workflow remains unverified**:

```powershell
$env:WORKBENCH_LLM_PROVIDER = "lmstudio"
$env:WORKBENCH_LLM_BASE_URL = "http://127.0.0.1:1234/v1"
.venv\Scripts\python scripts/check_lmstudio.py
$env:WORKBENCH_LLM_MODEL = "replace-with-the-full-model-id"
.venv\Scripts\python -m uvicorn workbench.api:app --host 127.0.0.1 --port 8000
```

Environment variables override `config.local.toml`; the app does not automatically read `.env`. The launcher reads non-secret settings from TOML, but the diagnostic script and direct uvicorn startup do not. See [local startup](local-startup.en.md).

## 3. Settings

Set these before starting the backend, then restart it after changes.

| Variable | Purpose |
| --- | --- |
| `WORKBENCH_LLM_PROVIDER` | Set to `lmstudio` explicitly. |
| `WORKBENCH_LLM_BASE_URL` | Server base URL ending in `/v1`; the default is `http://127.0.0.1:1234/v1`. Do not include credentials, a query, or a fragment. |
| `WORKBENCH_LLM_MODEL` | Full model ID returned by `/v1/models`. |
| `WORKBENCH_LLM_API_KEY` | Optional local token if LM Studio authentication is enabled. Keep it out of Git. |
| `WORKBENCH_LLM_TIMEOUT_SECONDS` | Enrichment deadline, including at most one correction retry; default 90 seconds, range 5–300. |
| `WORKBENCH_LLM_MAX_TOKENS` | Completion token limit; default 4096, range 256–16384. |

LM Studio mode does not use `OPENAI_API_KEY`; OpenAI mode does not use the local token or base URL. There is no automatic fallback between providers. The initial request uses structured output, a temperature of 0.7, and no streaming; these settings are not a claim of optimal model quality.

An enrichment failure preserves the parser result. A workbench timeout ends its own worker but does not guarantee that the separate LM Studio server immediately stops generating.

## 4. WSL connectivity and privacy

If LM Studio runs on Windows while the backend runs in WSL, `127.0.0.1` may not reach the other environment. Use an address reachable **from the backend**, and do not append `/chat/completions` to the base URL.

Do not expose the model service to the public internet or disable your firewall for troubleshooting. If access from another environment is needed, limit allowed sources and consider LM Studio authentication. The input text and relevant analysis are sent to the configured host when enrichment or follow-up questions are used; a remote address therefore sends data to that host.

For detailed behavior and test evidence, see the [Traditional Chinese LM Studio guide](lmstudio.md) and [reading workflow](reading-workflow.md).
