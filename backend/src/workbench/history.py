"""SQLite snapshot repository. Every operation owns and closes its connection."""

from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import base64
import json
import os
import sqlite3
import threading
import time

from workbench.questions import check_snapshot
from workbench.schema import Analysis

ROOT = Path(__file__).resolve().parents[3]
MAX_SNAPSHOT_BYTES = 262144


class HistoryError(Exception):
    def __init__(self, status, code, message, retryable=False):
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def invalid(message="紀錄內容不完整或格式不符，尚未保存。"):
    return HistoryError(422, "HISTORY_INVALID", message)


def now_ms():
    return time.time_ns() // 1_000_000


def database_path():
    path = Path(os.getenv("WORKBENCH_DB_PATH", "data/workbench.sqlite3")).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def canonical_snapshot(value):
    try:
        analysis = value if isinstance(value, Analysis) else Analysis.model_validate(value)
        check_snapshot(analysis)
        if ("\0" in analysis.input.text or not 1 <= len(analysis.analysis_id) <= 128 or
                not 1 <= len(analysis.schema_version) <= 32):
            raise ValueError()
        encoded = json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise HistoryError(413, "HISTORY_TOO_LARGE", "這筆分析過大，尚未保存。")
        return analysis, encoded
    except (ValueError, TypeError, RecursionError):
        raise invalid() from None


def decode_cursor(cursor):
    if cursor is None:
        return None
    try:
        if len(cursor) > 200:
            raise ValueError()
        pair = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (not isinstance(pair, list) or len(pair) != 2 or
                any(type(x) is not int or not 0 <= x <= 2**63 - 1 for x in pair)):
            raise ValueError()
        return pair
    except (ValueError, TypeError):
        raise invalid("分頁資訊無效，請重新整理紀錄。") from None


def page_result(rows, limit):
    items = [dict(row) for row in rows[:limit]]
    cursor = None
    if len(rows) > limit:
        last = items[-1]
        cursor = base64.urlsafe_b64encode(json.dumps([last["observed_at_ms"], last["snapshot_id"]]).encode()).decode()
    return {"items": items, "next_cursor": cursor}


class HistoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self._initialized = False
        self._lock = threading.Lock()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA synchronous=FULL")
            return db
        except BaseException:
            db.close()
            raise

    def initialize(self):
        with self._lock:
            if self._initialized:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size:
                    with self.path.open("rb") as source:
                        if source.read(16) != b"SQLite format 3\x00":
                            raise HistoryError(503, "HISTORY_DATABASE_INVALID", "歷史資料檔格式不符，未覆寫原檔案。")
                db = self._connect()
                try:
                    db.execute("BEGIN IMMEDIATE")
                    version = db.execute("PRAGMA user_version").fetchone()[0]
                    if version not in (0, 1, 2):
                        raise HistoryError(503, "HISTORY_VERSION_UNSUPPORTED", "歷史資料庫版本不相容，請使用相容版本的工作台。")
                    if version == 0:
                        if db.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
                            raise HistoryError(503, "HISTORY_DATABASE_INVALID", "資料庫不是空白的工作台資料庫，未修改原資料。")
                        sql = (Path(__file__).parent / "migrations/001_history.sql").read_text()
                        statement = ""
                        for line in sql.splitlines(keepends=True):
                            statement += line
                            if sqlite3.complete_statement(statement):
                                db.execute(statement)
                                statement = ""
                        if statement.strip():
                            raise RuntimeError("incomplete migration")
                        version = 1
                    if version == 1:
                        sql = (Path(__file__).parent / "migrations/002_translations.sql").read_text()
                        statement = ""
                        for line in sql.splitlines(keepends=True):
                            statement += line
                            if sqlite3.complete_statement(statement):
                                db.execute(statement)
                                statement = ""
                        if statement.strip():
                            raise RuntimeError("incomplete migration")
                    db.execute("SELECT e.original_text, s.result_json FROM history_entries e LEFT JOIN analysis_snapshots s ON s.entry_id=e.id LIMIT 0")
                    db.commit()
                    if db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                        raise sqlite3.OperationalError("WAL unavailable")
                    self._initialized = True
                finally:
                    db.close()
            except (sqlite3.Error, OSError, RuntimeError):
                raise HistoryError(503, "HISTORY_UNAVAILABLE", "歷史資料庫暫時無法使用；目前分析仍可閱讀。", True) from None

    @contextmanager
    def connection(self, write=False):
        self.initialize()
        db = None
        try:
            db = self._connect()
            if db.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise HistoryError(503, "HISTORY_VERSION_UNSUPPORTED", "歷史資料庫版本不相容。")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except (sqlite3.Error, OSError):
            raise HistoryError(503, "HISTORY_UNAVAILABLE", "歷史資料庫暫時無法使用；目前分析仍可閱讀。", True) from None
        finally:
            if db is not None:
                db.close()  # Rolls back uncommitted writes on every failure path.

    def save(self, value, enrichment, *, origin="app", observed_at_ms=None):
        analysis, encoded = canonical_snapshot(value)
        if enrichment not in ("on", "off") or origin not in ("app", "browser_import"):
            raise invalid()
        stored = now_ms()
        observed = stored if observed_at_ms is None else observed_at_ms
        if type(observed) is not int or not 0 <= observed <= stored + 300000:
            raise invalid("紀錄時間無效或超出目前時間，未匯入。")
        with self.connection(write=True) as db:
            existing = db.execute("SELECT * FROM analysis_snapshots WHERE analysis_id=?", (analysis.analysis_id,)).fetchone()
            if existing:
                # Parse again so old optional defaults compare consistently.
                _, previous = canonical_snapshot(json.loads(existing["result_json"]))
                if previous != encoded or existing["enrichment_requested"] != enrichment:
                    raise HistoryError(409, "HISTORY_ID_CONFLICT", "相同分析 ID 已有不同內容，原紀錄已保留。")
                return {"entry_id": existing["entry_id"], "snapshot_id": existing["id"], "created": False}
            db.execute("INSERT INTO history_entries(language,original_text,created_at_ms) VALUES(?,?,?) ON CONFLICT(language,original_text) DO NOTHING",
                       (analysis.input.language, analysis.input.text, stored))
            entry_id = db.execute("SELECT id FROM history_entries WHERE language=? AND original_text=?",
                                  (analysis.input.language, analysis.input.text)).fetchone()[0]
            result = db.execute("""INSERT INTO analysis_snapshots
                (entry_id,analysis_id,schema_version,status,enrichment_requested,origin,observed_at_ms,stored_at_ms,result_json)
                VALUES(?,?,?,?,?,?,?,?,?)""", (entry_id, analysis.analysis_id, analysis.schema_version, analysis.status,
                                             enrichment, origin, observed, stored, encoded))
            return {"entry_id": entry_id, "snapshot_id": result.lastrowid, "created": True}

    def list_entries(self, limit=50, cursor=None, language=None, q="", status=None):
        pair = decode_cursor(cursor)
        where, args = [], []
        if language:
            where.append("e.language=?"); args.append(language)
        if q:
            where.append("instr(e.original_text,?)>0"); args.append(q)
        if status:
            where.append("s.status=?"); args.append(status)
        if pair:
            where.append("(s.observed_at_ms,s.id)<(?,?)"); args.extend(pair)
        sql = """SELECT e.id AS entry_id,e.language,e.original_text,s.id AS snapshot_id,
            s.status,s.enrichment_requested,s.observed_at_ms,
            (SELECT count(*) FROM analysis_snapshots a WHERE a.entry_id=e.id) AS version_count
            FROM history_entries e JOIN analysis_snapshots s ON s.id=(
                SELECT a.id FROM analysis_snapshots a WHERE a.entry_id=e.id
                ORDER BY a.observed_at_ms DESC,a.id DESC LIMIT 1)"""
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY s.observed_at_ms DESC,s.id DESC LIMIT ?"
        with self.connection() as db:
            return page_result(db.execute(sql, [*args, limit + 1]).fetchall(), limit)

    def entry(self, entry_id):
        with self.connection() as db:
            value = db.execute("SELECT * FROM history_entries WHERE id=?", (entry_id,)).fetchone()
            if value is None:
                raise HistoryError(404, "HISTORY_NOT_FOUND", "找不到這筆紀錄，可能已被刪除。")
            latest = db.execute("SELECT id AS snapshot_id,status,enrichment_requested,observed_at_ms FROM analysis_snapshots WHERE entry_id=? ORDER BY observed_at_ms DESC,id DESC LIMIT 1", (entry_id,)).fetchone()
            return {**dict(value), "latest_snapshot": dict(latest) if latest else None}

    def snapshots(self, entry_id, limit=50, cursor=None):
        pair = decode_cursor(cursor)
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM history_entries WHERE id=?", (entry_id,)).fetchone():
                raise HistoryError(404, "HISTORY_NOT_FOUND", "找不到這筆紀錄。")
            sql = "SELECT id AS snapshot_id,analysis_id,status,enrichment_requested,origin,observed_at_ms FROM analysis_snapshots WHERE entry_id=?"
            args = [entry_id]
            if pair:
                sql += " AND (observed_at_ms,id)<(?,?)"; args.extend(pair)
            sql += " ORDER BY observed_at_ms DESC,id DESC LIMIT ?"
            return page_result(db.execute(sql, [*args, limit + 1]).fetchall(), limit)

    def snapshot(self, entry_id, snapshot_id):
        with self.connection() as db:
            row = db.execute("""SELECT s.result_json,e.language,e.original_text FROM analysis_snapshots s
                JOIN history_entries e ON e.id=s.entry_id WHERE s.entry_id=? AND s.id=?""", (entry_id, snapshot_id)).fetchone()
            if row is None:
                raise HistoryError(404, "HISTORY_NOT_FOUND", "找不到這個分析版本。")
            try:
                analysis, _ = canonical_snapshot(json.loads(row["result_json"]))
                if analysis.input.language != row["language"] or analysis.input.text != row["original_text"]:
                    raise ValueError()
                return analysis
            except (ValueError, HistoryError):
                raise HistoryError(409, "HISTORY_SNAPSHOT_INVALID", "分析快照格式不相容或已損壞，未重新推論。") from None

    def translations(self, entry_id):
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM history_entries WHERE id=?", (entry_id,)).fetchone():
                raise HistoryError(404, "HISTORY_NOT_FOUND", "找不到這筆紀錄。")
            rows = db.execute("SELECT span_start,span_end,text FROM user_translations WHERE entry_id=? ORDER BY span_start", (entry_id,)).fetchall()
            return {"items": [dict(row) for row in rows]}

    def save_translation(self, entry_id, snapshot_id, sentence_id, text):
        if not isinstance(text, str) or len(text) > 4000 or "\0" in text:
            raise invalid("翻譯最多 4000 個字元。")
        with self.connection(write=True) as db:
            row = db.execute("""SELECT s.result_json,e.original_text,e.language FROM analysis_snapshots s
                JOIN history_entries e ON e.id=s.entry_id WHERE s.entry_id=? AND s.id=?""",
                (entry_id, snapshot_id)).fetchone()
            if row is None:
                raise HistoryError(404, "HISTORY_NOT_FOUND", "找不到這個分析版本。")
            try:
                analysis, _ = canonical_snapshot(json.loads(row["result_json"]))
                if analysis.input.text != row["original_text"] or analysis.input.language != row["language"]:
                    raise ValueError()
                sentence = next(s for s in analysis.sentences if s.id == sentence_id)
            except (ValueError, StopIteration, HistoryError):
                raise invalid("無法找到有效的句子，未保存翻譯。") from None
            start, end = sentence.span.start, sentence.span.end
            if not text.strip():
                db.execute("DELETE FROM user_translations WHERE entry_id=? AND span_start=? AND span_end=?",
                           (entry_id, start, end))
            else:
                db.execute("""INSERT INTO user_translations(entry_id,span_start,span_end,text) VALUES(?,?,?,?)
                    ON CONFLICT(entry_id,span_start,span_end) DO UPDATE SET text=excluded.text""",
                    (entry_id, start, end, text))
            return {"span_start": start, "span_end": end, "text": text if text.strip() else ""}

    def delete(self, entry_id=None):
        with self.connection(write=True) as db:
            where, args = (" WHERE id=?", (entry_id,)) if entry_id is not None else ("", ())
            count = db.execute("SELECT count(*) FROM history_entries" + where, args).fetchone()[0]
            if entry_id is not None and not count:
                raise HistoryError(404, "HISTORY_NOT_FOUND", "找不到這筆紀錄。")
            versions = db.execute("SELECT count(*) FROM analysis_snapshots" + (" WHERE entry_id=?" if entry_id is not None else ""), args).fetchone()[0]
            db.execute("DELETE FROM history_entries" + where, args)
            return {"deleted_entries": count, "deleted_snapshots": versions}

    def import_records(self, records):
        results = []
        for index, item in enumerate(records):
            try:
                if not isinstance(item, dict) or set(item) != {"id", "saved_at", "enrichment", "analysis"}:
                    raise invalid()
                if not isinstance(item["analysis"], dict) or item["id"] != item["analysis"].get("analysis_id"):
                    raise invalid()
                when = datetime.fromisoformat(item["saved_at"])
                if when.tzinfo is None:
                    raise invalid("舊紀錄缺少時區，未匯入。")
                saved = self.save(item["analysis"], item["enrichment"], origin="browser_import", observed_at_ms=int(when.timestamp() * 1000))
                self.snapshot(saved["entry_id"], saved["snapshot_id"])  # Readback before reporting success.
                results.append({"index": index, "status": "imported" if saved["created"] else "duplicate", **saved})
            except (ValueError, TypeError, OverflowError):
                results.append({"index": index, "status": "invalid", "message": "舊紀錄格式或時間無效，原資料已保留。"})
            except HistoryError as exc:
                results.append({"index": index, "status": "conflict" if exc.status == 409 else "failed" if exc.status == 503 else "invalid",
                                "message": exc.message, "code": exc.code})
        return {"results": results}


@lru_cache(maxsize=8)
def _store(path):
    return HistoryStore(path)


def history_store():
    return _store(database_path())
