# M0 parser spike：初次實測紀錄

後續更新：2026-09-26 已完成 Linux／WSL 的 spaCy／Stanza Sequoia／Stanza combined 對照，法文預設改為 combined；見 [選型報告](2026-09-26-parser-comparison.md)。以下保留 2026-09-24 的初次觀察。代名詞 lemma 受 treebank 正規化慣例影響，下表的 `Elle/Elles → lui` 不應單獨當成錯誤證據；詞性／動詞 lemma 問題仍需分開判斷。

日期：2026-09-24。環境：Windows、Python 3.12、CPU；spaCy 3.8.16、`fr_core_news_sm` 3.8.0、GiNZA 5.2.1、`ja_ginza` 5.2.0、SudachiPy 0.6.11、SudachiDict-core 20260723。模型與辭典由套件／官方模型來源安裝，沒有放進 repository。法文模型 wheel 的 SHA-256 為 `7d6ad14cd5078e53147bfbf70fb9d433c6a3865b695fda2657140bbc59a27e29`。

以 `scripts/run_spike.py` 跑 `eval/cases/spike.json` 的法日各六句。12 句皆能產生符合 v0.1 schema 的資料；這只代表結構有效，**不代表語言分析正確**。下列判讀是針對顯而易見的輸出問題，尚未有語言專家逐詞 gold annotation。

| 案例 | 觀察 | 結論 |
| --- | --- | --- |
| `J'en ai acheté trois.` | `J'`、`en` 分開；lemma／UPOS／dependency 有輸出 | 仍需人工審核 `en` 的依存角色與 gloss |
| `Je vais au marché.` | `au` 維持單一 ADP word | 無法驗證設計中的 surface `au` 對應 `à` + `le`；需要 Stanza MWT 對照 |
| `Je lui en parle.` | `lui` lemma 為 `luire`；`en` 為 ADP；`parle` 為 ADJ、lemma `parl` | 明顯錯誤；法文 spaCy baseline 未通過品質門檻 |
| `Il y en a trois.` | 把 `Il` 當 root，後續數詞／動詞關係也可疑 | 需另一 parser 與人工審核 |
| `Elle est arrivée.` | `arrivée` 為 NOUN；主語 `Elle` lemma 為 `lui` | 明顯錯誤 |
| `Elles ont acheté des livres.` | 多數結構可讀，但 `Elles` lemma 為 `lui` | lemma 不能直接當可靠答案 |
| 六個日文案例 | 預設切分、UPOS、lemma、dependency 與原文 offset 均有資料；`Reading`／`Inflection` 是 GiNZA 專屬資料 | 已映射到 `extensions.ja`；語言正確性仍需人工審核 |

日文例句 `昨日、本を三冊買った。` 的分析單位為 `昨日 / 、 / 本 / を / 三 / 冊 / 買っ / た / 。`。這是 parser 的實際切分，不是 UI 自行合併。`食べた。` 沒有新增虛構主語 word。

目前程式以 spaCy 法文模型作**暫時可執行 baseline**，會顯示模型限制提示。原本設計首選的 Stanza tokenize/mwt/pos/lemma/depparse 尚未完成同環境測試；完成前，不應把法文分析品質或 MWT 展開列為已驗收。日文也尚未完成 emoji、混合字形、人工 gold review、20 次延遲與記憶體量測。LLM enrichment 已有資料契約與模擬回應測試，沒有進行真實 provider 呼叫。
