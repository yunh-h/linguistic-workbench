# 固定本機啟動配置驗收

日常啟動改為 `./scripts/start.sh`，使用專案 `.venv`、`data/models/stanza`、既有 `data/workbench.sqlite3`。本機設定在 Git 忽略的 `config.local.toml`，版本控制只保存範例與腳本。沒有新增 Docker、開機服務或自動啟動 LM Studio。

## 環境遷移

- 以系統 Python 3.12 建立新的 `.venv`，複製原已驗證的 site-packages，另外在新環境安裝 pip／wheel 並 editable install 本專案。未搬用舊 venv 的啟動腳本；套件管理可用，`pip check` 無相依性錯誤。
- 複製法文模型至 `data/models/stanza`，`diff -qr` 與原模型完全相同。Stanza、GiNZA 與 backend 的 import 路徑都在固定專案目錄；`sys.path` 沒有 `/tmp`。
- 舊 `/tmp` 檔案保留，沒有刪除。日常啟動不再依賴它們；瀏覽器測試的 Chromium 工具仍是獨立的暫存測試資產。
- 啟動腳本不下載、不自動執行模型推論。`--check` 只檢查設定、必要套件及法文模型檔；不是 DB 完整性或模型連線檢查。

## 驗證

| 項目 | 結果 |
| --- | --- |
| 新增啟動腳本測試 | 17 passed |
| 固定環境完整後端，含真實 parser | 264 passed，29.64 秒；一則既有 Starlette/httpx 棄用警告 |
| 固定環境完整 Chromium 測試 | 33 passed，16.55 秒 |
| 從 `/tmp` 用絕對路徑啟動腳本 | 成功；模型仍使用專案固定路徑，DB 環境覆寫仍生效 |
| `--check` 與 `--parser-only` | 成功，不啟動 LLM、不寫 DB |
| Bash 語法與 Git diff whitespace | 通過 |
| `.venv`、本機設定、模型、DB 與備份的 Git ignore | 通過 |

新測試涵蓋固定路徑、不受 cwd 影響、含空白路徑、缺少 venv 提示、shell／TOML／旗標的優先順序、未知欄位與型別拒絕、憑證值不回顯、TOML 不執行 shell，以及 check 模式不啟動服務／建立資料庫。

瀏覽器驗證使用獨立 `8765` 服務與 `/tmp/language-app-fixed-start.9RCBER/history.sqlite3`，測後已停止。切換正式 8000 服務前先做線上備份 `data/backups/before-fixed-launcher-20260927.sqlite3`。切換後法文／日文 readiness 均為 true，歷史 API 正常，並逐筆確認備份中的 2 份原分析快照仍存在且內容相同。沒有新增測試句子至正式 DB，也沒有呼叫真實 LLM。

使用方法見 [固定配置與重新啟動](../docs/local-startup.md)。重開機後 LM Studio 的 Windows 位址如有改變，仍需更新本機 TOML；未承諾自動偵測或自動啟動。
