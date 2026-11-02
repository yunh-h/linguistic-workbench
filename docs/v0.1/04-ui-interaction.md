# MVP UI / API interaction contract v0.1

2026-09-26 使用者同意新增的閱讀流程切片見 [句子歷史、診斷與追問](../reading-workflow.md)。該文件補充此處的 API／UI 合約；目前自動 LLM 僅產生翻譯與 gloss，構式／替代分析暫停生成，舊快照仍可顯示。

狀態：設計規格，已有單頁介面與 API；parser-only 的已驗收範圍見 [穩定性紀錄](../../eval/2026-09-26-parser-only-stability.md)，不代表下列完整 MVP 情境皆已完成。資料定義以 [schema](01-normalized-schema.md) 為準。

## 1. 使用路徑與資訊配置

1. 使用者手動選 French / Japanese；英文擴充完成且 ready 時才顯示 English。輸入一句或短段落，按「分析」。不做語言偵測。
2. 看保留原始字串的分析區；法日 enrichment 有結果時顯示逐詞 gloss 與每句自然翻譯。英文 parser-only 顯示分析欄位，不留空白 gloss／翻譯占位。
3. 點原文 segment，開啟右側（窄螢幕為下方）word 詳情。
4. 詳情列 form、lemma、UPOS、morphology、dependency、語言擴充、來源及分歧。
5. 下方構式卡片點選後反向 highlight 相關 words。來源與不確定性不藏在開發者頁面。

預設用語言學術語與必要短標籤，不自動教一整堂文法課。v0.1 不做語法樹排版；dependency 顯示為「本詞 — relation → head」，點 head 跳到對應 word。root 明示「句子根節點」。partial 圖顯示「依存資訊不完整」，不能畫出一棵看似完整的樹。

## 2. Input 與 request

單一同步 `POST /api/v0.1/analyses`，JSON request：

```json
{
  "text": "J'en ai acheté trois.",
  "language": "fr",
  "enrichment": "on"
}
```

- text：1–500 Unicode code points、至少一個非空白字元；最多 5 句，由 parser 斷句後檢查。保留輸入所有空白與引號。
- language：fr/ja/en；enrichment：on/off，皆必填。英文只接受 off；其 pack 未 ready 時依 ADAPTER_UNAVAILABLE 處理。多餘 keys 拒絕。
- explanation/translation 固定 zh-TW、gloss 固定 en；此次 request 不提供 profile、history 或模型選擇參數。
- 過長輸入前端先提示，server 必須再檢查。混合語言允許當一般文字處理，但結果依所選語言分析、不保證混語品質。
- parser-only 可用時就能提交。LLM 未設定時 UI 預設 off，清楚註明只提供基礎分析；使用者無須填 key 才能看到畫面。選英文時固定 off 並停用 enrichment 控制。
- 輸入框下說明 enrichment on 會將輸入與分析資料送到所設定的模型服務；切換 off 不送出 LLM request。

不串流半成品 JSON、不新增背景 job API。後端預算依 [Adapter](02-language-adapter.md) 的 provider 設定；OpenAI 預設總 30 秒／前端 35 秒，LM Studio 預設總 100 秒／前端 105 秒。capabilities 提供補充模式的前端期限，parser-only 前端維持 35 秒。沒有資料庫、歷史清單或背景同步。

## 3. Response 與錯誤

成功 HTTP 200 直接回 Analysis；status=partial 仍是 200，UI 展示有效內容與警告。

錯誤 envelope（沒有 Analysis）：
`{request_id: string, error: {code: string, message: string, retryable: boolean}}`。

| HTTP | code | UI 處置 |
| --- | --- | --- |
| 422 | INVALID_INPUT / INPUT_TOO_LONG / TOO_MANY_SENTENCES / UNSUPPORTED_LANGUAGE / UNSUPPORTED_ENRICHMENT | 留住輸入、就地提示；英文要求 enrichment=on 屬 UNSUPPORTED_ENRICHMENT |
| 503 | ADAPTER_UNAVAILABLE | 顯示該語言暫不可用；可切換另一語言 |
| 504 | PARSER_TIMEOUT | 保留輸入，可手動重試或縮短 |
| 502 | PARSER_INVALID_OUTPUT | 顯示分析失敗，不顯示未驗證 words |
| 500 | INTERNAL_ERROR | 顯示一般錯誤與 request ID，不露出 exception 或 keys |

`GET /api/v0.1/capabilities` 回 `{schema_version:"0.1.0", languages:[{language, ready:boolean, capabilities:Capability[]}], enrichment_available:boolean}`。法日列為 MVP 語言；英文擴充完成後才列入，且其 enrichment capabilities 固定 unsupported。ready=false 的原因放在 segmentation capability.reason；客戶端不能靠前一次成功推論現在已載入模型。

新增 `enrichment_provider`、`enrichment_model`、`analysis_timeout_ms`；最後一項為補充模式的**前端等待期限**（後端總預算加 5 秒）。`enrichment_available` 只代表設定合法且具備所需 model／key，不代表 provider 已通過健康檢查。UI 顯示 provider/model 與「連線狀態待分析時確認」，不回傳 token 或 base URL。LM Studio 無 authentication 時不需要任何 key；設定不合法仍允許 parser-only。

## 4. UI 狀態機

| 狀態 | 顯示與允許操作 |
| --- | --- |
| idle | 編輯 input、選語言與 enrichment |
| analyzing | 保留輸入；顯示分析中；同一畫面只接納最新 request |
| ready | 完整結果、token 選取、來源／分歧面板 |
| partial | 同 ready，另有缺漏提示與失敗階段 |
| error | 可保留前次結果，但標示為「上次結果」；不得顯示成目前輸入的分析 |

目前實作於輸入修改、切換語言或 enrichment 時直接隱藏現有結果並取消前次 HTTP request；以本地遞增 request sequence 防止晚到回應覆蓋新結果。取消不承諾後端立即中止推論，但仍受後端 deadline 約束。loading 時可編輯，但完成的結果必須和當時 submitted text/language 成對呈現。模型狀態查詢失敗時不可僅靠輸入文字重新啟用提交；使用「重新檢查模型」取得新的狀態。

partial 的「重新分析」會重跑整個 request；v0.1 不實作局部重跑與結果合併，UI 不稱它為「只重試 LLM」。

## 5. Selection 與 rendering

UI local state：active analysis_id、selected sentence_id、segment_id、word_id、開啟的 source/disagreement ID。重新分析清掉舊 ID。

- 原文永遠從 input.text 與 segment spans 還原；不要以 words 加空格重組。
- 一個 segment 對應一個 word：點下直接選取。
- 一個 segment 對應多個 words：先高亮 segment、顯示 words 清單，選取第一個並允許切換。例如 au 僅渲染一次，下方列 à / le；word.span=null 不代表沒有原文位置。
- 法日 gloss 一個 word 一個欄位，多 word segment 可有分列；要求 enrichment 但缺值時用「尚無 gloss」，不把缺值補成翻譯。英文或 enrichment=off 時不顯示 gloss 區。
- 多字構式只高亮其 word 所屬 segments；重複 segment 去重，支援不連續選取。
- 日文 reading 有資料才顯示為欄位；不自動拼出整句讀音或 pitch accent。
- 原文、LLM text 與 resource text 一律純文字 render，不執行 HTML。Source link 只開啟 registry 的 http/https URL，LLM 不能新增引用連結。

## 6. 不確定性與來源

- 欄位 present/unassessed：一般顯示，來源標籤「Parser」「模型補充」等；不打勾宣稱已驗證。
- tentative：顯示「待確認」及原因；若有 Disagreement，展示保留值／替代值、各自來源與保留理由。
- supported：顯示「資源支持」，仍可開來源，避免「正確」標章。
- unknown：「此次無法判定」；unsupported：「目前未支援」；not_applicable：「不適用」。不要把三者都變成空白或 0。
- 翻譯與構式為 LLM 產生時，卡片標「模型補充」；上下文不足的主語／指涉不以斬釘截鐵語氣呈現。
- schema 通過不顯示「語言分析已驗證」；它只代表格式及參照有效。

2026-09-26 已驗收分歧卡片：分開顯示保留值、替代分析（待確認）、各自來源與 rationale；unresolved 明示「原欄位未判定，不自動補入」。測試使用真實 parser 結果加模擬 LLM，不代表真實翻譯品質已驗收。

## 7. Accessibility 與驗收情境

2026-10-26 已依使用者的閱讀工作台預覽更新視覺與導覽：頂層輸入／分析／紀錄各自為獨立畫面，分析頁另有逐詞／句法／語法分頁、來源抽屜、可展開的語言細節、亮暗色與手機版面。主分頁保留草稿與選取狀態，支援 URL hash 與瀏覽器前後頁；詳見 [UI 改版與驗收](../../eval/2026-10-26-reading-ui.md)。

可聚焦的 segments 提供 word/segment 名稱；Enter/Space 選取，左右方向鍵移動 segment；多 word 清單用標準按鈕／清單選取。selection 不能只用顏色表達。窄螢幕詳情移到原文下方，焦點可返回原選詞。

後續 UI 驗收至少包含：
1. French clitic 點選可見核心欄位、來源與構式；不硬寫 en=obj。
2. au 的多 word 選取不重複原文。
3. Japanese 無空格原文與活用切分仍可對齊。
4. emoji／組合字元之後的 highlight 不偏移。
5. LLM timeout 顯示 partial，parser 仍可探索。
6. 已知分歧明顯可見，替代分析可展開。
7. A request 晚於 B 回傳，畫面仍顯示 B。
8. off 模式無 LLM 網路請求；parser 失敗不顯示 LLM-only 結果。
9. 若英文 pack 已加入，選英文只顯示 parser 欄位、來源與點詞 UI；不出現無意義的 gloss／翻譯區，也不允許 enrichment=on。
