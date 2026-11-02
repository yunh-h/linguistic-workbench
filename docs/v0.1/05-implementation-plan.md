# Repo structure 與 implementation milestones

狀態：後續實作建議。本輪只提交 README 與 docs/v0.1 的五份 Markdown；下列程式目錄、測試與 CI 都尚未建立。

## 1. 建議目錄

```text
language-app/
  README.md
  docs/v0.1/                 # 本輪的設計基準
  backend/
    pyproject.toml           # 未來選定的精確 runtime 依賴
    src/workbench/
      api/                   # request/response 與 readiness
      core/
        schema.py            # normalized model；匯出 JSON Schema
        validation.py        # spans、references、graph invariants
        pipeline.py          # 階段呼叫、timeout、partial result
        reconciliation.py    # 明確來源政策
      adapters/
        base.py              # contract 與 manifest
        french/              # parser wrapper、mapping、pack resources
        japanese/            # parser wrapper、mapping、pack resources
        english/             # 可選 parser-only 擴充；M1 後才建立
      enrichment/            # 一個 provider wrapper、prompt、候選 schema
    tests/
      contract/
      adapters/
      reconciliation/
  frontend/
    src/                     # input、segments、word detail、construction
    tests/                   # 少量主要互動案例
  schemas/                   # 從 backend 產生，禁止手改的 JSON Schema
  eval/
    cases/                   # 原創句子、現象標籤、人工判斷／允許替代
    reports/                 # 版本、數據、已知限制
  THIRD_PARTY_NOTICES.md     # M0 完成 artifact 盤點後建立
```

使用單一 repo、單一 backend、單一 frontend。不先建立 plugin marketplace、共用套件發行、資料庫、vector store、訊息佇列或微服務。Core 不能 import french/japanese 的內部模型；registry 是唯一將 pack 接上 core 的地方。模型與辭典下載位置在 repo 外，禁止 commit 大型 artifacts。

## 2. Milestones 與出口條件

| 階段 | 工作／產出 | 完成條件 |
| --- | --- | --- |
| M0：工具 spike 與決策確認 | 目標 Windows CPU 環境、法／日模型各跑 6 個現象案例、artifact inventory、compatibility lock、實測報告 | 兩 pack 可共存；記錄原始切分／offset／欄位；縮合與日文切分可映射；無需 LLM。若做不到先修訂 stack 決策 |
| M1：共同資料骨架 | Pydantic model、匯出 JSON Schema、semantic validator、兩 adapter 的 parser-only payload | 兩語言都能經相同 core 回有效結果；含 au 多 word、Unicode、partial dependency；完整 JSON examples；無效參照不能外流 |
| M2：最小互動與 resources | 單頁 UI、capability API、法日各至少 2 個 reviewed resource entries | 點詞、head navigation、多 word segment、來源與 unknown 狀態可用；resources 有版本及適用條件 |
| M2E：可選英文 adapter | 法日 parser-only 流程通過 M1 後做英文模型 spike 與同一 schema mapping；M2 UI 可用後接上點詞互動與人工標記案例 | 不加入 gloss／翻譯／構式或 LLM；若拖慢 M4 就移到 M4 之後，不阻擋 MVP |
| M3：LLM enrichment 與 reconciliation | 單一 provider、結構化候選、gloss/翻譯/構式、分歧面板、timeout 降級 | 正常與關閉模型皆能 demo；schema-invalid／未知 ID 被拒；衝突不覆寫 parser；每次要求的缺漏可見 |
| M4：v0.1 demo 與品質報告 | 固定案例集、延遲／資源報告、少量端到端情境、示範影片或截圖、操作 README | 達成下列 gates；未達部分寫明限制或修正，不以展示兩句成功取代驗收 |

核心交付順序 M0 → M1 → M2 → M3 → M4；先看到法日 parser-only 的可互動結果，再加入 enrichment。英文 M2E 在 M1 通過後可進行，但不得阻擋 M4。中文最後，待 MVP 與英文的 adapter 經驗都可評估後再決定粒度與選型。每階段可獨立 review，不以一次做完所有 UI 元件為前置。

## 3. Evaluation：先衡量能解釋的差異

建立 24 個法日原創、人工審核案例（fr/ja 各 12），含主要展示句與邊界案例。英文若加入，另外建立至少 6 個人工標記案例，結果獨立報告，不混入法日 release gate。每語言 8 個供開發，4 個保留到 M4；報告各自結果，不將調過 prompt 的句子稱為 held-out。

每例記錄 input、現象、必要結構條件、可接受的分析替代、不可宣稱的結論、審核者與日期。句法歧義允許多種合理分析，不強迫唯一 golden tree。parser snapshots 作版本差異參考，不等同 gold。確切 head／POS 評估以人工標註的目標 words 為準，不能拿模型自己的輸出當答案。

案例覆蓋：
- French：直／彎引號 clitic、au/du、en/y 語境、agreement、auxiliary、否定、標點及 Unicode。
- Japanese：助詞、topic/subject、量詞、活用、politeness、缺省論元、混排及 Unicode。
- English（M2E）：縮合、雙賓語、引號與 emoji offset；使用者逐例註記預期與可接受替代。
- 共通故障獨立成 contract fixtures：缺欄位、重複 ID、跨句 edge、cycle、失敗 resource、LLM 無效 JSON／未知 ID／timeout、過長輸入。
- 非法輸入、故障注入屬軟體可靠性測試，不納入語言準確率分母。

## 4. 暫定 release gates

以下是目標，不是本輪測得的數據：

- 全部 fixtures 通過 schema 與 reference checks；有效分析不得含無效 ID/span。負例必須有預期 rejection／partial，非「測試沒 crash」就算通過。
- 24 例原文 span 全數正確回映；所有非空白文字可重現。關鍵 au 與 Japanese 示例必須能點選各分析單位。
- 人工標記的 lemma/UPOS/目標 dependency 分開報 exact match、分子／分母與允許替代；三項各目標 ≥ 90%，法／日分別計算。小樣本只作 release gate，不宣稱代表一般語言效能。
- 每語言至少 10/12 例達到預先定義的現象條件；其餘錯誤列為已知限制。兩句主要 demo 不得出現會誤導使用者的未標示核心錯誤。
- 在 enrichment on 的無故障 run 中，至少 22/24 例產生完整要求欄位；所有失敗必須可見，不能靜默空白。
- 人工審查不得發現未標示的臆造來源、把零主語確定成「我」、或把 syntactic function 等同唯一 thematic role。若發現，修正後重查相關案例。
- 分歧與 failure fixtures 100% 顯示預期來源／降級；parser-only 不依賴 LLM。
- 延遲與記憶體依 stack 文件的量測方式報告；總 timeout 行為符合 API contract。完整 enrichment warm p95 暫定 ≤ 25 秒，未達先調整範圍／模型與文件。

不要求 v0.1 提供論文等級完整 UD benchmark。先明確揭露樣本小、語域有限、未做廣泛準確率驗證，再以 corpus evaluation 擴充。

## 5. 完成後應有的 demo

輸入 `J'en ai acheté trois.` 與 `昨日、本を三冊買った。`，使用者能：

1. 看原文、gloss、自然翻譯。
2. 點詞看 morphology、dependency 與來源。
3. 看語言相關構式，不把所有語言壓成相同文法分類。
4. 查看預製故障／分歧情境，理解系統保留了哪個分析與原因。
5. 關閉 LLM，仍可操作基礎分析。

MVP 不包含 learner profile、SRS、完整 course system、帳號、學習進度追蹤、syntax-tree layout、發音／音訊、語意角色標註器、中文／台語 adapter 或跨語言比較。2026-09-26 使用者另同意加入瀏覽器本機的句子查詢快照及短期追問，不是帳號型學習紀錄或聊天歷史；範圍見 [閱讀流程](../reading-workflow.md)。英文 M2E 完成時，另示範一個英文句子的 parser-only 點詞分析；沒有完成不阻擋法日 MVP。其餘非目標沒有 placeholder endpoints，也不為了預留它們先建資料表。

## 6. 開始實作前的待決事項

不妨礙目前設計文件完成，但不可假裝已定案：

- M0：精確套件／模型／辭典版本、共存環境、artifact 條款與實測資源需求。
- M0：French Sequoia package 的可用版本及 MWT 行為；GiNZA 的實際 segmentation 與輸出欄位。
- M2：首批 curated entry 的參考來源、適用條件與人工審核；語言案例由具相關語言能力的人核對。
- M3：可用 LLM provider/model、成本預算與 prompt 基準；不建立通用 model marketplace。
- M2E（可選）：英文 spaCy 模型與 GiNZA runtime 相容性、實際欄位、人工案例；中文另待後續版本決定繁體／簡體、詞與字粒度，不列入 v0.1。
- 公開發行前：專案 license 與 notices；repo 保持原本私有設定。

任何改動到 Word/Segment 意義、offset 單位、Field.status 或來源優先政策，都先同步修改 schema、Adapter 與 UI 文件，再動實作；不允許三份規格各自演化。
