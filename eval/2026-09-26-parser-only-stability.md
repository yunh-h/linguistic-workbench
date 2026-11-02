# Parser-only 穩定性驗收

日期：2026-09-26。分支：`feat/linguistic-workbench-mvp`。

結論：法文 Stanza combined／日文 GiNZA 的 parser-only 穩定性切片已完成，**不是完整 v0.1 MVP 驗收**。模型選型與語言分析限制另見 [parser 比較](2026-09-26-parser-comparison.md)。本次沒有呼叫付費 LLM、推送遠端或將模型納入 Git。

## 實作範圍

- 每語言一個 `spawn` 子程序，啟動時實際載入模型；ready 需載入完成且程序存活。每語言載入預算 60 秒，缺模型不自動下載。
- HTTP 請求 parser 8 秒、resources 1 秒、enrichment 20 秒、整體 30 秒；鎖等待計入 parser 與總預算，另有少量程序清理時間。直接呼叫 pipeline 的比較腳本不受 HTTP supervisor 約束。
- parser 卡住時終止程序、回 504，再背景恢復；等待同語言程序的另一請求逾時不會終止目前使用者的工作。跨程序回應只傳一部分就卡住也受 watchdog 約束。
- resources 在隔離副本執行，例外、無效參照或修改 parser 核心時回復有效 parser，標示 partial／RESOURCE_UNAVAILABLE。資源或 enrichment 程序卡住／崩潰時回傳已驗證的階段 checkpoint，不回傳半成品。
- 前端 35 秒逾時、保留輸入、可重試；編輯輸入／語言／enrichment 後隱藏舊結果並取消請求，以 sequence 拒絕晚到回應。HTTP 取消不保證立即中止後端，後端仍受 deadline 約束。
- 模型狀態查詢失敗或直接開啟 file:// 時維持停用分析；新增重新檢查模型。輸入上限以 Unicode code points 計算。
- 真實模型的法文 `au` 單一原文片段、多 word 選取、dependency head 導航與日文點詞；選詞有非顏色狀態、鍵盤左右鍵，窄螢幕欄位換行。

## 驗證證據

環境：Linux／WSL CPU、Python 3.12、Stanza 1.11.0 combined、GiNZA `ja_ginza` 5.2.0。測試環境與模型放在 repository 外。瀏覽器使用 Playwright 1.55.0 的 Chromium headless，補齊 CJK 字型後檢視 390 × 844 手機 viewport 截圖。

| 測試 | 結果 | 證據與界線 |
| --- | --- | --- |
| `WORKBENCH_TEST_MODELS=1 python -m pytest backend/tests -q` | 40 passed | 契約、真實法文探針、API 法→日→法、故障注入；有一則 Starlette/httpx 棄用警告 |
| `WORKBENCH_TEST_UI=1 python -m pytest frontend/tests -q` | 8 passed | 真實法日 API、au／head／鍵盤／手機版、失敗後重試、舊回應隔離、Unicode 上限與 file:// |
| `node --check frontend/app.js` | 通過 | JavaScript 語法 |
| `git diff --check` | 通過 | patch 空白檢查 |

故障注入使用真正 `spawn` 的假 worker：parser hang、半個 IPC frame、crash、resource hang/crash、enrichment hang、總預算、併發結果隔離，以及 POSIX 忽略 SIGTERM 後強制終止。驗證舊 PID 消失且下一次分析可恢復；不代表真實模型自然發生過這些故障。

瀏覽器的正常分析與重試使用真實服務；504、resource partial、capabilities 網路失敗、晚到回應使用受控攔截。35 秒客戶端 deadline 使用瀏覽器虛擬時鐘測試，後端 deadline 另以短預算的真子程序驗證。500 emoji 案例驗證的是前端字數限制，不是 parser 語言品質。

重跑方式見 [README](../README.md)。本機可選截圖輸出由 `WORKBENCH_UI_ARTIFACT_DIR` 指定，不納入 Git。

## 尚未宣稱完成

1. 當時尚未完成 LLM 候選驗證與 reconciliation 邊界案例；同日後續已完成 [候選驗證切片](2026-09-26-enrichment-validation.md)。真實 provider 與語言內容正確性仍待驗收。
2. 法日人工 gold／held-out 品質審查、curated resource 條目的適用性與來源審查。結構合法不表示語言分析正確。
3. Windows 原生 combined 模型、程序生命週期與效能重測；使用 spawn 是相容性設計，不是 Windows 測試證據。
4. 生產負載／記憶體壓力與多使用者部署驗收。此版本仍為本機同步分析工作台，每語言序列執行；不提供任務佇列或即時取消後端 API。
