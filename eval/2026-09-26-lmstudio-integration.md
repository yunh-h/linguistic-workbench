# LM Studio 連接驗收

日期：2026-09-26。分支：`feat/linguistic-workbench-mvp`。

## 已完成

- 新增明確的 `lmstudio` provider，使用 Chat Completions + JSON Schema，模型 ID、base URL、本地 token、補充 deadline 與 max_tokens 可設定。OpenAI Responses 路徑保留，兩者不共用憑證、不自動 fallback。
- LM Studio 預設 `http://127.0.0.1:1234/v1`，無 authentication 時不要求 key。不讀取環境 proxy、不跟隨 redirect；base URL 不能包含帳密、query 或 fragment。設定只取自 backend 環境變數，非 HTTP request 欄位。
- 本地補充預設 90 秒（含修正重試）、總 100 秒、前端 105 秒；cloud 預設仍為 20／30／35 秒。parser-only 前端維持 35 秒，parser 預算仍為 8 秒。所有期限由同一配置推導，錯誤配置停用補充而不阻擋 parser。
- 模型輸出必須正常結束且包含一則 assistant 文字；拒答、工具呼叫、截斷回應不當成完成。只處理最終 content，忽略獨立 reasoning_content；不使用字串截取掩蓋混在內容的 think／markdown。保留既有整批候選驗證、一次修正重試與 partial 降級。
- UI 顯示 provider／model 及尚未確認連線的提示；來源 metadata 記錄 provider。新增唯讀 `scripts/check_lmstudio.py`，只列出 `/v1/models` ID，不推論、不載入或下載模型。

## 證據

| 驗證 | 結果 |
| --- | --- |
| 完整 backend，`WORKBENCH_TEST_MODELS=1` | 184 passed；一則既有 Starlette/httpx 棄用警告 |
| Chromium 瀏覽器互動 | 13 passed |
| `node --check frontend/app.js` | 通過 |
| `git diff --check` | 通過 |

新 provider 測試涵蓋模型 ID 切換、JSON Schema payload、key 隔離、錯誤 URL、設定邊界、refusal、truncation、401／429／500、redirect、不 fallback、修正重試、診斷指令及前後端 deadline 同步。測試會清除繼承的 LLM 環境變數／真實 key，只使用明確的測試配置。

另外驗證真正的完整傳輸鏈：真實 Stanza／GiNZA → spawn worker → loopback HTTP 模擬 LM Studio → validator → API，法日皆產生有效結果。這證明協定整合，不代表已呼叫真實 Qwen／Gemma。

## 尚待真實機器驗收

此環境為 WSL2。唯讀探測 localhost:1234 無服務可連；對目前 Windows 主機閘道的 :1234 探測也逾時。沒有變更網路、防火牆、LM Studio 設定，沒有發送真實推論或付費 API 請求。

候選模型包含 Qwen3-14B Q4 與 Gemma 4 E4B Q4；硬體細節不公開。仍需啟動可由 backend 到達的 LM Studio Server、確認完整 model ID，然後量測法日翻譯、gloss、候選通過率、速度及 VRAM；尚無兩個真實模型優劣的比較結論。

逐步操作與官方協定依據見 [LM Studio 指南](../docs/lmstudio.md)。工作台終止 worker／HTTP 不保證獨立 LM Studio 已立即停止生成。
