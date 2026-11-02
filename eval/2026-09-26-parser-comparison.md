# French parser comparison — 2026-09-26

## 決策與範圍

法文預設由 spaCy `fr_core_news_sm` 3.8.0 改為 **Stanza 1.11.0 / combined**（tokenize、mwt、pos、lemma、depparse；CPU、非 transformer）。日文保留 GiNZA `ja_ginza` 5.2.0。原首選 Sequoia 的結構與速度通過，但此開發案例集仍有 lemma／auxiliary 錯誤；combined 改善這些具體例子，因此作為目前預設。這不是一般法文準確率排名，也不是 M0–M4 完整驗收。

12 個法文開發 probes 包含原有六句、彎引號、au/du、否定、emoji、分解重音及前後空白。句子與 expansion 預期在執行前建立；新增 combined 是觀察 Sequoia 結果後的適應性比較，沒有 held-out 成績或人工逐詞 gold。沒有 LLM 呼叫。

## 環境與方法

- WSL2 Linux x86_64、Python 3.12；硬體與作業系統細節基於隱私不公開，相關模型和套件版本保留於下方。
- PyTorch 2.8.0+cpu、Stanza 1.11.0、spaCy 3.8.16、GiNZA 5.2.1、ja_ginza 5.2.0、SudachiPy 0.6.11、SudachiDict-core 20260723；`pip check` 通過。
- 每 parser 使用獨立程序，依序執行。OMP/MKL threads=2；Stanza 另設 torch threads=2。load 包含 framework import 與模型載入，非清除 OS disk cache 的冷機量測。
- 每個負載先 warm-up，再跑 20 次。計時包含 parser、mapping、schema/semantic validation 與本地 resources；排除模型載入、HTTP/UI、LLM。p95 使用 nearest-rank，第 19/20 筆。
- 法文單句為 `J'en ai acheté trois.`；段落為五句、486 code points 文字加 14 個尾端空白，共 500 code points。全文與每次數值保留於 JSON。這是一個輸入上限 probe，不涵蓋所有最壞情況。
- RSS 為各程序 lifetime peak，包含 Python/framework/model/inference；不是單純權重大小，也不是法日共載記憶體。Windows 原生與目標機器尚需重測。

## 測得的結果

| 法文候選 | 結構／原文回映 | 指定 MWT checks | 單句 p50 / p95 | 500 code points p50 / p95 | 載入 | peak RSS |
| --- | --- | --- | --- | --- | --- | --- |
| spaCy sm 3.8.0 | 12/12 | 0/3 | 0.0022 / 0.0024 s | 0.0125 / 0.0134 s | 3.016 s | 466 MiB |
| Stanza Sequoia | 12/12 | 3/3 | 0.0690 / 0.0741 s | 0.5916 / 0.6836 s | 3.013 s | 781 MiB |
| Stanza combined | 12/12 | 3/3 | 0.0563 / 0.0688 s | 0.5437 / 0.5728 s | 3.187 s | 810 MiB |

三個指定 MWT checks 是一般 au、介詞 du、emoji 之後的 au。partitive du 未強制指定語意或依存答案。結構成功只證明 span/reference/graph 合約，不證明分句、lemma、詞性或 dependency 正確。兩個 Stanza 候選均達到此次 CPU 探針的單句 p95 ≤ 3 s、段落 p95 ≤ 8 s 預算。

日文六個既有案例均通過結構驗證，實際主展示句仍切成 `昨日 / 、 / 本 / を / 三 / 冊 / 買っ / た / 。`。單句 p50/p95=0.0125/0.0131 s；另跑的日文五句段落只有 39 code points，p50/p95=0.0186/0.0194 s，**不是日文輸入上限驗收**。載入 1.769 s，peak RSS 590 MiB。

切換預設後，`WORKBENCH_TEST_MODELS=1` 的完整測試為 **25 passed**，含真實法文 12 例及同一 API 程序的法文→日文→法文切換；模型缺失的 503 行為也有回歸測試。另有一則 Starlette TestClient/httpx 棄用提示，未造成測試失敗。

## 可直接核對的分析差異

| 句子／目標 | spaCy sm | Stanza Sequoia | Stanza combined |
| --- | --- | --- | --- |
| Je vais au marché. | au 為單一 word | à / le，共用 au segment | à / le，共用 au segment |
| Je lui en parle. | lui→luire；en=ADP；parle=ADJ、lemma=parl | lui/en=PRON；parle=VERB、parler | 同左核心改善；en dependency 仍需審核 |
| Il y en a trois. | Il 為 root，a 為 fixed | a/avoir 為 root | a/avoir 為 root |
| Elle est arrivée. | arrivée=NOUN；est=cop | arrivée=VERB/arriver；est=aux:pass | arrivée=VERB/arriver；est=aux:tense |
| Je bois du café. | bois→boire | bois→bois | bois→boire |
| Je n’en veux plus. | n’=VERB；veux=ADJ | veux=VERB，但 lemma=vouvoir | n’=ADV/ne；veux=VERB/vouloir |

保留原始結果，沒有加入句子特例、手改模型 lemma 或把錯誤藏成 resource 修正。Stanza 的原文切分、lemma、UPOS、morphology、dependency、offset 與來源在 JSON 中可逐欄檢查。

### 已知限制

- `Je bois du café.` 的 café 仍被標成 `obl:arg`；不能因 du 展開成功就宣稱已解決 partitive 的分析。
- `en/y` 的 dependency 在候選間不同；未以人工 gold 決定唯一正解。
- emoji 被標成 PUNCT，emoji 和結尾引號可能各成一句。Unicode 回映正確不等於分句與詞性正確。
- 分解重音案例可回映，但 café 的 lemma 保留分解形式，du 的 dependency 分析也不同；不偷偷 normalization 原文。
- 代名詞 lemma 的正規化受 treebank 慣例影響。`Je → moi`、`Elle → lui` 不應只因不符合教學詞頭就直接算模型錯誤；需對應語料規範與 morphology。此前 M0 記錄對代名詞 lemma 的判斷已加註限制。參考 [UD French GSD](https://universaldependencies.org/treebanks/fr_gsd/) 與 [Sequoia](https://universaldependencies.org/treebanks/fr_sequoia/)。
- 本次比較未包含 24 例人工 review、held-out、真實 LLM、前端瀏覽器互動驗收或硬性 deadline／worker isolation。後續同日已完成 parser-only 瀏覽器與 deadline／worker isolation 驗收，見 [穩定性紀錄](2026-09-26-parser-only-stability.md)；其餘仍是 MVP 後續工作。

## 重跑與 artifacts

按 README 安裝；[constraints-linux-py312.txt](constraints-linux-py312.txt) 記錄本次環境的精確套件版本，不代表跨平台 lock。模型目錄必須在 repo 外。官方 resource manifest 固定 1.11.0，八個使用中的 artifacts 各有 SHA-256、大小與下載 URL，附於 Stanza JSON；下載時也核對 manifest 的 MD5。共用 pretrain/charlm 不必重複下載。

```bash
export WORKBENCH_STANZA_DIR="$(pwd)/../language-app-models/stanza"
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
.venv/bin/python scripts/download_french_model.py --model-dir "$WORKBENCH_STANZA_DIR"
.venv/bin/python scripts/download_french_model.py --model-dir "$WORKBENCH_STANZA_DIR" --package sequoia
WORKBENCH_STANZA_PACKAGE=combined .venv/bin/python scripts/compare_parsers.py --parser stanza --output /tmp/stanza-combined.json
WORKBENCH_STANZA_PACKAGE=sequoia .venv/bin/python scripts/compare_parsers.py --parser stanza --output /tmp/stanza-sequoia.json
.venv/bin/python -m pip install -c eval/constraints-linux-py312.txt https://github.com/explosion/spacy-models/releases/download/fr_core_news_sm-3.8.0/fr_core_news_sm-3.8.0-py3-none-any.whl
.venv/bin/python scripts/compare_parsers.py --parser spacy --output /tmp/spacy-fr.json
.venv/bin/python scripts/compare_parsers.py --parser ginza --output /tmp/ginza.json
WORKBENCH_TEST_MODELS=1 .venv/bin/python -m pytest backend/tests -q
```

初次安裝較大；實測 artifacts 與套件保存在 repo 外。spaCy wheel SHA-256 為 `7d6ad14cd5078e53147bfbf70fb9d433c6a3865b695fda2657140bbc59a27e29`，與此前紀錄一致。

原始觀察（模型選型階段輸出，不是 gold）：[spaCy](reports/2026-09-26-spacy-fr.json)、[Sequoia](reports/2026-09-26-stanza-sequoia.json)、[combined](reports/2026-09-26-stanza-combined.json)、[GiNZA](reports/2026-09-26-ginza.json)。最後接入時另保留 fr.clitic_type 與新增可見的模型限制提示；選型快照保留當時輸出。

來源：[Stanza 1.11.0 resource manifest](https://raw.githubusercontent.com/stanfordnlp/stanza-resources/main/resources_1.11.0.json)、[French model artifacts](https://huggingface.co/stanfordnlp/stanza-fr/tree/v1.11.0/models)、[GiNZA](https://github.com/megagonlabs/ginza)。這份 artifact 記錄不取代公開發行前的 notices／授權盤點。
