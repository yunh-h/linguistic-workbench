"""Stable local launcher. No downloads, model requests or database writes."""

import argparse
import importlib.util
import os
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "WORKBENCH_DB_PATH": "data/workbench.sqlite3",
    "WORKBENCH_STANZA_DIR": "data/models/stanza",
    "OMP_NUM_THREADS": "2",
    "MKL_NUM_THREADS": "2",
    "WORKBENCH_LLM_PROVIDER": "lmstudio",
    "WORKBENCH_LLM_BASE_URL": "http://127.0.0.1:1234/v1",
    "WORKBENCH_LLM_MODEL": "",
}
ALLOWED = set(DEFAULTS) | {"WORKBENCH_LLM_TIMEOUT_SECONDS", "WORKBENCH_LLM_MAX_TOKENS",
                           "WORKBENCH_STANZA_PACKAGE", "WORKBENCH_FRENCH_PARSER"}


def load_config(path):
    if not path.exists():
        return {}
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, ValueError):
        # Do not echo a malformed line which might contain a credential.
        raise ValueError("無法讀取 config.local.toml，請檢查檔案權限及 TOML 格式。") from None


def settings(root, config, inherited, parser_only=False, port=None):
    if not isinstance(config, dict) or set(config) - {"server", "environment"}:
        raise ValueError("設定只接受 [server] 與 [environment]。")
    server, configured = config.get("server", {}), config.get("environment", {})
    if (not isinstance(server, dict) or set(server) - {"host", "port"} or
            not isinstance(configured, dict) or set(configured) - ALLOWED or
            any(not isinstance(v, str) for v in configured.values())):
        raise ValueError("設定包含未知欄位或型別錯誤；environment 的值必須是字串，憑證請用環境變數。")
    host = server.get("host", "127.0.0.1")
    port = server.get("port", 8000) if port is None else port
    if not isinstance(host, str) or not host.strip() or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("server 需要有效的 host 與 1–65535 整數 port。")
    env = {**inherited}
    for name, value in {**DEFAULTS, **configured}.items():
        env.setdefault(name, value)
    for name in ("WORKBENCH_DB_PATH", "WORKBENCH_STANZA_DIR"):
        if not env[name].strip():
            raise ValueError("資料庫與模型路徑不可空白。")
        path = Path(env[name]).expanduser()
        env[name] = str((path if path.is_absolute() else root / path).resolve())
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        if not env[name].isdigit() or not 1 <= int(env[name]) <= 256:
            raise ValueError("執行緒數量必須是 1–256。")
    if parser_only:
        env["WORKBENCH_LLM_MODEL"] = ""
    # Never inherit the old temporary source tree or external Python user packages.
    env["PYTHONPATH"] = str(root / "backend/src")
    env["PYTHONNOUSERSITE"] = "1"
    return env, host, port


def check_installation():
    missing = [name for name in ("fastapi", "uvicorn", "httpx", "stanza", "torch", "spacy", "ginza", "ja_ginza")
               if importlib.util.find_spec(name) is None]
    if missing:
        raise ValueError("缺少 Python 套件：" + ", ".join(missing) + "。請見 docs/local-startup.md。")
    from workbench.adapters.french import model_files_available
    if os.getenv("WORKBENCH_FRENCH_PARSER", "stanza") == "stanza" and not model_files_available():
        raise ValueError("法文模型不完整。請依 docs/local-startup.md 下載模型；啟動腳本不會自動下載。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="啟動本機工作台；Ctrl+C 停止。")
    parser.add_argument("--check", action="store_true", help="只檢查設定、套件和模型檔，不啟動服務或呼叫 LLM")
    parser.add_argument("--parser-only", action="store_true", help="本次停用 LLM（不修改設定檔）")
    parser.add_argument("--port", type=int, help="只覆寫本次的 HTTP port")
    args = parser.parse_args(argv)
    try:
        env, host, port = settings(ROOT, load_config(ROOT / "config.local.toml"), os.environ,
                                   args.parser_only, args.port)
        os.environ.clear()
        os.environ.update(env)
        sys.path.insert(0, env["PYTHONPATH"])
        check_installation()
        from workbench.llm_config import configured_llm
        llm = configured_llm()
    except (OSError, ValueError) as exc:
        print("啟動檢查失敗：" + str(exc), file=sys.stderr)
        return 1
    print("Python：" + sys.executable, flush=True)
    print("SQLite：" + env["WORKBENCH_DB_PATH"], flush=True)
    print("Parser 模型：" + env["WORKBENCH_STANZA_DIR"], flush=True)
    llm_status = ("設定無效；仍可使用 parser" if llm is None else
                  "已設定；尚未檢查連線" if llm.available else "停用")
    print("LLM：" + llm_status, flush=True)
    if args.check:
        print("本機啟動檢查通過（未載入模型、未連線、未檢查 DB 完整性）。")
        return 0
    os.chdir(ROOT)
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "workbench.api:app",
                             "--host", host, "--port", str(port)])


if __name__ == "__main__":
    sys.exit(main())
