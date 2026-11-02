# 使用 LM Studio 本地模型

**繁體中文** · [English](lmstudio.en.md)

目前透過 LM Studio 的 `/v1/chat/completions`、`response_format: json_schema` 呼叫模型；收到 JSON 後仍經本地候選驗證，不讓模型覆寫 parser。Qwen3-14B Q4 已完成法日各一句的真實連線測試，但補充品質尚未過關；Gemma 4 E4B 可用相同介面切換，尚未實測。詳見 [Qwen 實測紀錄](../eval/2026-09-26-qwen-live-smoke.md)。

## 1. 準備 LM Studio

1. 在 LM Studio 載入一個模型，先測 Qwen3-14B Q4。Context 可從 8K、單句開始，依實際輸入／輸出長度調整；GPU offload 依可用顯存設定，一次只載入一個模型。
2. 對 Qwen3 關閉模型設定中的 thinking。此程式不傳未驗證的 thinking API 開關，也不嘗試剝除混在 JSON 裡的 `<think>` 文字。服務若另回 `reasoning_content`，只讀取最終 `content`。
3. 在 Developer 頁啟動 Server，確認顯示的網址與 port。一般同機起點為 `http://127.0.0.1:1234`，不是網頁聊天網址。

## 2. 設定工作台與確認 model ID

在**執行本專案 backend 的終端機**設定，沿用 [README](../README.md) 的 Python 環境與 parser 模型目錄。這些是環境變數，程式沒有自動讀取 `.env` 檔。Linux／WSL 也可把非憑證設定保存在 `config.local.toml`，由 `./scripts/start.sh` 載入，詳見 [固定啟動配置](local-startup.md)；shell 環境變數優先於 TOML。

Linux／WSL：

```bash
export WORKBENCH_LLM_PROVIDER=lmstudio
export WORKBENCH_LLM_BASE_URL=http://127.0.0.1:1234/v1
.venv/bin/python scripts/check_lmstudio.py
```

Windows PowerShell：

```powershell
$env:WORKBENCH_LLM_PROVIDER = "lmstudio"
$env:WORKBENCH_LLM_BASE_URL = "http://127.0.0.1:1234/v1"
.venv\Scripts\python scripts/check_lmstudio.py
```

診斷指令只呼叫 `GET /v1/models`，不載入、不下載、不推論模型；列出的 ID 不等於模型已載入。從輸出複製完整 ID，而不是猜測檔名或 UI 顯示名稱。再設定並啟動服務：

```bash
export WORKBENCH_LLM_MODEL='替換為診斷指令列出的完整ID'
./scripts/start.sh
```

PowerShell 對應：

```powershell
$env:WORKBENCH_LLM_MODEL = "替換為診斷指令列出的完整ID"
.venv\Scripts\python -m uvicorn workbench.api:app --host 127.0.0.1 --port 8000
```

開啟 `http://127.0.0.1:8000`，頁面會顯示 `LM Studio · 模型ID`。勾選「加入模型補充」後提交。切換 Gemma 時，在 LM Studio 準備相應模型、更新 `WORKBENCH_LLM_MODEL`，再重啟 backend；不需修改程式碼。UI 不會自動挑選、載入或切換模型。

## 3. 可調設定

| 變數 | LM Studio 預設／用途 |
| --- | --- |
| `WORKBENCH_LLM_PROVIDER` | 必須明確設 `lmstudio`；未設定時沿用 `openai` |
| `WORKBENCH_LLM_BASE_URL` | `http://127.0.0.1:1234/v1`；接受 http(s) 主機與 `/v1`，不含帳密、query、fragment |
| `WORKBENCH_LLM_MODEL` | 必填，使用 `/v1/models` 的完整 ID |
| `WORKBENCH_LLM_API_KEY` | 選填；僅當 LM Studio 啟用 authentication 時設本地 token。不要貼到聊天或提交 Git |
| `WORKBENCH_LLM_TIMEOUT_SECONDS` | `90`，範圍 5–300；整個補充階段含最多一次修正重試 |
| `WORKBENCH_LLM_MAX_TOKENS` | `4096`，範圍 256–16384；本地 completion 的生成上限，長輸出可能需要增大 context／期限 |

請在啟動 backend 前設定；修改後重啟。LM Studio 不讀取 `OPENAI_API_KEY`，OpenAI 模式不讀取本地 token／網址；沒有跨 provider fallback。預設送出 `temperature=0.7`、非串流、JSON Schema；這些是初始配置，不是兩個模型都已量測的最佳值。

parser 預算維持 8 秒、resources 1 秒。本地預設補充 90 秒 → 後端總 100 秒 → 前端等待 105 秒。總期限會隨補充設定加 10 秒、前端再加 5 秒，parser-only 前端仍為 35 秒。同語言 worker 忙碌時另一請求的等待仍受 8 秒 parser 預算限制。

工作台逾時會終止自己的 worker，不能保證獨立 LM Studio 服務立即中止已送出的生成；重新分析會發新請求。`finish_reason=length`、拒答、工具呼叫或 provider 失敗保留 parser 結果，不把截斷 JSON 當成功。

## 4. Windows／WSL 連線與隱私

若 LM Studio 在 Windows，backend 在 WSL，`127.0.0.1` 不一定能跨到另一端。請以**backend 所在環境**可達的 LM Studio 位址設定 `WORKBENCH_LLM_BASE_URL`；不要把 `/chat/completions` 加進 base URL。可先從 Windows 本機確認 Server 已啟動，再排查 WSL 到主機的連線。

不要為排錯直接將服務暴露到網際網路或停用防火牆。若必須讓 WSL／區網連入，僅開放所需來源，並考慮 LM Studio authentication。`lmstudio` 是 provider 名稱，不代表設定任意遠端網址後仍完全不出本機；原文會送到你設定的主機。本地路徑不使用環境 HTTP proxy、不跟隨 redirects，也不會在失敗後轉送 OpenAI。

LM Studio 預設不需 authentication；若啟用 token，請使用專用的 `WORKBENCH_LLM_API_KEY`。這項行為依據 [LM Studio authentication 文件](https://lmstudio.ai/docs/developer/core/authentication)。協定與 JSON Schema 依據 [Chat Completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions) 及 [Structured Output](https://lmstudio.ai/docs/developer/openai-compat/structured-output) 文件。

## 5. 驗收界線

最新 `v0.1.2-reading` 已將自動模型補充收斂為翻譯與 gloss，構式／語法候選陣列強制為空；並加入錯誤原因細節、句子歷史與追問。相同法日例句重測成功，見 [閱讀流程驗收](../eval/2026-09-26-reading-workflow.md)；操作與隱私見 [閱讀流程](reading-workflow.md)。以下為調整前的初次實測，保留作比較。

已測試 mock provider、真實 parser／spawn worker 到模擬本地 HTTP 服務，以及瀏覽器期限／來源標示。後續已連上使用者的真實 LM Studio：Qwen 法文單句約 19 秒完成，但有錯誤語法建議；日文單句約 34 秒補充失敗並保留 parser，追加診斷觀察到循環依存候選遭拒。這只是小樣本冒煙測試，不是完整品質／效能驗收，也尚未比較 Gemma。不要將 JSON 通過率當作語言品質；細節見 [實測紀錄](../eval/2026-09-26-qwen-live-smoke.md)。
