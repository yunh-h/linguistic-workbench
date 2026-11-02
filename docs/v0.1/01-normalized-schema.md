# Normalized linguistic schema v0.1

狀態：設計基準。已有 Pydantic models 與跨欄位 validator；LLM draft 的 provider schema 由同一份 Pydantic 定義產生。2026-09-26 候選驗證範圍與限制見 [驗收紀錄](../../eval/2026-09-26-enrichment-validation.md)，未完成項目不因寫入規格即視為實作完成。

## 1. Representation 與範圍

借用 [Universal Dependencies 的 POS、features、basic dependency 與 words/tokens 區分](https://universaldependencies.org/format.html)，不是宣稱完整支援 CoNLL-U。v0.1 不支援 enhanced dependencies、empty nodes 或自動補出的零代詞。

分開三個概念：

- **Segment**：可在原文精確定位的表面片段。前端靠它重現原文與點選。
- **Word**：parser 的句法分析單位，依存關係指向 Word ID。一個 segment 可對應多個 word，例如法文 `au → à + le`。
- **Annotation**：gloss、翻譯與構式說明。可指向一句話或多個 words，不把語意角色強制塞入 dependency。

Word 不保證是最小語素。日文活用切分遵循選定 parser，不在 core 另造斷詞規則。

## 2. Primitive 與共同規則

- `schema_version = "0.1.0"`；ID 為非空字串，在同一 analysis 內唯一，重新分析可變，禁止跨 analysis 引用。
- 所有 span 都是原始 input 的 **Unicode code point** 全文座標，0 起算、左含右不含：`{start: integer, end: integer}`。不得使用 UTF-8 byte 或 JavaScript UTF-16 index；前端建立 code-point→UTF-16 對照再切字串。
- 不 trim、不 NFC/NFKC、不替換直／彎引號。parser 若內部正規化，adapter 必須回映原文；映射失敗視為結構錯誤。
- 必填 key 不得省略；以下標示 nullable 者使用 null。空陣列是「已執行但沒有項目」，不能同時代表失敗。
- Core object 禁止額外 keys。語言擴充只能出現在 `extensions`，且由對應 capability pack 驗證。
- 語言碼 v0.1 為 `fr | ja | en`；`en` 是可選 parser-only 擴充，未安裝時能力端點標示 unavailable。法日的翻譯／解釋語言固定 `zh-TW`，lexical gloss 固定英文 `en`；英文擴充不產生這三種 annotation。UD 縮寫維持原樣。這是 MVP 顯示策略，不是語言學的 universal 假設。

## 3. Analysis envelope

下列全部為必填欄位：

| 欄位 | 型別／語意 |
| --- | --- |
| schema_version | 固定 0.1.0 |
| analysis_id | 字串 |
| input | {text: string, language: fr/ja/en} |
| status | complete / partial |
| capabilities | Capability[]，見 Adapter 文件 |
| sentences | Sentence[]，至少一項 |
| sources | Source[]，包含 parser、adapter normalization 規則與實際使用的資源／LLM |
| annotations | Annotation[] |
| disagreements | Disagreement[] |
| warnings | Warning[] |

`complete`：必備 parser 階段有效；若是法日且本次要求 enrichment，該階段成功、每句有翻譯、每個非標點 word 有 gloss；構式可為零。英文 parser-only 與法日 `enrichment=off` 不因 annotation 為空降級。它不表示分析無歧義。`partial`：有有效 parser 結果，但必要欄位無法取得、圖不完整或本次要求的 enrichment 缺漏／失敗。完全沒有有效 parser 結果時回 API error，不製造空 Analysis。

## 4. Sentence、Segment、Word

Sentence：
- `id: string; span: Span; segments: Segment[]; words: Word[]`
- `dependency_status: "complete" | "partial" | "unavailable"`

Segment：
- `id: string; span: Span; surface: string; word_ids: string[]`
- span 非空、彼此不重疊、依原文排列，surface 必須等於原文 slice。
- 所有非空白 code points 必須被 segment 覆蓋一次；空白間隙從原文還原。標點也是 segment。
- word_ids 不可為空；一個 word 恰屬一個 segment；多個 words 共用 segment 時依分析順序排列。

Word：
- `id: string; segment_id: string; form: string`
- `span: Span | null`：可直接對應原文時必填 span；縮合拆出的無法逐字對應成分使用 null，點選時落在 parent segment。
- `lemma: Field<string>; upos: Field<UPOS>; xpos: Field<string>`
- `morphology: Field<object<string, string[]>>`
- `dependency: Field<{head_word_id: string | null, relation: string}>`
- `extensions: object`，未使用時 {}。

`form` 是 parser 分析形式；有 word.span 時必須等於原文 slice。lemma 與 Sudachi normalized form 不等同。UPOS 只接受 UD 的 17 個標籤：ADJ、ADP、ADV、AUX、CCONJ、DET、INTJ、NOUN、NUM、PART、PRON、PROPN、PUNCT、SCONJ、SYM、VERB、X。未辨識是 Field.unknown，不把缺漏一律填 X。

Morphology 使用 UD feature 名稱與值陣列，例如 `{"Number":["Sing"],"Person":["1"]}`。沒有可用資訊用 unknown + null；有效但無 features 可用 present + {}。非 UD 特徵放 extensions；v0.1 依固定 UD allowlist 驗證，新增分類需更新 pack mapping。dependency relation 接受 UD basic relation 與 pack allowlist 的 subtype。

目前候選專用 allowlist 定義於 `backend/src/workbench/candidates.py`：只接受支援的 feature/value 子集、UD basic relations 與明列的法／日 subtype；不宣稱涵蓋所有 UD 或語言特徵。parser adapter 的現有輸出不套用這份候選詞彙限制。Morphology 同一 feature 的值不能重複，順序不同但值集合相同不算分歧。

## 5. 每個分析欄位的 Field<T>

所有 keys 必填：

| key | 型別 |
| --- | --- |
| status | present / unknown / not_applicable / unsupported |
| value | T 或 null |
| source_ids | string[] |
| certainty | unassessed / tentative / supported |
| reason | string 或 null |

present 才可有非 null value，且 source_ids 至少一個；其他 status 的 value 必為 null、reason 必為非空字串。unknown 是此次無法判定，unsupported 是 pack 不提供，not_applicable 是該欄位對此單位不適用。非 present 的 certainty 必為 unassessed；sources 可列造成缺漏的 parser。

certainty 不是機率：parser 與 LLM 預設 unassessed；有明確歧義／分歧設 tentative；supported 只用於可定位、符合條件的 reviewed resource／人工核對，不能只因兩個模型說法一致就提升。v0.1 不呈現百分比 confidence。

## 6. Annotation、Source、Disagreement、Warning

Annotation：
- `id; kind: "lexical_gloss" | "translation" | "construction"; sentence_id; word_ids: string[]`
- `language: "en" | "zh-TW"; text: Field<string>`
- lexical_gloss：一個 word、language=en。是閱讀輔助 gloss，不承諾 Leipzig 規範的 morpheme alignment；標點可不產生。
- translation：空 word_ids 表示整句，language=zh-TW。
- construction：至少一個 word、language=zh-TW，可不連續，但都屬同句。
- LLM 可說明省略主語或 en 的可能指涉，必須保留「需語境」；不得新增看似確定的隱含 words。
- 同 kind、sentence_id、word_ids 集合只能有一個主顯示 annotation；word_ids 不重複，集合順序不同視為相同 scope。present text 不得空白，non-present placeholder 不算完成 coverage。

Source：
- `id; kind: "parser" | "resource" | "rule" | "llm"; name; version: string; locator: string | null; metadata: object<string,string>`
- parser 記錄套件、model artifact 名稱與版本／hash；rule 記錄 adapter mapping 版本；resource 記錄 entry ID／版本與來源連結；LLM 記錄 provider、實際回傳的 model ID、prompt version、request ID（可用時）。
- 上游不提供 immutable version 時明記 `unavailable`，不要假裝可完全重現。API key、完整 prompt、使用者原文不放入 metadata。
- source_ids 表示誰產生／支援這個主張，不等於正確性證明；同一個 lemma 不能拿整份分析的籠統來源代替欄位來源。

Disagreement：
- `id; word_id; field: "lemma" | "upos" | "morphology" | "dependency"; selected: Field<T>; alternatives: Field<T>[]; resolution: "retain_parser" | "unresolved"; reason: string`
- T 與該欄位型別一致；selected 必須與 Word 的同名 Field 完全相同，alternatives 至少一項。dependency 是整組 head+relation，禁止單獨改 head。
- retain_parser 僅指 v0.1 的來源政策，不代表 parser 在語言學上勝出。unresolved 用於 parser 該欄無值、候選仍未驗證；selected 留原本無值欄位。
- 分歧的 selected 與 alternatives 只要 present 就標 tentative；見 reconciliation 規則。

Warning：
- `code: string; message: string; sentence_id: string | null; word_ids: string[]; source_ids: string[]`
- code 包含 FIELD_MISSING、DEPENDENCY_INCOMPLETE、ENRICHMENT_FAILED、ENRICHMENT_INCOMPLETE、RESOURCE_UNAVAILABLE、ANALYSIS_DISAGREEMENT、ALIGNMENT_REJECTED。message 用繁體中文，可供 UI 顯示；細部 exception 不放入回應。

## 7. 最小語言擴充

`extensions.fr`（如存在）：`{clitic_type: Field<"en" | "y" | "other">}`。分類只表示形式上的 clitic 標記，不自動推導 obj、Theme 或先行詞。

`extensions.ja`（如存在）：`{reading: Field<string>, normalized_form: Field<string>, conjugation_type: Field<string>, conjugation_form: Field<string>}`。缺漏依 Field.status 表示。reading 是資源的假名讀法，不宣稱 pitch accent 或完整語流發音。

英文擴充的 `extensions` 固定為 {}。另一語言 namespace 不得出現在本語言結果。Core 不需理解特定欄位；UI 從 pack 的固定標籤表顯示，不執行 pack 傳來的程式或 HTML。

## 8. 結構驗證與示例

驗證分兩層：schema 檢查型別／enum／required；semantic validator 檢查 ID、span、參照與圖。complete dependency 句中，每個 word 有一條有效 edge，恰一個 root；root 的 head=null、relation=root，其他 edge 同句、不可自指且無環。partial 允許缺 edge，但現有 edges 仍須無環、同句、至多一個 root；不得畫成完整樹。

無效 ID／span 是整份 parser output 的致命錯誤。依存圖無效時，保留有效斷詞與其他欄位，整句所有 dependency 轉 unknown、dependency_status=unavailable，附 warning；不可只丟壞 edge 後宣稱成功。

以下是手寫契約片段，不是某個 parser 的實測答案：

| 原文 | 表面 Segment／span | 分析 Words | UI 行為 |
| --- | --- | --- | --- |
| J'en ai acheté trois. | J' [0,2)、en [2,4) | J'、en，後續各成 word | 點 en 選自己的 word；不強制其 dependency=obj |
| au | au [0,2) | à、le，各自 span=null，segment_id 相同 | 原文只顯示一次 au，點選後列兩個分析單位 |
| 昨日、本を三冊買った。 | 買っ [7,9)、た [9,10) 是一種示意切分 | 買っ、た | 不把切分預先宣稱為 GiNZA 的固定輸出；M0 記錄實際切分 |

Field 示意：
```json
{
  "status": "present",
  "value": "en",
  "source_ids": ["parser-fr", "normalizer-fr"],
  "certainty": "unassessed",
  "reason": null
}
```

應有設計案例：emoji／組合字元 offset、au/du 縮合、彎引號 J’en、日文無空格、標點-only、缺 lemma、LLM 未知 ID、循環依存、跨句 head、重複 ID。完整法／日 payload 與可執行 validator 是 M1 的產出，這輪不實作。
