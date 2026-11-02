"""Consistent online backup; never overwrite a destination or create a missing source."""

import os
from pathlib import Path
import sqlite3
import time


def check_database(path):
    with_read = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        if with_read.execute("PRAGMA user_version").fetchone()[0] not in (1, 2):
            raise ValueError("不支援的資料庫版本。")
        if with_read.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("資料庫完整性檢查失敗。")
        if with_read.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("資料庫外鍵檢查失敗。")
        with_read.execute("SELECT e.original_text,s.result_json FROM history_entries e LEFT JOIN analysis_snapshots s ON s.entry_id=e.id LIMIT 0")
    finally:
        with_read.close()


def backup_database(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    check_database(source)
    # Reserve the destination exclusively before SQLite opens it.
    handle = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(handle)
    reader = writer = None
    try:
        reader = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
        writer = sqlite3.connect(destination, timeout=5)
        deadline = time.monotonic() + 30
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError("備份逾時，請稍後重試。")
        reader.backup(writer, pages=128, progress=progress, sleep=.05)
        writer.close(); writer = None
        reader.close(); reader = None
        check_database(destination)
    except BaseException:
        if writer is not None:
            writer.close()
        if reader is not None:
            reader.close()
        destination.unlink()  # Only the file exclusively created by this invocation.
        raise
