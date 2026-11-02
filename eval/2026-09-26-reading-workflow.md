# 閱讀流程切片驗收

日期：2026-09-26。沿用 `feat/linguistic-workbench-mvp`，使用者同意順序：清楚的模型失敗原因 → 收斂補充 → 句子追問，另新增查過句子的本機歷史（不是聊天歷史）。

## 實作範圍

- `ENRICHMENT_FAILED` 保留相容，新增安全的 reason／detail／retryable；連線、期限、驗證、截斷、拒答、候選／圖錯誤與程序中斷分開。UI 可展開細節，不顯示原始 exception 或 provider body。
- `v0.1.2-reading` 自動補充只生成翻譯／gloss，構式與候選陣列同時受 schema／本地型別 maxItems=0 限制；不放寬既有候選驗證。必備覆蓋缺漏可重試一次；資源優先與原子套用保留。
- 同瀏覽器保存最近 50 筆／約 2 MB 句子分析快照；相同語言與原文去重，支援關閉保存、單筆刪除、確認後清空。恢復快照只驗證，不推論。儲存毀損或配額失敗不阻擋分析。
- 句子追問帶入原文、parser 欄位、選詞與最近三輪對話，純文字顯示且不能改分析；聊天只在記憶體、不持久化。async HTTP 整體 deadline、一個 API 程序一個進行中追問、輸入與 body 大小限制、取消／晚到回應防護。

## 自動驗收

| 項目 | 結果 |
| --- | --- |
| 完整 backend，含真實 parser／spawn 測試 | 219 passed，23.42 秒；一則既有 Starlette/httpx 棄用警告 |
| Chromium 桌面／手機互動 | 22 passed，9.08 秒 |
| 三個 frontend JS 的 `node --check` | 通過 |
| `git diff --check` | 通過 |

後端新增測試包含安全錯誤分類與不洩漏、圖錯誤／程序期限區分、禁止自動語法候選、缺 gloss 修正、無推論快照驗證、過大／錯誤 content-type／壞 ID／非法 role／過長對話、無設定、追問逾時取消／busy／鎖釋放、本地與雲端憑證隔離及 malformed／thinking／truncation 回答。

瀏覽器新增測試涵蓋錯誤詳細原因、reload 後讀取快照不推論、去重／關閉保存／刪除／清除確認、損壞或配額不足、快照引用損壞、50 筆容量上限、跨分頁通知、取消後不保存、選詞追問／三輪上限／純文字 XSS 防護／聊天不持久化、失敗保留問題、逾時與換句子丟棄晚到回答。手機截圖人工檢視無水平溢出；原有 au 多詞選取與日文點詞測試保持通過。

測試指令沿用 README 的 backend／UI opt-in；UI 指向隔離的 parser-only :8765，模型回答由 mock 提供。所有 backend 測試清除真實 LLM 設定與 key。最初非升權 sandbox 中 TestClient 停滯，已停止該兩個測試程序，改在允許本機 IPC 的環境完成上列驗收；沒有把未完成的執行當成通過。

## 真實 Qwen 前後觀測

LM Studio、`qwen/qwen3-14b` Q4；硬體及服務位址細節不公開。仍未量測 VRAM 或讀取 thinking／context／offload 設定。未呼叫付費 API，也未實測 Gemma。

| 輸入 | 舊 prompt 的單次觀測 | 收斂後的單次觀測 |
| --- | --- | --- |
| `Je vais au marché.` | 18.89 秒，complete 但有不可靠語法候選 | 10.39 秒，complete，1 翻譯＋5 gloss，無語法候選 |
| `昨日、本を三冊買った。` | 33.86 秒，ENRICHMENT_FAILED／partial | 12.33 秒，complete，1 翻譯＋7 gloss，保留原資源註解 |

新法文翻譯「我要去市場。」；日文「昨天，買了三本書。」。兩個輸出另外通過 `Analysis.model_validate` 與 `validate_analysis`。日文未補入明確主語；法文仍選了帶意向色彩的中文表達，不能由結構通過宣稱所有語意都精確。

用該日文分析選取「を」追問「這裡的『を』是什麼作用？能知道是誰買的嗎？」：1.14 秒、HTTP 200；模型說明格助詞標示被購買的書，且原句無法確定購買者。這是一則模型生成示例，不是語法品質評分或權威來源。沒有改動原分析。

以上不是受控效能 benchmark：每句只有一次新舊觀測，prompt 工作範圍已變小、重試次數／載入狀態可能不同，不推導穩定加速比例或 p95。仍需更廣樣本、重複試驗及人工語言品質檢查。

## 保留的限制

localStorage 非加密、非跨裝置備份，不同 host／port 各自獨立；多分頁極少數同時寫入可能 last-write-wins。歷史開啟仍需本機 backend。關閉等待不保證 LM Studio 中止生成；不同分頁的分析與追問可能競爭 GPU。context 字元上限不保證符合模型 token 容量。沒有 chat transcript、帳號或同步。

目前只是可試用的閱讀流程切片，不是完整 MVP 或語言品質驗收完成。操作與完整 API／隱私界線見 [閱讀流程](../docs/reading-workflow.md)。
