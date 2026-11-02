# 🔎 Linguistic Workbench

**把句子拆開，看見語言的結構。**

一個本機語言分析工作台，為語言學習者與語言學愛好者打造。輸入法文或日文句子，探索逐詞分析、句法關係與資料來源，也可以選擇透過語言模型補充翻譯與 gloss。

**繁體中文** · [English](README.en.md)

> 🚧 **v0.1 本機試用版**
>
> 工作台在本機執行，透過瀏覽器操作。分析與模型輸出可能有誤，尚未完成廣泛的語言品質評估。

## ✨ 可以做什麼？

- **看懂句子結構**：分析法文與日文的斷詞、詞形、詞性及依存關係，點選詞語查看細節與來源。
- **選擇模型補充**：連接 LM Studio 本機模型或 OpenAI API，取得翻譯、逐詞 gloss，並針對句子追問。
- **留下自己的版本**：保存句子與分析歷史，編輯「我的翻譯」，方便以後回來查看。
- **帶走學習筆記**：將單筆、多選或全部歷史匯出成 Markdown；每句優先使用個人翻譯，否則使用模型翻譯。

💡 沒有連接語言模型也可以使用 parser 分析；翻譯、gloss 與追問則需要可用的模型服務。英文是需另行安裝模型的選用 parser-only 擴充。

## 🧭 目前的限制

- 目前主要在 **Linux／Windows WSL、Python 3.12** 環境測試；Windows 原生及 macOS 尚未完成完整安裝和模型流程驗證，也還沒有一鍵安裝包。
- Parser 與 LLM 都可能出錯；資料格式驗證通過不代表分析或翻譯正確。
- 自動 LLM 補充目前限於翻譯與 gloss；自動構式與語法候選已暫停。追問對話不保存，也不會匯出。
- 句子歷史存於本機 SQLite，同一個 backend 的使用者會共用資料，**沒有帳號隔離**。服務預設只監聽本機，請勿直接公開到網際網路。

<a id="本機啟動"></a>

## 🚀 快速開始

以下指令適用 Linux／WSL，請在專案根目錄執行。Ubuntu 需先安裝 Python 3.12 與 `python3.12-venv`。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install torch==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -c eval/constraints-linux-py312.txt -e './backend[dev]'
export WORKBENCH_STANZA_DIR="$(pwd)/data/models/stanza"
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
.venv/bin/python scripts/download_french_model.py --model-dir "$WORKBENCH_STANZA_DIR"
./scripts/start.sh
```

啟動後開啟 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**。日常啟動時，在專案根目錄執行 `./scripts/start.sh`；若遇到執行權限問題，可使用 `bash scripts/start.sh`。以 `Ctrl+C` 停止服務。

僅使用 parser：

```bash
./scripts/start.sh --parser-only
```

📦 安裝時需下載 Python 套件及語言資源；repository 不包含 parser 模型檔或 LLM。

## 🤖 模型設定

- **LM Studio：**載入模型並啟動服務，再設定 provider、模型 ID 與 backend 可連線的網址；詳見 [LM Studio 指南](docs/lmstudio.md)。
- **OpenAI API：**設定 `WORKBENCH_LLM_PROVIDER=openai`、`WORKBENCH_LLM_MODEL` 與 `OPENAI_API_KEY`；需要帳號與 API key，使用時可能產生費用。

localhost 僅適用 backend 可以透過該位址連到模型的情況；WSL 連接 Windows 時可能需要不同位址。

🔐 API key 請放在環境變數，不要提交到 Git；使用模型補充或追問時，輸入與相關分析資料會送至所選服務。

非憑證設定可參考 `config.example.toml` 建立 `config.local.toml`，請勿覆蓋既有設定；直接啟動 uvicorn 不會讀取此檔。

## 🧪 測試

```bash
.venv/bin/python -m pytest backend/tests -q
node frontend/tests/test_export.js
```

GitHub Actions 在 main 更新及指向 main 的 pull request 執行輕量測試，不下載語言模型、不啟動瀏覽器，也不呼叫 LLM。CI 通過不代表真實模型或語言品質已驗證。

## 📚 更多文件

- [本機啟動指南](docs/local-startup.md)
- [LM Studio 設定](docs/lmstudio.md)
- [閱讀流程](docs/reading-workflow.md)
- [歷史、備份與匯出](docs/sqlite-history.md)
- [資料結構與語言 adapter](docs/v0.1/01-normalized-schema.md)
- [Parser 比較紀錄](eval/2026-09-26-parser-comparison.md)

## 🤖 開發註記

本專案在開發過程中使用 OpenAI Codex 協助。

## 📄 授權

本專案使用 [MIT 授權](LICENSE)。第三方套件、模型與字典各有自己的授權與使用條款。
