# 使用 SQLite 句子歷史

句子與分析快照現已保存到 backend 的 SQLite。預設位置是專案根目錄下的 `data/workbench.sqlite3`。此路徑不受啟動命令的目前目錄影響。`data/` 與 SQLite sidecar 檔已排除於 Git。

## 使用方式

- 新分析完成後，開啟「保存查詢紀錄到本機資料庫」就會保存。完整／部分分析都可保存；模型關閉時，parser-only 與歷史功能仍可使用。
- 同語言、完全相同原文共用一筆句子；每次重新分析保留新版本。點歷史紀錄後，可以用「分析版本」切回較早的結果，不會重跑模型。
- 每頁顯示 50 個句子，可載入更多、依原文片段搜尋與篩選語言。資料庫不會因超過 50 筆而自動刪除資料。
- 同一 backend 的不同瀏覽器／網址都讀取同一 DB；保存偏好仍各自存在瀏覽器。切回分頁會重新讀清單，也可按「重新整理紀錄」。此版本是共用本機歷史，沒有帳號隔離。
- 刪除句子會連帶刪掉所有版本；清除全部會影響連到同一 backend 的所有瀏覽器，兩者都有確認。畫面已開啟的快照可能仍留在記憶體。
- 保存失敗不影響目前分析，畫面提供「重試保存」。未保存的結果只在分頁記憶體中，重新整理／關閉分頁前應先重試。已送出的保存請求即使取消等待，也可能已提交；重試相同 analysis_id 不會重複建立。
- 聊天、憑證、模型服務網址不寫進 history。Analysis 的來源與模型名稱仍隨快照保存。DB 是未加密的本機檔案，請按個人資料方式保管。

## 匯入舊瀏覽器紀錄

在原本存有 localStorage 的網址／瀏覽器重新整理頁面，按「匯入瀏覽器舊紀錄」。每批最多 10 筆，畫面顯示成功、重複與未匯入項目；壞資料或 ID 衝突會保留原件。匯入中斷可重試。

所有項目匯入並從 DB 讀回成功後，才會出現「刪除此瀏覽器舊副本」。按下後還會再檢查對應快照可讀且 ID 正確、瀏覽器原件未改變，才移除舊副本；不會自動清除。若之前分別使用 localhost 與 127.0.0.1，請各自在原網址匯入舊紀錄。新紀錄已不再受這個來源隔離限制。

## 指定路徑

啟動 backend 前可設定：

```bash
export WORKBENCH_DB_PATH="$PWD/data/workbench.sqlite3"
```

相對路徑以專案根目錄解析。更改設定會切換到另一個 DB，不會自動搬移資料。WSL 建議沿用 Linux 本機磁碟路徑，正式資料不要放 `/tmp`。請不要讓多台機器透過網路共用同一個 SQLite 檔案。

SQLite 由 Python 標準庫提供；需要 STRICT tables 與 JSON functions。啟動時初始化或升級 DB schema v2、WAL 與必要索引，並在每條連線啟用外鍵、5 秒鎖等待與 FULL 同步。初始化失敗只停用歷史功能，parser 與模型設定仍可使用；不相容／非 SQLite 檔不會被刪除重建。

## 我的翻譯與匯出

分析頁每句可輸入、保存個人翻譯，清空後再保存可移除。譯文獨立於模型分析，且同一原文的各個分析版本依相同的句子原文範圍共用；如果重新分析後斷句範圍改變，原本的譯文不會套到不同範圍，仍可在舊版本查看。即使停用自動保存，明確按下「保存我的翻譯」仍會保存該分析與譯文。

分析頁複製／下載會使用欄位目前內容（包含尚未保存的編輯）；歷史多選或全部匯出則使用已保存的個人譯文。每句優先採用個人譯文，沒有時採用模型譯文，並標示來源。全部匯出包括所有歷史紀錄，不受搜尋及語言篩選影響；多選可跨頁保留，兩者均匯出每筆紀錄的最新分析版本。匯出過程若有資料無法讀取，不會下載部分結果。Markdown 是可閱讀的筆記，不是可匯回的完整備份；追問不保存也不匯出。

首次啟用此功能會將 v1 SQLite 資料庫升級為 v2，保留既有快照與紀錄；舊版程式無法直接使用 v2 資料庫。原 v1 備份仍可檢查並於新版開啟時升級。

## 備份與復原

在 repository 根目錄執行，使用已安裝 backend 的 Python 環境；若尚未 editable install，可加 `PYTHONPATH=backend/src`。例：

```bash
mkdir -p data/backups
.venv/bin/python scripts/backup_history.py --output data/backups/workbench-2026-09-26.sqlite3
.venv/bin/python scripts/backup_history.py --source data/backups/workbench-2026-09-26.sqlite3 --check
```

備份指令預設讀取 WORKBENCH_DB_PATH／正式預設路徑，來源必須已存在。使用 SQLite backup API，可在 app 執行中備份，不只是複製主檔；目的檔已存在會拒絕覆寫。沒有自動備份排程，重要資料請另保留副本。

復原步驟：停止 backend → 對備份執行 `--check` → 把備份複製成新的工作檔，例如 `data/restored-2026-09-26.sqlite3` → 將 WORKBENCH_DB_PATH 指向這個新檔再啟動。先保留原工作檔與備份，確認句子／版本可讀後再決定是否移除原件。不要在 app 執行時替換 `.sqlite3` 或混用不同備份的 `-wal`／`-shm`。

## 實作位置與測試

實際 migration 在 `backend/src/workbench/migrations/001_history.sql`，repository 在 `history.py`，API 在 `history_api.py`。資料模型與 API 規則見 [設計文件](sqlite-history-design.md)。內部整數 ID 使用 AUTOINCREMENT，清空後不重用舊 URL 的 ID。

後端測試自動將 WORKBENCH_DB_PATH 指向每個測試自己的暫存 DB，也會移除真實 LLM 憑證；不用啟動 LM Studio。瀏覽器測試會刪除測試資料庫的全部紀錄，因此只能使用獨立測試 backend。先用臨時目錄啟動 8765，切勿指向日常使用的 DB：

```bash
# 第一個終端機：保留 README 的 parser 模型目錄等設定
export WORKBENCH_DB_PATH="$(mktemp -d)/workbench-test.sqlite3"
unset WORKBENCH_LLM_MODEL OPENAI_API_KEY WORKBENCH_LLM_API_KEY
.venv/bin/python -m uvicorn workbench.api:app --host 127.0.0.1 --port 8765

# 第二個終端機：明確允許重設上述測試 DB
WORKBENCH_TEST_UI=1 WORKBENCH_TEST_DB_RESET=1 .venv/bin/python -m pytest frontend/tests -q
```

沒有 WORKBENCH_TEST_DB_RESET=1，使用 DB 的瀏覽器情境會跳過。所有持久化與模型 mock 測試都可在 LLM 關閉時執行；真實 Qwen 品質測試另行手動進行。
