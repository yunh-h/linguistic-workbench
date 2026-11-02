# 句子紀錄、模型診斷與追問

這是 2026-09-26 依使用者要求加入的本機閱讀流程切片，不代表完整 MVP 或語言品質已驗收。

## 模型補充與診斷

自動補充目前只要求每句繁體中文翻譯、每個非標點 word 的英文 gloss。`v0.1.2-reading` prompt 不再要求模型生成構式或 parser 替代分析；兩個陣列必須為空，JSON Schema 與本地型別都限制 `maxItems=0`。既有資源構式與候選 reconciliation 程式仍保留，但自動模型路徑不使用候選修正。缺少必要翻譯／gloss 可修正重試一次；再失敗即保留 parser／resources。

`ENRICHMENT_FAILED` 保持相容，新增可選的 `diagnostic: {reason, detail, retryable}`。UI 顯示原因摘要，可展開固定文案細節；不暴露 exception、provider 原始回應、思考過程、API key 或私有網址。分類含 connection、timeout、authentication、busy、http、truncated、refused、format、candidate、graph、configuration、provider、worker、skipped。只有實際觀察到的原因才標示；程序中斷不假稱逾時，前一階段失敗不假稱 LLM 已回答。

舊快照沒有 diagnostic 時仍顯示舊訊息，不能事後重建原本丟失的失敗原因。`retryable` 是建議，不保證同一輸入下次成功。結構有效不代表翻譯／解釋正確。

## 查過的句子

歷史已改用 backend 的 SQLite，預設檔案為 `data/workbench.sqlite3`。操作、匯入與備份指令見 [SQLite 歷史指南](sqlite-history.md)。

- 成功取得的完整／部分 Analysis 可保存；同語言、同原文共用一筆句子，每次重分析保留新快照。列表每頁 50 筆，沒有自動淘汰；可搜尋、切換先前版本。
- 保存偏好仍在瀏覽器，句子資料則由同一 backend 共用。關閉保存不刪舊資料；聊天、API key 與服務網址不存入歷史。
- 開啟快照只從 DB 取回並驗證，不重跑 parser／LLM。需要工作台服務運作，但不需模型 ready。
- 保存失敗保留當前分析並提供重試；尚未保存的結果只在分頁記憶體中。舊請求在輸入變更後才回來時不會送出保存；已送出的保存則可能已 commit。
- 刪除句子會連帶刪除其所有版本，清空影響同一 backend 的所有瀏覽器；操作前確認。支援本機備份指令，沒有自動排程。
- 舊 localStorage 可由原瀏覽器按「匯入瀏覽器舊紀錄」搬入 DB；逐筆回報、不自動清除。確認 DB 副本仍可讀後，才讓使用者選擇刪除舊副本。原本不同 host／port 的舊資料需各自匯入。

## 句子追問

分析結果下方有「問問這個句子」。不需先開啟自動補充，也可從歷史快照提問；但 backend 必須有合法模型設定。選取詞語時，會帶入該 word ID。

送出到目前 provider 的資料包括原文、parser 欄位、選取詞 ID、問題與最近三輪完成的對話；不會送整份歷史紀錄。模型與 provider 沿用自動補充的環境設定，沒有跨 provider fallback。LM Studio 不使用 OpenAI key。

回答為純文字（不執行 HTML／Markdown），標示未經語言學驗證。它沒有工具，不會修改 Analysis，也不寫入瀏覽器歷史或後端聊天資料庫。模型服務本身是否記錄請求，仍由該服務設定決定。

限制：問題 1–1000 Unicode 字元、最多三輪 user／assistant 配對歷史、單則歷史內容最多 4000 字元；context HTTP body 上限 256 KiB。最終回答最多 4000 字元；混入 `<think>`、截斷、拒答或異常回應不採用。模型端 context 長度仍可能不足，需縮短句子／清除對話；字元上限不保證符合模型 token 上限。英文若已有 parser 結果也可追問，但英文自動 enrichment 仍不支援。

追問後端使用 async HTTP，整體等待受 `WORKBENCH_LLM_TIMEOUT_SECONDS` 約束，前端再加 5 秒。每個 API 程序最多一個進行中的追問，其他回 429；不占用 parser worker。同一分頁追問中禁止提交新分析，但可編輯／開歷史，會清除對話並停止等待。清除、換句子、重新分析、重新整理會清空對話；取消不保證 LM Studio 停止生成，後端仍受 deadline 約束。不同分頁的 enrichment 與追問仍可能同時使用 GPU，這不是全域模型排程器。

API：`POST /api/v0.1/questions` 收 `{analysis, selected_word_id, question, history}`，回 `{answer, provider, model, notice}`；歷史只允許 user／assistant，不能插入 system role。無模型設定 503、並行追問 429、格式／引用不符 422、過大 413、非 JSON 415；provider 失敗 502、逾時 504。錯誤 envelope 包含安全分類與操作建議。輸入分析與歷史一律視為不可信資料，prompt 不是保證模型永不受注入影響的防線；沒有工具與分析寫入能力才是此功能的權限邊界。

`GET /api/v0.1/capabilities` 新增 `question_available`（只表示配置有效）、`question_timeout_ms`。

失敗處理參考 [OpenAI Structured Outputs 官方文件](https://developers.openai.com/api/docs/guides/structured-outputs)：拒答與未完整結束需分別處理，schema 遵循不代表內容正確。LM Studio 協定沿用 [本地模型指南](lmstudio.md)。本次真實推論只使用 LM Studio。
