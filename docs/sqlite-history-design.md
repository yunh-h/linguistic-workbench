# SQLite 句子歷史設計

狀態：已依本設計接入 SQLite；瀏覽器舊紀錄須由使用者在原瀏覽器按匯入。操作見 [SQLite 歷史指南](sqlite-history.md)。參考 DDL：[sqlite-schema-v1.sql](sqlite-schema-v1.sql)，runtime 使用 `backend/src/workbench/migrations/001_history.sql`。範圍為單人、本機工作台的句子查詢歷史；既有聊天維持短期記憶體狀態。

## 1. 資料模型

採兩張表，把可搜尋的原文與不可變的分析快照分開。現在的輸入允許一個短段落／最多五句，因此 entry 代表「一次查詢的完整原文」，不是 parser 切出的單句。

```text
history_entries（同語言＋同原文一筆）
    1 ─── N analysis_snapshots（每次重新分析一筆）
                  └─ result_json：完整 Analysis
                     sentences / words / dependency / annotations /
                     sources / disagreements / warnings
```

| 表 | 欄位 | 用途 |
| --- | --- | --- |
| history_entries | id | DB 內部整數主鍵 |
| | language | fr／ja／en |
| | original_text | 原樣保存文字，保留空格、大小寫、換行與 Unicode 組合形式 |
| | created_at_ms | 首次寫入此 DB 的 UTC Unix 毫秒 |
| analysis_snapshots | id | 快照內部主鍵，同時間戳時作排序 tie-breaker |
| | entry_id | 外鍵；刪除 entry 時連同所有快照刪除 |
| | analysis_id | 現有 Analysis.analysis_id，唯一；用於重試／匯入去重 |
| | schema_version | Analysis 格式版本，例如 0.1.0 |
| | status | complete／partial；不是語言正確性評分 |
| | enrichment_requested | 當次是否要求補充，on／off；on 不等於 LLM 成功 |
| | origin | app 或 browser_import；由 API 路徑決定 |
| | observed_at_ms | 查詢保存時間；app 用伺服器收到保存要求的時間，匯入用原 saved_at |
| | stored_at_ms | 此快照首次寫入 DB 的伺服器時間 |
| | result_json | 完整 Analysis JSON，包含原文、parser／LLM 來源與失敗原因 |

DB 的 `PRAGMA user_version=1` 是資料庫結構版本，與 Analysis.schema_version 分開。

實作採 AUTOINCREMENT 整數主鍵，避免清空後重用舊 ID，讓已開啟的歷史連結與匯入回執不會誤指向新紀錄。

不把 words、依存邊、gloss 各拆成表：目前主要操作是重新呈現整份分析，沒有跨所有句子搜尋某個 dependency 的需求。JSON 正好保留既有 schema、offset、多詞片段與來源；原文／語言／時間／狀態則可直接查欄位。若日後需要詞彙統計，再建可由快照重建的索引表。

## 2. 去重與版本規則

1. `UNIQUE(language, original_text)` 使用精確、區分大小寫的比對。不 trim、不做 Unicode normalization、不壓縮空白，避免改變既有字元 offset。API 另拒絕全空白、NUL、超過 500 Unicode 字元或五句的輸入。
2. 相同原文重新分析，新 analysis_id → 新快照。列表每個 entry 只出現一次，預設開最新快照，詳情可選「先前分析」。最新是 partial 也照實顯示，先前 complete 仍保留。
3. 相同 analysis_id 再次保存：驗證後 canonical Analysis JSON 與 enrichment_requested 都相同則回傳既有快照；不新增、不改時間、不置頂。內容不同回 409 `HISTORY_ID_CONFLICT`，不靜默覆寫。
4. canonical JSON 由目前支援的 Analysis validator 輸出，再以固定 key 排序／分隔符序列化；這是語法去重，不改寫語言內容。不要用有碰撞風險的短 hash 取代原文唯一約束。
5. 快照建立後不可修改；第一版沒有修改快照的 API。重分析是新增版本，刪除是明確操作。未來收藏／標籤可掛在 entry 上。
6. 資料庫不沿用 localStorage 的 50 筆自動淘汰；列表每頁 50 筆、最多 100 筆，保存的資料不因翻頁或數量增加而自動刪除。先不做版本上限或自動清理。

## 3. 寫入與讀取流程

保留目前的互動語意：分析回應抵達目前有效的前端請求、使用者開啟保存時，前端才呼叫新的保存 API。parser／LLM 執行時不持有 DB transaction；換句子後才到達的舊分析不發保存要求。

`POST /api/v0.1/history` 收 `{analysis, enrichment_requested}`。backend 執行 `Analysis.model_validate`、`check_snapshot`／語意引用驗證，衍生原文、語言、狀態、schema_version 等欄位，再開始短寫入交易：

1. `BEGIN IMMEDIATE`；先查 analysis_id 並處理重複／衝突。
2. 對 entry 用 `INSERT ... ON CONFLICT(language, original_text) DO NOTHING`，取得 entry_id。
3. 確認 snapshot.input 與 entry 的原文／語言完全相符，再插入快照。
4. commit，回傳 entry_id、snapshot_id、是否新建；任一失敗 rollback，不留下空 entry。

SQL 的 JSON CHECK 僅檢查 JSON 有效與部分欄位一致；Pydantic／現有語意 validator 負責 IDs、offset、依存圖及跨表原文一致性。所有 SQL 使用參數綁定。前端送回的 Analysis 視為不可信快照；通過驗證不表示已證明模型來源或語言內容真實，origin 只表示匯入途徑。

保存錯誤單獨顯示「分析完成，但尚未保存」，保留目前畫面並提供重試；不把 DB 寫入失敗混入語言分析的 warnings，也不悄悄另存 localStorage。回應遺失時可用相同 analysis_id 重試。保存要求已送出後才取消等待，DB 可能已 commit；取消不會反向刪除紀錄。關閉保存只影響未來保存要求，既有紀錄仍可閱讀。

列表只取原文與最新快照摘要，點開才載入 result_json。最新快照由 `(observed_at_ms DESC, id DESC)` 決定，不另維護容易失同步的 latest_snapshot_id／查詢次數。關聯最新快照的查詢基準：

```sql
SELECT e.id AS entry_id, e.language, e.original_text,
       s.id AS snapshot_id, s.status, s.enrichment_requested,
       s.observed_at_ms
FROM history_entries AS e
JOIN analysis_snapshots AS s ON s.id = (
    SELECT a.id FROM analysis_snapshots AS a
    WHERE a.entry_id = e.id
    ORDER BY a.observed_at_ms DESC, a.id DESC LIMIT 1
)
ORDER BY s.observed_at_ms DESC, s.id DESC
LIMIT :limit;
```

後續頁加 `(s.observed_at_ms, s.id) < (:cursor_time, :cursor_id)`；cursor 由服務端編碼／驗證。並行重分析會把 entry 移到前頁，重新整理可見；此列表不宣稱是跨多頁的交易快照。語言／最新狀態可過濾，原文搜尋先採 `instr(original_text, :q) > 0` 精確子字串，適用日文且不把 `%` 當 wildcard。第一版不建 FTS，搜尋可能掃描原文，待實際資料量再量測。

## 4. API

| API | 行為 |
| --- | --- |
| POST /api/v0.1/history | 保存有效 Analysis 快照；新建 201，重複 200，ID 衝突 409 |
| GET /api/v0.1/history?limit=50&cursor=…&language=…&q=… | 每 entry 最新快照摘要＋next_cursor |
| GET /api/v0.1/history/{entry_id} | 原文、最新快照摘要，不含所有版本的 JSON |
| GET /api/v0.1/history/{entry_id}/snapshots | 分頁列出版本摘要 |
| GET /api/v0.1/history/{entry_id}/snapshots/{snapshot_id} | 返回指定 Analysis；確認 snapshot 屬於該 entry，無推論 |
| DELETE /api/v0.1/history/{entry_id} | 刪掉原文及所有版本，UI 明確提示版本數量 |
| DELETE /api/v0.1/history | UI 確認後清空全部句子／版本；保留 DB 結構 |
| POST /api/v0.1/history/import | 匯入瀏覽器現有紀錄，回傳逐筆結果 |

單筆 JSON 延續 256 KiB 上限，enrichment_requested 必須是 on／off；未知 DB ID 404，快照 schema 不支援回明確相容性錯誤，不能擅自重跑替換。讀快照仍經 Analysis 驗證。原有 `/analyses/restore` 在遷移期可保留相容，但新 DB 讀取走 ID。

清空與保存若同時發生，依 SQLite 交易提交順序：清空之後提交的新增會保留。UI 清空期間停用同頁保存操作；不同分頁仍依此規則，不宣稱永久禁止新資料。

## 5. localStorage 匯入

瀏覽器才能讀自己的 localStorage，因此 DB 建好不會自動看見舊資料。UI 偵測 `workbench.sentence-history.v1` 後顯示筆數，由「匯入舊紀錄」按鈕送到 backend；不同網址／瀏覽器的舊資料需分別在原來源開啟與匯入。

- 沿用 `{id, saved_at, enrichment, analysis}`：id 必須等於 analysis.analysis_id；enrichment 對應 enrichment_requested；saved_at 解析為 observed_at_ms，origin 固定 browser_import。
- saved_at 必須為有效含時區時間，非負且不超過伺服器時間五分鐘；不合法回逐筆錯誤，原件保留，避免錯誤未來時間長期霸佔列表。沒有可靠的模型實際完成時間，因此欄位不稱 analyzed_at。
- 每批最多 10 筆、HTTP body 最多 3 MiB，每筆快照仍最多 256 KiB；前端可分批完成原有最多 50 筆。每筆用上述獨立交易，回 `imported / duplicate / invalid / conflict / failed` 與對應 ID／安全錯誤原因。
- 同 ID、同有效內容的重複匯入不新增快照，也不改排序時間；較舊快照不蓋掉較新快照。快照 UUID 相同但內容不同時保留 DB 與瀏覽器兩邊，報衝突。
- 只有全部 imported／duplicate 且可從 DB 讀回，UI 才提供「刪除此瀏覽器舊副本」選項；不自動清除。匯入中斷可以重試，不能用「曾匯入」布林值略過仍失敗的紀錄。

保存偏好暫留瀏覽器，句子／分析搬入 DB；不同瀏覽器可各自選擇是否保存，但查閱的是同一 backend 的共同歷史。此版本定位單人使用，沒有使用者隔離。

## 6. 檔案、連線與備份

建議用 Python 標準庫 sqlite3，先不引入 ORM。設定 `WORKBENCH_DB_PATH`；預設由專案根目錄解析為 `data/workbench.sqlite3`，不依賴 shell 當前路徑。實作時將 data DB、`-wal`、`-shm` 與備份排除於 Git。WSL 環境使用 Linux 本機檔案系統，正式資料不放 `/tmp`。

每次 DB 操作使用短生命週期 connection，在同一 thread 內開啟、交易與關閉；async route 以 thread offload 執行阻塞 DB 操作。不共用一條全域 connection，也不讓 parser 子程序寫 DB。每條 connection 在交易外設定 foreign_keys=ON、busy_timeout=5000、synchronous=FULL；初始化在交易外啟用 journal_mode=WAL 並檢查結果。WAL 允許讀寫並行但仍只有一個 writer；預期為單機低流量用途。[SQLite 外鍵文件](https://www.sqlite.org/foreignkeys.html)、[WAL 文件](https://www.sqlite.org/wal.html)。

`user_version` 管理依序 migration：空 DB 執行 v1 DDL、成功後才更新版本；已有 v1 不重跑，較新版本拒絕寫入。schema 升級前備份，migration 失敗 rollback；不得遇到錯誤就刪 DB 重建。DB 不可用時 parser／追問仍可運作，history API 回安全的 503 與保存失敗訊息。正式檔案須先確認是否為 SQLite 與支援版本，不能覆蓋同名其他檔案。

備份使用 sqlite3 Connection.backup，產生一致的獨立 DB 檔；WAL 活躍時不只複製主檔。第一版提供本機備份指令，目的路徑已存在就拒絕覆寫。復原在停止 backend 後進行，先保留原 DB，檢查 integrity_check／foreign_key_check／user_version，再置換。備份是手動操作，不宣稱已有自動排程或雲端備份。[SQLite Backup API](https://www.sqlite.org/backup.html)。

## 7. 實作順序與驗收

1. repository 層與 v1 migration：插入、精確去重、不同分析版本、衝突、rollback、cascade；建立備份／復原操作說明。
2. history API：分頁與搜尋、讀取快照、刪除、儲存失敗不影響分析；前端切換 DB 歷史。
3. 舊紀錄匯入：重複批次、部分失敗、舊時間排序、跨瀏覽器來源與保留副本。
4. 瀏覽器驗收：保存選項、重開無推論、最新 partial 與舊 complete 都可讀、取消不保存未送出的結果、刪除所有版本、backend 重啟後仍存在。

已加入磁碟 WAL、並行去重、版本保存、API、匯入／刪除、備份與瀏覽器操作測試。localStorage 現僅保存偏好與尚未由使用者清除的舊副本。單次保存 HTTP body 上限 264 KiB（包含 envelope），canonical Analysis JSON 仍限制 256 KiB。
