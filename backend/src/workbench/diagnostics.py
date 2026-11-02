"""Allowlisted user diagnostics: never expose exception text or provider bodies."""

import httpx

from workbench.schema import Warning


MESSAGES = {
    "connection": ("無法連接模型服務。", "請確認 LM Studio Server 已啟動，且 backend 可連到設定的網址。", True),
    "timeout": ("模型補充超過時間限制。", "可縮短句子後重試；工作台已停止等待，但模型服務可能仍在生成。", True),
    "authentication": ("模型服務拒絕驗證。", "請檢查 backend 的 provider 專用 API key；不要將 key 貼到畫面或聊天。", False),
    "busy": ("模型服務忙碌或已達請求限制。", "請等待目前生成完成後重試。", True),
    "http": ("模型服務回傳錯誤。", "請在模型服務端檢查模型載入、context 長度與 API 路徑；這不等於模型沒有生成。", True),
    "truncated": ("模型輸出被截斷，本次未採用。", "輸出達到生成長度限制；可縮短輸入、檢查 thinking 與生成上限。", False),
    "refused": ("模型拒絕或停止回答，本次未採用。", "請調整問題；沒有將拒答內容當作分析結果。", False),
    "format": ("模型已回應，但輸出格式無法解析。", "需要完整且符合約定的 JSON；已拒絕空白、額外文字或缺漏欄位。", True),
    "candidate": ("模型已回應，但分析候選未通過檢查。", "候選含無效 ID、重複項目、缺漏或不允許的值；整批補充未採用。", True),
    "graph": ("模型已回應，但依存關係候選無效。", "候選與 parser 合併後形成循環或不合法的根節點；整批補充未採用。", True),
    "configuration": ("模型服務尚未設定或設定無效。", "請檢查 backend 的 provider、模型 ID、網址與期限設定。", False),
    "provider": ("模型服務未回傳可採用的完整回答。", "回應結構或結束狀態不符合約定；沒有採用不完整結果。", True),
    "worker": ("模型補充程序中斷。", "工作台會嘗試恢復分析程序，請稍後再試。", True),
    "skipped": ("模型補充尚未執行。", "前一個資源處理階段未完成，已保留可用的基本分析。", True),
}


def diagnostic(reason: str) -> dict:
    reason = reason if reason in MESSAGES else "provider"
    message, detail, retryable = MESSAGES[reason]
    return dict(reason=reason, message=message, detail=detail, retryable=retryable)


def failure_reason(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connection"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return "authentication" if status in (401, 403) else "busy" if status == 429 else "http"
    return getattr(exc, "reason", "provider")


def enrichment_warning(reason: str) -> Warning:
    info = diagnostic(reason)
    return Warning(code="ENRICHMENT_FAILED", message=info.pop("message") + "已保留 parser 分析。",
                   diagnostic=info, sentence_id=None, word_ids=[], source_ids=[])
