"""Run after installing backend, or with PYTHONPATH=backend/src."""

import argparse
from pathlib import Path
import sqlite3

from workbench.history import database_path
from workbench.history_backup import backup_database, check_database


def main():
    parser = argparse.ArgumentParser(description="備份或檢查工作台 SQLite 歷史紀錄")
    parser.add_argument("--source", type=Path, default=database_path(), help="來源 DB；預設使用 WORKBENCH_DB_PATH")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path, help="新備份檔路徑；上層目錄須存在，不覆寫既有檔案")
    action.add_argument("--check", action="store_true", help="只檢查來源 DB 的版本、完整性與外鍵")
    args = parser.parse_args()
    try:
        if args.check:
            check_database(args.source)
            print("資料庫版本、完整性與外鍵檢查通過。")
        else:
            backup_database(args.source, args.output)
            print(f"備份完成：{args.output.resolve()}")
        return 0
    except FileExistsError:
        print("目的檔案已存在，未覆寫；請指定新的備份路徑。")
    except (OSError, sqlite3.Error, ValueError, TimeoutError):
        print("備份／檢查未完成；請確認路徑、權限、DB 版本與可用空間。來源資料未刪除。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
