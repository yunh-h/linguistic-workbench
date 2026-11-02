# Qwen3-14B 真實本機冒煙測試

日期：2026-09-26。模型 ID：`qwen/qwen3-14b`。使用 Q4 量化模型，硬體細節不公開；本次未讀取 GPU 記憶體用量或 LM Studio 的 thinking、context、offload 設定。

## 連線與設定

WSL 連線初次逾時；改用當次可達的 Windows 主機位址，`GET /v1/models` 與專案 `scripts/check_lmstudio.py` 均成功，確認 Qwen 模型 ID。沒有修改防火牆或 LM Studio 設定。

以下提供連線設定範例（非本次使用的實際位址；程式不自動讀取 `.env`）：

```bash
export WORKBENCH_LLM_PROVIDER=lmstudio
# 以下為同一網路環境的範例；跨環境時請改成實際可達位址
export WORKBENCH_LLM_BASE_URL=http://127.0.0.1:1234/v1
export WORKBENCH_LLM_MODEL=qwen/qwen3-14b
```

使用既有真實 Stanza combined／GiNZA，透過工作台 `POST /api/v0.1/analyses`、spawn worker、LM Studio、候選驗證完整路徑，依序提交下列兩句。沿用 90 秒補充期限、4096 max_tokens、temperature 0.7，測試程序清除 OpenAI 與本地 API key；沒有呼叫雲端或測試 Gemma。

## 實測結果

| 輸入 | 端到端耗時 | HTTP／分析狀態 | 觀察 |
| --- | --- | --- | --- |
| `Je vais au marché.` | 18.89 秒 | 200／complete | 翻譯、5 個 gloss、3 個 construction 與 3 個分歧；結構有效但語言內容有錯 |
| `昨日、本を三冊買った。` | 33.86 秒 | 200／partial | ENRICHMENT_FAILED；保留 GiNZA 與資源註解，無模型補充 |

兩個 HTTP 結果均另經 `Analysis.model_validate` 與 `validate_analysis` 驗證。這驗證的是資料結構與引用／圖等約束，不是語言正確性。耗時各只有一次觀測，含 parser、傳輸、生成及可能的修正重試，不是 tokens/s 或穩定速度估計；未記錄首次請求是否觸發載入。

法文模型回傳「我正要去市場。」；gloss 為 I／go／to／the／market。語法內容混用簡體字，且提出 `vais` 曾是未完成過去時、把介詞 `à` 標為 `PronType=Emp` 等不可靠解釋／候選。3 個分歧均保留 parser 原值並標示來源，沒有覆寫。

為診斷日文另以同句的 parser-only 結果呼叫一次 `_request`，耗時 16.42 秒。這次 JSON 可解析，翻譯為「昨天，買了三本書。」且涵蓋 7 個非標點 gloss；但候選把 `買っ` 的 head 改為 `本`，而 parser 的 `本` 已指向 `買っ`，形成循環。`_check` 因 `BadCandidate: invalid candidate dependency graph` 拒絕整批候選。此為追加診斷的直接證據；原先 API 的失敗回應未保存兩次模型候選，不能宣稱它們的具體原因完全相同。診斷亦出現把「を」標示的受詞稱作間接受詞等語言品質問題；候選未套用到工作台。

## 結論與下一步

本機傳輸已可用，parser 保留與候選圖驗證有實際作用；目前不能把 Qwen 的語法補充視為可靠教學內容，也不能由這兩句判定 Qwen／Gemma 孰優。

下一輪優先確認 thinking 設定，評估縮小補充任務到翻譯／gloss、讓語法 alternatives 更保守，以及明確要求繁體中文。任何 prompt／政策調整須另做固定法日樣本的前後比較，不應為了通過率放寬 ID 或圖驗證。本次未修改 prompt、validator 或 runtime，也未持久化機器專屬預設。
