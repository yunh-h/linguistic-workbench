# French / Japanese MVP parser & resource decisions（英文擴充）

狀態：2026-09-23 的設計選型，未安裝、未 benchmark。以下區分「上游可提供的功能」與「本專案尚須實測的承諾」。

實作更新（2026-09-26）：Linux／WSL CPU 對照後，法文採 Stanza 1.11.0 **combined**，取代原先 Sequoia 首選；日文保留 GiNZA。採用 package 的 processors 為 combined tokenizer/mwt、combined_charlm POS/depparse、combined_nocharlm lemma，不使用 transformer。結果、版本、速度與已知錯誤見 [選型報告](../../eval/2026-09-26-parser-comparison.md)。以下其餘條目仍是原設計基準，不能視為全部驗收完成。

## 1. 決策摘要

| 層 | v0.1 選擇 | 理由與界線 |
| --- | --- | --- |
| Backend | Python 單一服務；建議 FastAPI、Pydantic | NLP 生態與 schema 驗證放同一端；API framework 是建議，非既有依賴 |
| French | Stanza 1.11.0 combined；tokenize/mwt/pos/lemma/depparse | Sequoia 對照後改選 combined；au/du mapping 與開發例 offset 已驗證，仍需完整人工語言品質驗收 |
| Japanese | GiNZA standard model ja_ginza + 它相容的 spaCy/SudachiPy/SudachiDict | 不選 transformer/ELECTRA 作第一個 baseline；保留模型預設切分並記錄實際設定 |
| English（可選擴充） | spaCy `en_core_web_sm` 為首個候選 | 沿用 GiNZA 所需的 spaCy runtime，提供 parser-only 分析；先驗證版本相容與輸出，不擋法日 MVP |
| Linguistic resources | 隨 pack 發行、人工整理的小型構式／gloss 資料；日文另取既有 Sudachi 資訊 | 不加入第二套 tokenizer；不依賴線上字典服務才可 demo |
| Enrichment | 單一可設定的結構化輸出 LLM provider | 只產生 gloss、翻譯、構式及衝突候選；固定模型／prompt 版本後評估 |
| Frontend | 建議 React + TypeScript，單頁 UI | 只消費 schema；不把 parser-specific 資料送前端 |

選擇不同 parser 是有意的：adapter abstraction 應吸收語言工具差異。代價是 Stanza 與 GiNZA 有不同 runtime／模型依賴；M0 若無法在一套環境鎖版，再評估替代方案，不預先拆成微服務。

## 2. French

[Stanza 官方文件](https://stanfordnlp.github.io/stanza/)列出斷句、MWT expansion、lemma、POS、morphology、dependency 能力。這支持把它列為法文首選，但不證明指定 French package 在本專案句子上的品質。

M0 必查：
- `J'en ai acheté trois.` 與 `J’en ai acheté trois.`：clitic 邊界、原文 offset、lemma；不能把 en 一律映成 obj。
- `Je vais au marché.`：au 表面 segment 與 à/le 分析單位如何對應；確認 MWT processor 實際是否啟用。
- `Je lui en parle.`、`Il y en a trois.`：en/y 不同語境，避免僅靠詞形產生相同解釋。
- `Elle est arrivée.`、`Elles ont acheté des livres.`：auxiliary、agreement、morphology 的輸出覆蓋。
- 縮合／partitive `du` 不以手寫全域 replacement 展開。

French curated resource 首批只涵蓋 en/y 的形式提醒、常見代詞位置與複合時態說明。每條 entry 至少有 id、revision、適用的形式／局部依存條件、限制、原創短說明、reference URL 或人工 review 記錄。未符合條件時不套用。M0/M2 才編寫與校閱條目；不能把 LLM 產生的內容改標成 dictionary。

暫不整合大型法語字典、IPA、liaison、字形還原以外的發音分析。lemma 先用 parser；lexical gloss 通常是 LLM 補充，UI 不稱它為權威字典義。

## 3. Japanese

[GiNZA 官方 README](https://github.com/megagonlabs/ginza)提供基於 spaCy 的日文 UD 分析，並使用 Sudachi 相關元件；有 standard ja_ginza 與 transformer 模型選項。MVP 選 standard 以降低初始執行需求，效能仍待量測。

保留該模型的預設 segmentation，不為了 UI 在 parse 後合併 words；「買った」可透過 span／construction 聚合閱讀。鎖定並記錄實際 split mode、dictionary edition/version、GiNZA model；不可假定所有環境的預設一致。

[舊 SudachiPy repository](https://github.com/WorksApplications/SudachiPy)已指出 0.6 以上移至 [Sudachi.rs](https://github.com/WorksApplications/sudachi.rs)。實作依 GiNZA 相容依賴解析，不從 archived 舊 repo 複製 tokenizer。辭典版本另見 [SudachiDict](https://github.com/WorksApplications/SudachiDict)。

M0 必查：
- `昨日、本を三冊買った。`：本/を、數量／助數詞、買っ/た 的實際切分與依存。
- `私は学生です。` 與 `私が読みました。`：は/が 不直接等同固定 topic/subject 語意角色。
- `本を読んでいます。`：活用／助動詞與可用 reading。
- `食べた。`：不可生成一個「我」的 word；翻譯若補主詞，需說明語境不明。
- 假名、漢字、全形數字、emoji 混排，檢查原文回映。

resource 利用既有 tokenizer 辭典可取得的 reading、normalized form、活用資訊；API 欄位可得性由 M0 檢查，不可保證每 token 都有。normalized form 獨立於 lemma 保存。Curated entries 先處理格助詞提示、topic 提醒與省略論元說明；pitch accent、零論元解析、完整敬語系統都不進 MVP。v0.1 不整合 JMdict，避免增加詞義選擇、資料散布與 tokenizer 對齊工作。

## 4. English parser-only 擴充

英文讓使用者比較容易直接判斷分析哪裡不合理，也能檢查第三個 adapter 是否真的沿用同一資料契約。[spaCy 官方英文模型列表](https://spacy.io/models/en)列出 `en_core_web_sm`；確切可用欄位與模型版本在英文 spike 才確認，目前尚未實測。

英文 pack 只提供斷詞、lemma、UPOS、morphology（有資料時）、basic dependency、原文 offsets 與來源；不加入 lexical gloss、翻譯、構式資源或 LLM enrichment。法日 parser-only 流程通過 M1 後才開始 parser spike，M2 UI 完成後接上點詞互動；若加入英文使 M4 延後，先保留為獨立擴充。英文沒有語言專屬 extensions；縮合形式與標點仍須通過共通 schema 驗證。測試至少包含 `I haven't seen it.`、`She gave him a book.`、句首引號與 emoji offset；由使用者人工標註哪些分析可接受，避免只憑印象判斷。

## 5. 替代方案與切換條件

- French spaCy `fr_core_news_sm`：可共用日文底層框架；[官方 model card](https://spacy.io/models/fr)列出 morphologizer、parser、lemmatizer。未選為初始方案，因本產品要優先驗證法文縮合／分析單位表示。若 Stanza 安裝／CPU 延遲不符門檻，在同一案例集比較後才切換；不能憑套件大小推論品質。
- Japanese Stanza：可能簡化為同一 parser framework，但需重新驗證日文切分與資源欄位映射；當 GiNZA 相容性阻礙單機 demo 時才評估。
- GiNZA ELECTRA、第二個 parser ensemble、即時 web retrieval：沒有 baseline 結果前不加入。
- LLM-only：不符合產品核心，不作 fallback。

## 6. Artifact、授權與 reproducibility

不是用一個套件 license 代表所有模型／辭典。實作 M0 建立法日 artifact inventory；英文 spike 再追加其 package/model 的精確版本、下載來源、checksum、license URL、所需 notices、是否可再散布；不將權重或辭典直接 commit。

上游 Stanza 專案標示 Apache-2.0，GiNZA library/model 標示 MIT；仍須分別核對選定模型與底層 resource 條款（見各官方頁）。本文件不代表完成再散布審查；本 repository 也尚未選定專案 LICENSE。MVP 先使用本機安裝的 artifacts，不散布 corpus 原始資料。

Python 3.11 是第一個環境候選，非已驗證相容矩陣。M0 在目標 Windows CPU 環境解析精確版本並鎖定；如果失敗，記錄失敗原因再選可共存版本。禁止以未鎖版的 latest 當可重現基準。

LLM provider/model 的確切選擇在 M3 以既有可用帳號與小型評估決定；此時只要求 schema output、可設定 timeout、可記錄實際 model ID。沒有 key 不阻擋 M1/M2，也不要求使用者先購買服務。

## 7. 決策驗證門檻

本輪沒有測得任何速度或準確率。後續以 CPU、單句與上限短段落各量 20 次 warm runs，記錄硬體、模型冷啟動時間、p50/p95 延遲與 peak RAM。暫定 parser warm p95 ≤ 3 秒／一般單句、上限輸入 ≤ 8 秒；不符合先縮輸入或選更小模型，勿先擴架構。

語言品質看人工審核的 phenomenon cases，不只看 schema 通過率；具體案例數與 release gates 見 [milestones](05-implementation-plan.md)。
