"""Read-only LM Studio connectivity/model-ID check. Does not run or load a model."""

import sys

import httpx

from workbench.llm_config import ConfigurationError, LLMConfig


def main() -> int:
    try:
        config = LLMConfig.from_env()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if config.provider != "lmstudio":
        print("請先設定 WORKBENCH_LLM_PROVIDER=lmstudio。", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    try:
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            response = client.get(config.base_url + "/models", headers=headers)
            response.raise_for_status()
            body = response.json()
        entries = body.get("data") if isinstance(body, dict) else None
        if not isinstance(entries, list) or any(not isinstance(entry, dict) or
                                               not isinstance(entry.get("id"), str) for entry in entries):
            raise ValueError()
    except httpx.HTTPStatusError as exc:
        print(f"LM Studio 回應 HTTP {exc.response.status_code}；請檢查網址與本地 token。", file=sys.stderr)
        return 1
    except (httpx.HTTPError, ValueError):
        print("無法讀取 LM Studio 模型清單；請確認服務已啟動、/v1 網址及 WSL／主機連線。", file=sys.stderr)
        return 1
    ids = [entry["id"] for entry in entries]
    print("LM Studio 連線成功。可用模型 ID（不代表已載入或完成分析驗收）：")
    for model_id in ids:
        print(f"  {model_id}")
    if not ids:
        print("尚無模型；請先在 LM Studio 準備模型。")
    if config.model and config.model not in ids:
        print("目前 WORKBENCH_LLM_MODEL 不在清單中，請改用上列完整 ID。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
