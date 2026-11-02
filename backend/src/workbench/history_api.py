"""History HTTP boundary. No parser or model calls are made by these routes."""

import asyncio
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workbench.history import HistoryError, history_store, invalid
from workbench.schema import Analysis

router = APIRouter(prefix="/api/v0.1/history")
DBId = Annotated[int, Path(ge=1, le=2**63 - 1)]
PageLimit = Annotated[int, Query(ge=1, le=100)]


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis: Analysis
    enrichment_requested: Literal["on", "off"]


class TranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot_id: int = Field(ge=1, le=2**63 - 1)
    sentence_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=4000)


async def read_body(request, limit):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HistoryError(415, "HISTORY_CONTENT_TYPE", "請使用 JSON 傳送紀錄。")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HistoryError(413, "HISTORY_TOO_LARGE", "紀錄資料過大，請分批匯入。")
        body.extend(chunk)
    return bytes(body)


@router.post("")
async def save(request: Request):
    body = await read_body(request, 270336)
    try:
        value = SaveRequest.model_validate_json(body)
    except ValidationError:
        raise invalid() from None
    result = await asyncio.to_thread(history_store().save, value.analysis, value.enrichment_requested)
    return JSONResponse(status_code=201 if result["created"] else 200, content=result)


@router.get("")
async def entries(limit: PageLimit = 50, cursor: Annotated[str | None, Query(max_length=200)] = None,
                  language: Literal["fr", "ja", "en"] | None = None,
                  q: Annotated[str, Query(max_length=500)] = "", status: Literal["complete", "partial"] | None = None):
    return await asyncio.to_thread(history_store().list_entries, limit, cursor, language, q, status)


@router.post("/import")
async def import_history(request: Request):
    body = await read_body(request, 3 * 1024 * 1024)
    try:
        payload = json.loads(body)
        if (not isinstance(payload, dict) or set(payload) != {"records"} or
                not isinstance(payload["records"], list) or not 1 <= len(payload["records"]) <= 10):
            raise ValueError()
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise invalid("每批請提供 1–10 筆舊紀錄。") from None
    return await asyncio.to_thread(history_store().import_records, payload["records"])


@router.get("/{entry_id}")
async def entry(entry_id: DBId):
    return await asyncio.to_thread(history_store().entry, entry_id)


@router.get("/{entry_id}/snapshots")
async def snapshots(entry_id: DBId, limit: PageLimit = 50,
                    cursor: Annotated[str | None, Query(max_length=200)] = None):
    return await asyncio.to_thread(history_store().snapshots, entry_id, limit, cursor)


@router.get("/{entry_id}/translations")
async def translations(entry_id: DBId):
    return await asyncio.to_thread(history_store().translations, entry_id)


@router.put("/{entry_id}/translations")
async def save_translation(entry_id: DBId, request: Request):
    body = await read_body(request, 20000)
    try:
        value = TranslationRequest.model_validate_json(body)
    except ValidationError:
        raise invalid("翻譯內容或句子資訊無效。") from None
    return await asyncio.to_thread(history_store().save_translation, entry_id, value.snapshot_id,
                                   value.sentence_id, value.text)


@router.get("/{entry_id}/snapshots/{snapshot_id}")
async def snapshot(entry_id: DBId, snapshot_id: DBId):
    return await asyncio.to_thread(history_store().snapshot, entry_id, snapshot_id)


@router.delete("/{entry_id}")
async def delete_entry(entry_id: DBId):
    return await asyncio.to_thread(history_store().delete, entry_id)


@router.delete("")
async def clear():
    return await asyncio.to_thread(history_store().delete)
