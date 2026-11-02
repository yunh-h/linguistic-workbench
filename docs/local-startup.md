# 固定本機配置與重新啟動（Linux／WSL）

**繁體中文** · [English](local-startup.en.md)

## 每次開機後

開啟 WSL 終端機：

```bash
cd /path/to/language-app
./scripts/start.sh
```

看到 `Application startup complete` 後開啟 <http://localhost:8000>。保持終端機開著；`Ctrl+C` 停止。這是手動啟動，沒有安裝開機服務、Docker 或背景 daemon。

不需要先 activate venv。腳本永遠使用專案的 `.venv/bin/python`，也可從其他目錄以絕對路徑執行。若 8000 已在使用，先停止原服務；腳本不會自動殺掉其他程序。

```bash
./scripts/start.sh --check        # 檢查設定、必要套件及法文模型檔，不啟動、不連 LLM、不寫 DB
./scripts/start.sh --parser-only  # 本次停用 LLM，不修改設定檔
```

`--check` 不保證模型能成功載入、資料庫完整或 LM Studio 連線成功；實際啟動後再查看畫面的模型 readiness。

## 固定位置

| 內容 | 專案內路徑 | 納入 Git |
| --- | --- | --- |
| Python 環境、日文模型及依賴 | `.venv/` | 否 |
| 法文 Stanza 模型 | `data/models/stanza/` | 否 |
| 歷史資料庫及 WAL sidecar | `data/workbench.sqlite3*` | 否 |
| 這台機器的設定 | `config.local.toml` | 否 |
| 設定範例與啟動腳本 | `config.example.toml`、`scripts/start.*` | 是 |

以上路徑相對於專案根目錄。新環境請依下方重建步驟安裝，不要直接搬用其他機器的虛擬環境。

## 修改本機設定

編輯 `config.local.toml`，格式見 `config.example.toml`。這是 TOML 資料檔，不會作為 shell script 執行；路徑相對於專案根目錄。

設定優先順序：啟動旗標（`--port`／`--parser-only`）→ shell 環境變數 → 本機設定 → 預設。既有 `WORKBENCH_DB_PATH` 等 export 會優先，啟動時會印出實際 DB／模型路徑；若顯示舊路徑，請移除舊 export 後再啟動。

沒有自動讀取 `.env`。API key 請透過環境變數設定，不要放在 TOML 或 Git 裡。直接執行 uvicorn 不會讀 TOML；日常請用 `start.sh`。

服務預設只綁 `127.0.0.1`。此 app 沒有帳號隔離；不要直接改成對外開放。

## LM Studio

使用前在 `config.local.toml` 設定模型 ID 與可從 backend 存取的服務網址。未開 LM Studio 時仍可啟動工作台，請不要勾選模型補充，或使用 `--parser-only`。

要使用模型時，在 Windows 開 LM Studio、載入模型並啟動 Server。重開機後如果 WSL 到 Windows 的 IP 改變，修改 TOML 的 `WORKBENCH_LLM_BASE_URL` 後重啟工作台。`start.sh` 不會自動載入模型或探測其他網路位址。

連線診斷仍使用環境變數，不會自行讀 TOML，例如：

```bash
WORKBENCH_LLM_PROVIDER=lmstudio \
WORKBENCH_LLM_BASE_URL=http://127.0.0.1:1234/v1 \
WORKBENCH_LLM_MODEL=qwen/qwen3-14b \
.venv/bin/python scripts/check_lmstudio.py
```

以上 localhost 範例僅適合同一網路環境；若 backend 與 LM Studio 位於不同環境，請改成 backend 可連到的服務位址。更多說明見 [LM Studio](lmstudio.md)。

## 換機或環境損壞時重建

日常重啟不用重新安裝。新機器需要 Python 3.12 與 venv 支援（Ubuntu 套件為 `python3.12-venv`），依 [README](../README.md#本機啟動) 的初次安裝步驟建立 `.venv` 與下載法文模型，再從 `config.example.toml` 建立本機設定；不要覆寫既有的設定或 SQLite。不要將其他路徑／機器的整個 `.venv` 直接搬過來使用。

歷史資料需另外備份，方法見 [SQLite 指南](sqlite-history.md)。Git 不會替你保存 DB 或本機設定。
