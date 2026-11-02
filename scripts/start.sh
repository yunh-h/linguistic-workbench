#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ ! -x "$project_root/.venv/bin/python" ]]; then
  echo "找不到專案 .venv。請依 docs/local-startup.md 建立 Python 環境。" >&2
  exit 1
fi
exec "$project_root/.venv/bin/python" "$project_root/scripts/start.py" "$@"
