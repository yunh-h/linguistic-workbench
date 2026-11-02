# SQLite 歷史實作驗收

SQLite 已接入句子歷史。正式預設為專案根目錄 `data/workbench.sqlite3`，不再把新分析保存到 localStorage。既有瀏覽器原件由使用者按匯入後搬入，未自動讀取或清除使用者瀏覽器資料。

## 實作

- 兩張 STRICT 表，schema v1：精確語言／原文去重與多版本 Analysis JSON；snapshot_id／entry_id 不重用。外鍵連帶刪除、WAL、5 秒 busy timeout、短交易與 rollback。
- history API 保存、分頁／搜尋／語言／狀態過濾、讀回與刪除；同 analysis_id 同內容重試回既有記錄，衝突保留原件。快照讀寫均使用現有結構／語意引用驗證。
- 前端版本切換、保存失敗重試、關閉保存選項、搜尋與載入更多。歷史開啟不推論；聊天不入 DB。舊回應在輸入切換後不送保存要求，已送出之請求可能仍已提交。
- 瀏覽器舊紀錄分批匯入，逐筆成功／重複／失敗／衝突。清除舊副本前再次讀回對應快照並核對 analysis_id；原件變更或 DB 副本已刪除時保留原件。
- `scripts/backup_history.py` 使用 SQLite Backup API；唯讀檢查來源，目的檔獨占建立，不覆寫；提供 `--check` 完整性／外鍵／版本驗證。復原操作見指南。

## 證據

| 驗證 | 結果 |
| --- | --- |
| 完整 backend（含真實 Stanza／GiNZA、模擬 provider） | 246 passed，27.52 秒 |
| 最後補上主鍵不重用保護後的 SQLite 測試 | 27 passed，0.87 秒 |
| 最終 migration、新暫存 DB 的 Chromium 測試 | 26 passed，13.75 秒 |
| 隔離 backend 保存後停止／重啟 | 同 entry_id、snapshot_id、analysis_id 可讀回同一分析 |
| 服務運作時使用備份 CLI | 備份成功，讀回包含同一已提交快照 |
| frontend JS syntax、git diff whitespace | 通過 |

新增 DB 測試覆蓋磁碟重開、精確去重、重複寫入與衝突、寫入失敗 rollback 無空 entry、並行初始化／保存、分頁與日文／特殊字元搜尋、版本歸屬、cascade／清空／ID 不重用、外來／新版／不可用檔案保留、匯入失敗時間／部分成功／重試、WAL 備份、拒絕覆寫與缺失來源、API 大小／型別／ID／引用驗證。測試使用 tmp_path 資料庫，不接觸正式資料。

瀏覽器測試涵蓋跨瀏覽器讀取、50 筆分頁不淘汰、原文搜尋、完整／partial 版本切換、刪除確認、保存失敗重試、瀏覽器儲存被停用時 SQLite 仍可保存、壞舊快照拒收、可重試匯入、明確清理原件與副本已刪除時拒絕清理。保留先前點詞、來源／分歧、追問與逾時／晚到回應測試；手機截圖已檢視。

驗收服務為 127.0.0.1:8765，使用 `/tmp/language-app-sqlite-ui.5pTpwX/final.sqlite3`，與正式 8000 DB 分開。測試前重設只針對明確 opt-in 的隔離 DB。正式 DB 不插入測試句子。

本次依使用者安排讓 LLM 保持關閉，未發送真實 Qwen／Gemma 或雲端推論。模型相關流程使用 mock，parser 使用既有本機模型；沒有把 mock 結果當作語言品質測試。仍有一則既有 Starlette/httpx 棄用警告。

## 操作界線

這是單人本機歷史，所有連到同一 backend 的瀏覽器共享資料，沒有帳號／同步／自動備份排程。DB 不可用時歷史請求會明確失敗，分析結果仍可閱讀。未保存快照的重試佇列只在當前分頁記憶體中。

指南：[SQLite 歷史](../docs/sqlite-history.md)。設計：[schema 與 API](../docs/sqlite-history-design.md)。
