# Language Adapter contract v0.1

狀態：設計規格。依賴 [schema](01-normalized-schema.md)；本文件的方法名稱是介面約定，不是已建立的 class。

實作更新（2026-09-26）：parser-only 的程序隔離、deadline 與資源降級見 [穩定性紀錄](../../eval/2026-09-26-parser-only-stability.md)；候選型別／引用／依存圖與 reconciliation 已通過模擬驗收，見 [候選驗收紀錄](../../eval/2026-09-26-enrichment-validation.md)。真實 provider 與語言品質驗收仍待完成。

## 1. 邊界與流程

Core 負責 request validation、pack registry、階段 deadline、LLM provider 呼叫、reconciliation、final validation、API serialization。Adapter 負責語言相關 parser、offset mapping、resource lookup、語言擴充與候選的語言規則。前端只能接收 normalized Analysis，不可依賴 spaCy Doc 或 Stanza Document。

流程：

Input → validate → select adapter → parse → normalize → validate core
→ lookup resources → generate enrichment candidates → validate candidates
→ reconcile → validate final Analysis → UI。

這是固定 pipeline；HTTP 服務以每語言一個持續載入模型的 subprocess 執行，由父程序監督 deadline。同語言請求序列處理，等待計入預算；沒有外部任務佇列、動態插件載入、agent framework 或多模型投票。Parser 是統計模型，不稱為正確性保證；「可重現的分析骨架」與「正確的分析」必須分開。

## 2. Pack manifest 與能力

一個 pack 是版本化的 adapter + parser config + mapping rules + curated resource entries。manifest 欄位：

- language（fr/ja；可選英文擴充 en）、adapter_version、schema_version（0.1.0）。
- parser：package、model、artifact version/hash、必要 runtime 依賴。
- resources：entry-set version/hash、來源、license reference。
- capabilities：下列 Capability[]；啟動時檢查，缺必要 parser 就不將該 pack 標為 ready。

Capability = `{name: string, support: "available" | "limited" | "unsupported", reason: string | null}`。limited／unsupported 必填 reason。使用固定 name：segmentation、lemma、upos、morphology、dependency、lexical_gloss、translation、construction、fr.clitic_type、ja.reading、ja.normalized_form、ja.conjugation。能力表示 pack 上限，Field.status 表示這次個別結果，不混為一談。

法日 segmentation/lemma/upos/dependency 是必要能力；morphology、語言擴充允許 limited。可選英文 pack 也需提供 segmentation/lemma/upos/dependency，morphology 可為 limited；lexical_gloss/translation/construction 固定 unsupported，沒有 LLM 或 resource enrichment。LLM 未設定時，法日的 translation/gloss 標 limited（只有 curated entries 或無可用輸出），UI 仍可開 parser-only 模式。

## 3. 呼叫約定

| 邏輯方法 | 輸入 | 輸出／責任 |
| --- | --- | --- |
| describe() | 無 | PackManifest；唯讀，不啟動下載 |
| parse(text, context) | 原文；request_id、deadline、pack config | Adapter 私有 RawParse；不可傳到 UI |
| normalize(raw, original_text) | parse 結果與完全相同原文 | NormalizedCore：input、sentences、sources、warnings；符合 schema 的 parser 子集合 |
| lookup(core, context) | 有效且不可變的 core | ResourceBatch：新增 sources、annotations、extension candidates、warnings |
| enrichment_spec(core, resources) | 有效 core 與已驗證 resources | 固定 prompt version、合法 ID、允許欄位與結構化輸出規格；core 負責送 provider |
| validate_extensions(core, candidates) | namespace 與欄位 | 通過／明確錯誤，不修改原始 text 或 word IDs |

Context 不含 learner profile。parse/normalize 不呼叫 LLM；lookup 只讀隨 pack 發行的小型本地資料。v0.1 不做即時網路字典查詢。

英文 parser-only pack 跳過 lookup/enrichment_spec；core 直接以 normalize 後的有效 parser 資料組成 Analysis。英文不使用 LLM，也不把空 annotation 當成 enrichment 失敗。

Core 的 source registry 先配置各階段 ID。Adapter 可產生 parser/resource/rule sources；LLM 不能自行聲稱 resource source 或生成引用 URL。原文當資料隔離，不能改變 system 規則或取得執行工具能力。

## 4. Enrichment 輸入／輸出

一次 request 最多一次初始 LLM 呼叫，涵蓋全部句子；固定上限見 UI contract。輸入是原文、words/IDs、parser 欄位、可用 resource entries、允許的 output schema。output 不含新的 segmentation 或依存樹。

EnrichmentBatch：
- `annotations: Annotation[]`：依 schema；core 產生 annotation IDs，LLM 草稿僅回 kind/sentence_id/word_ids/language/text。
- `proposals: Proposal[]`
- Proposal = `{word_id, field, value, rationale}`；field 僅 lemma/upos/morphology/dependency，value 型別與 schema 相同；dependency 以 head+relation 一組提出。
- 完成驗證後 core 補 source_ids=[本次 llm source]、certainty=unassessed；有不確定語境的說明標 tentative。
- 不開放 LLM 修改 extensions、capabilities、sources、warning 或 status。構式候選無法對齊 IDs 時拒絕。

每句最多一個 translation、每 word 最多一個 LLM gloss、每句最多三則 construction。非標點 gloss／translation 缺漏須列 ENRICHMENT_INCOMPLETE 並標 partial，不能靠空陣列冒充完整結果。

目前 wire draft 使用 `translations/glosses/constructions/proposals` 四個陣列；proposal 值以 `value_json` 傳輸，再嚴格解碼，不接受重複 JSON key 或非 JSON 數值。provider schema 由 `EnrichmentDraft` 產生，額外 keys 拒收；合法候選詞彙與既有 annotations 同時提供給模型，原文與資源文字都只是資料。

每個 word/field 最多一個 proposal。同批 dependency 候選一起覆蓋到**暫時的驗證圖**，其餘沿用 parser edges；禁止跨句、自指、成環、多 root 與 root/null head 不一致。完整 edge 集合必須恰有一個 root，缺 edge 的 parser 圖仍可保留缺漏。協調改 root/head 的候選可以整批通過，但不會真的替換 parser 圖。

## 5. Reconciliation policy

| 情況 | 決策 |
| --- | --- |
| Parser 核心欄位有效且無候選衝突 | 保留；certainty 不自動升級 |
| LLM 和 parser 在同一欄位相同 | 去除重複，不把一致視為獨立驗證 |
| LLM 和 parser 不同 | 保留 parser；建立 Disagreement，完整保存替代值、來源、rationale；selected/alternative 標 tentative |
| Parser 欄位 unknown，LLM 提出值 | v0.1 不自動補進核心欄位；Disagreement.resolution=unresolved，selected 留 unknown |
| 比較不同 segmentation | 不用字串位置猜對齊；拒絕候選並警告 ALIGNMENT_REJECTED |
| Resource 有一個符合條件的 reviewed gloss／construction | 使用 resource 版本；只有確切符合 applicability 才標 supported |
| Resource 有多義項或條件未滿足 | 不挑唯一義項；交 LLM 產生 tentative 說明或留 unknown |
| Resource 已供同 scope 的 annotation，LLM 也回傳 | 同 kind/sentence/word_ids 以 resource 優先；不產生兩個主顯示值 |

最後一項是 enrichment 的明確來源優先政策，不宣稱做了詞義消歧；UI 可見選用 resource 來源。對不同 kind 或不同 scope 的說明可並存。核心分析的多來源衝突才使用 Disagreement 結構。

v0.1 不設任意 confidence 加權公式，不讓 LLM 覆寫 parser，不做 universal grammar rule engine。可先只有 4–6 個 curated 構式條目；超出覆蓋範圍仍能回一般 parser 分析。

## 6. Failure、重試與生命週期

- HTTP parser 上限 8 秒（含同語言鎖等待）、resources 1 秒。OpenAI 補充預設 20 秒／總 30 秒，LM Studio 補充預設 90 秒／總 100 秒；補充期限含修正重試，可由 `WORKBENCH_LLM_TIMEOUT_SECONDS` 設為 5–300 秒，總期限隨之加 10 秒。另有少量程序清理時間。已實作於 runtime，直接呼叫 pipeline 的比較腳本不受此 supervisor 約束。
- parser 載入在啟動時完成；禁止第一個 request 自動下載模型。CPU 推論由可中止／隔離的 worker 執行，HTTP cancel 不可假設能打斷底層運算。
- 啟動／背景恢復的每語言載入預算為 60 秒；ready 需通過載入 handshake 且程序存活。逾時終止程序並背景重建；排隊請求自身逾時不能終止另一個正在執行的請求。
- 結構不合法或 parser timeout：整體失敗，不以 LLM-only 假裝成功。
- dependency 無效但 segmentation 有效：依 schema 降級為 partial。
- resources 失敗：在隔離副本上執行，例外、無效參照或改動 parser 核心時回復 parser，警告 RESOURCE_UNAVAILABLE；要求 on 時可繼續 LLM。若卡住／程序崩潰，終止 worker，回傳已驗證 parser checkpoint；此時不再啟動 LLM，要求 on 時另標 ENRICHMENT_FAILED。
- LLM schema 不合格：在總預算內最多一次修正重試；整個 batch 隔離，不把部分無效陣列混入結果。
- 候選在 Analysis 深拷貝上合併，重新進行 Pydantic 與跨欄位驗證後才交付；失敗不留下 sources、annotations 或 tentative 欄位。core 配置來源／annotation／disagreement ID，避免與現有 registry 衝突。
- LLM timeout/provider error、refusal 或 incomplete response：不自動重試；回 parser/resource 結果、partial + ENRICHMENT_FAILED。
- LM Studio 使用 Chat Completions + JSON Schema；不把工具呼叫、截斷回應或 reasoning 內容當候選。配置 provider 是顯式選擇，不會跨 provider fallback；本地 token 與 OpenAI key 分離。終止本工作台 worker 不保證獨立模型服務已停止生成，見 [本地連接指南](../lmstudio.md)。
- 檢查 IDs／候選 head／型別屬候選驗證的一部分；錯誤不得進到最後圖上。
- 驗證完成後 Analysis 視為 immutable。v0.1 無跨 request cache；同輸入重跑可有不同 enrichment，ID 不承諾相同。
- 記錄階段耗時、版本、錯誤碼，不預設記錄原文或 prompt。無資料庫與永久使用者歷史。

## 7. 合約驗收（後續實作）

每個 adapter（含可選英文）共用一套 contract cases：原文完整保留、code point offsets、ID 唯一、同句 head、缺資料可表達、關閉 LLM 可運作、模型缺失可辨識、資源來源可追溯。另有法日 enrichment 的兩種故障注入：LLM 指向不存在 word；LLM 改 subject head；前者拒收，後者保留 parser 並顯示分歧。英文不啟動這條 enrichment 路徑。

Core 測試不載真模型；adapter 整合測試才用固定模型 artifact。模型更換時重新檢查切分、mapping 與語言案例，不能只更新版本字串。
