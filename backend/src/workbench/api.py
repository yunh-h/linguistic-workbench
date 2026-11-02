"""HTTP contract for the v0.1 single-page workbench."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from workbench.pipeline import AdapterUnavailable, ParserInvalid, TooManySentences, capabilities
from workbench.runtime import AnalysisRuntime, Budgets, ParserTimeout
from workbench.llm_config import configured_llm
from workbench.diagnostics import diagnostic, failure_reason
from workbench.questions import Question, answer_question, check_snapshot
from workbench.schema import Analysis
from workbench.providers import BadResponse, ProviderUnavailable
from workbench.history import HistoryError, history_store
from workbench.history_api import router as history_router


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)
    language: Literal["fr", "ja", "en"]
    enrichment: Literal["on", "off"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = AnalysisRuntime()
    app.state.runtime = runtime
    app.state.question_lock = asyncio.Lock()
    try:
        try:
            await asyncio.to_thread(history_store().initialize)
        except HistoryError:
            pass  # History endpoints report failure; optional storage must not block parsing.
        await asyncio.to_thread(runtime.start)
        yield
    finally:
        await asyncio.to_thread(runtime.close)


app = FastAPI(title="Linguistic Workbench", version="0.1.0", lifespan=lifespan)
app.include_router(history_router)
_FRONTEND = Path(__file__).resolve().parents[3] / "frontend"


class WorkbenchHTTPError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


def _error(status: int, code: str, message: str, retryable: bool = False):
    raise WorkbenchHTTPError(status, code, message, retryable)


@app.exception_handler(WorkbenchHTTPError)
@app.exception_handler(HistoryError)
def workbench_error_handler(_, exc: WorkbenchHTTPError):
    return JSONResponse(status_code=exc.status, content={
        "request_id": str(uuid4()),
        "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable},
    })


@app.exception_handler(RequestValidationError)
def request_validation_handler(request, exc: RequestValidationError):
    errors = exc.errors()
    if request.url.path.startswith("/api/v0.1/history"):
        code, message = "HISTORY_INVALID", "紀錄 ID、分頁或篩選條件無效。"
    elif any(e.get("type") == "string_too_long" for e in errors):
        code, message = "INPUT_TOO_LONG", "輸入最多 500 個字元。"
    elif any("language" in e.get("loc", ()) for e in errors):
        code, message = "UNSUPPORTED_LANGUAGE", "目前不支援這個語言。"
    else:
        code, message = "INVALID_INPUT", "請確認文字、語言與分析選項。"
    return JSONResponse(status_code=422, content={
        "request_id": str(uuid4()), "error": {"code": code, "message": message, "retryable": False},
    })


@app.get("/")
def index():
    return FileResponse(_FRONTEND / "index.html")


@app.get("/app.js")
def app_js():
    return FileResponse(_FRONTEND / "app.js", media_type="text/javascript")


@app.get("/style.css")
def style_css():
    return FileResponse(_FRONTEND / "style.css", media_type="text/css")


@app.get("/history.js")
def history_js():
    return FileResponse(_FRONTEND / "history.js", media_type="text/javascript")


@app.get("/questions.js")
def questions_js():
    return FileResponse(_FRONTEND / "questions.js", media_type="text/javascript")


@app.get("/export.js")
def export_js():
    return FileResponse(_FRONTEND / "export.js", media_type="text/javascript")


@app.get("/api/v0.1/capabilities")
def list_capabilities(request: Request):
    runtime = request.app.state.runtime
    llm = configured_llm()
    runtime.refresh()
    languages = []
    for language in ("fr", "ja", "en"):
        is_ready = runtime.ready(language)
        entries = capabilities(language)
        if not is_ready:
            segmentation = next(entry for entry in entries if entry.name == "segmentation")
            segmentation.support = "unsupported"
            segmentation.reason = "模型尚未載入、載入失敗或正在恢復，請稍後重新檢查。"
        languages.append({"language": language, "ready": is_ready, "capabilities": entries})
    return {
        "schema_version": "0.1.0",
        "languages": languages,
        # Configuration only, not a network health check or model-quality claim.
        "enrichment_available": bool(llm and llm.available),
        "enrichment_provider": llm.provider if llm else None,
        "enrichment_model": llm.model if llm else None,
        "analysis_timeout_ms": int((getattr(runtime, "budgets", None) or Budgets.from_env()).total * 1000) + 5000,
        "question_available": bool(llm and llm.available),
        "question_timeout_ms": (llm.timeout_seconds if llm else 20) * 1000 + 5000,
    }


@app.post("/api/v0.1/analyses")
def create_analysis(request: AnalyzeRequest, http_request: Request):
    if not request.text.strip():
        _error(422, "INVALID_INPUT", "請輸入文字。")
    if request.language == "en" and request.enrichment == "on":
        _error(422, "UNSUPPORTED_ENRICHMENT", "英文擴充目前只有 parser 分析。")
    try:
        return http_request.app.state.runtime.analyze(request.text, request.language, request.enrichment)
    except AdapterUnavailable:
        _error(503, "ADAPTER_UNAVAILABLE", "這個語言的模型尚未準備好。", True)
    except ParserInvalid:
        _error(502, "PARSER_INVALID_OUTPUT", "無法取得有效的分析結果。", True)
    except ParserTimeout:
        _error(504, "PARSER_TIMEOUT", "基本分析逾時，已停止這次推論。請稍後重試或縮短輸入。", True)
    except TooManySentences:
        _error(422, "TOO_MANY_SENTENCES", "最多支援五句。")
    except Exception:
        _error(500, "INTERNAL_ERROR", "分析暫時無法完成，請稍後重試。", True)


async def bounded_json(request: Request) -> bytes:
    # Historical snapshots are client-supplied, not trusted server records.
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        _error(415, "INVALID_CONTENT_TYPE", "請使用 JSON 傳送資料。")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 262144:
            _error(413, "CONTEXT_TOO_LARGE", "分析或對話內容過大，請縮短後重試。")
    return bytes(body)


@app.post("/api/v0.1/analyses/restore")
async def restore_analysis(request: Request):
    body = await bounded_json(request)
    try:
        analysis = Analysis.model_validate_json(body)
        check_snapshot(analysis)
    except ValueError:
        _error(422, "INVALID_SNAPSHOT", "紀錄格式不相容或已損壞；未重新執行分析。")
    return analysis


@app.post("/api/v0.1/questions")
async def ask_question(request: Request):
    body = await bounded_json(request)
    try:
        question = Question.model_validate_json(body)
    except ValueError:
        _error(422, "INVALID_QUESTION", "請提供有效分析、1–1000 字的問題與最多三輪完整對話。")
    config = configured_llm()
    if not config or not config.available:
        _error(503, "MODEL_CONFIGURATION", diagnostic("configuration")["message"])
    lock = getattr(request.app.state, "question_lock", None)
    if lock is None:
        lock = request.app.state.question_lock = asyncio.Lock()
    if lock.locked():
        _error(429, "MODEL_BUSY", "已有追問正在處理，請稍後再試。", True)
    async with lock:
        try:
            return await asyncio.wait_for(answer_question(question, config), config.timeout_seconds)
        except (TimeoutError, httpx.HTTPError, BadResponse, ProviderUnavailable) as exc:
            reason = "timeout" if isinstance(exc, TimeoutError) else failure_reason(exc)
            info = diagnostic(reason)
            info["message"] = info["message"].replace("模型補充", "模型回答")
            return JSONResponse(status_code=504 if reason == "timeout" else 502, content={
                "request_id": str(uuid4()), "error": {"code": "QUESTION_" + reason.upper(), **info},
            })
        except Exception:
            _error(500, "QUESTION_FAILED", "追問暫時無法完成，原分析不受影響。", True)
