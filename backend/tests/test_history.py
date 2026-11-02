"""Disk-backed history regression tests; every database is a temporary fixture."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from test_contract import fixture_analysis
from workbench.api import app
from workbench.history import HistoryError, HistoryStore, database_path, history_store, now_ms
from workbench.history_backup import backup_database, check_database
from workbench.schema import Dependency, present


def analysis(id="first", language="fr", text="🍵 au", status="complete"):
    value = fixture_analysis(text, language)
    if text != "🍵 au":
        sentence = value.sentences[0]
        word = sentence.words[0]
        word.form = text
        word.span.end = len(text)
        word.dependency = present(Dependency(head_word_id=None, relation="root"), "parser-fr")
        sentence.words = [word]
        segment = sentence.segments[0]
        segment.surface = text
        segment.span.end = len(text)
        sentence.segments = [segment]
    value.analysis_id = id
    value.status = status
    return value


def test_disk_migration_versions_idempotency_and_reopen(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = HistoryStore(path)
    first = store.save(analysis(), "off", observed_at_ms=1000)
    latest = store.save(analysis("second", status="partial"), "on", observed_at_ms=2000)
    assert first["entry_id"] == latest["entry_id"]
    duplicate = store.save(analysis(), "off")
    assert duplicate == {**first, "created": False}
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("SELECT count(*) FROM history_entries").fetchone()[0] == 1
    reopened = HistoryStore(path)
    rows = reopened.list_entries()["items"]
    assert len(rows) == 1 and rows[0]["status"] == "partial" and rows[0]["version_count"] == 2
    assert reopened.snapshot(first["entry_id"], first["snapshot_id"]).status == "complete"
    assert reopened.snapshot(latest["entry_id"], latest["snapshot_id"]).status == "partial"


def test_exact_original_text_and_language_deduplication(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite3")
    texts = ["🍵 au", "🍵 Au", "🍵  au", "🍵 é au", "🍵 e\u0301 au"]
    for i, text in enumerate(texts):
        store.save(analysis(str(i), text=text), "off")
    store.save(analysis("ja", language="ja"), "off")
    assert len(store.list_entries()["items"]) == 6


def test_conflict_and_failed_insert_leave_original_and_no_empty_entries(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite3")
    first = store.save(analysis(), "off")
    with pytest.raises(HistoryError) as exc:
        store.save(analysis(status="partial"), "off")
    assert exc.value.status == 409
    with pytest.raises(HistoryError):
        store.save(analysis(), "on")
    # Force a write failure after inserting a new entry; the transaction must roll back.
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_snapshot BEFORE INSERT ON analysis_snapshots BEGIN SELECT RAISE(ABORT,'private failure'); END")
    with pytest.raises(HistoryError) as exc:
        store.save(analysis("new", text="🍵 autre au"), "off")
    assert "private" not in exc.value.message
    assert len(store.list_entries()["items"]) == 1
    assert store.snapshot(first["entry_id"], first["snapshot_id"]).status == "complete"


def test_concurrent_initialization_and_duplicate_saves(tmp_path):
    path = tmp_path / "h.sqlite3"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: HistoryStore(path).save(analysis(), "off"), range(8)))
    assert sum(r["created"] for r in results) == 1
    assert HistoryStore(path).list_entries()["items"][0]["version_count"] == 1


def test_pagination_search_and_language_filters(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite3")
    for i in range(7):
        store.save(analysis(str(i), text=f"🍵 {i}%_ au", language="ja" if i % 2 else "fr"), "off", observed_at_ms=1000)
    first = store.list_entries(limit=3)
    second = store.list_entries(limit=3, cursor=first["next_cursor"])
    third = store.list_entries(limit=3, cursor=second["next_cursor"])
    assert len({r["entry_id"] for p in (first, second, third) for r in p["items"]}) == 7
    assert third["next_cursor"] is None
    assert len(store.list_entries(q="3%_")["items"]) == 1
    assert len(store.list_entries(language="ja")["items"]) == 3
    assert not store.list_entries(q="' OR 1=1 --")["items"]
    with pytest.raises(HistoryError):
        store.list_entries(cursor="garbage")


def test_snapshot_pagination_and_entry_ownership(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite3")
    saved = [store.save(analysis(str(i)), "off", observed_at_ms=1000) for i in range(4)]
    other = store.save(analysis("other", text="🍵 other au"), "off")
    first = store.snapshots(saved[0]["entry_id"], limit=2)
    second = store.snapshots(saved[0]["entry_id"], limit=2, cursor=first["next_cursor"])
    assert len(first["items"]) == len(second["items"]) == 2
    assert second["next_cursor"] is None
    with pytest.raises(HistoryError) as exc:
        store.snapshot(other["entry_id"], saved[0]["snapshot_id"])
    assert exc.value.status == 404


def test_delete_cascades_but_preserves_other_entries(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite3")
    first = store.save(analysis(), "off")
    store.save(analysis("again"), "off")
    store.save(analysis("other", text="🍵 other au"), "off")
    assert store.delete(first["entry_id"]) == {"deleted_entries": 1, "deleted_snapshots": 2}
    assert len(store.list_entries()["items"]) == 1
    assert store.delete() == {"deleted_entries": 1, "deleted_snapshots": 1}
    with sqlite3.connect(store.path) as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT count(*) FROM analysis_snapshots").fetchone()[0] == 0
    after_clear = store.save(analysis("after-clear"), "off")
    assert after_clear["entry_id"] > first["entry_id"]
    assert after_clear["snapshot_id"] > first["snapshot_id"]
    with pytest.raises(HistoryError) as exc:
        store.snapshot(first["entry_id"], first["snapshot_id"])
    assert exc.value.status == 404


@pytest.mark.parametrize("kind", ["foreign", "newer", "occupied", "blocked"])
def test_incompatible_or_unavailable_database_is_not_overwritten(tmp_path, kind):
    path = tmp_path / "h.sqlite3"
    if kind == "foreign":
        path.write_bytes(b"not a database; preserve me")
    elif kind == "blocked":
        path.mkdir()
    else:
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE unrelated(value TEXT)")
            db.execute("INSERT INTO unrelated VALUES('preserve me')")
            if kind == "newer":
                db.execute("PRAGMA user_version=99")
    original = path.read_bytes() if path.is_file() else None
    with pytest.raises(HistoryError):
        HistoryStore(path).initialize()
    if original is not None:
        assert path.read_bytes() == original


def legacy(value, saved_at="2026-01-01T00:00:00+00:00"):
    return {"id": value.analysis_id, "saved_at": saved_at, "enrichment": "off", "analysis": value.model_dump(mode="json")}


def test_import_partial_failure_repeated_batches_and_old_order(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite3")
    newest = store.save(analysis("newest"), "off")
    records = [legacy(analysis("old")), legacy(analysis("bad"), "bad time"), legacy(analysis("newest", status="partial"))]
    first = store.import_records(records)["results"]
    assert [r["status"] for r in first] == ["imported", "invalid", "conflict"]
    assert store.import_records(records)["results"][0]["status"] == "duplicate"
    rows = store.list_entries()["items"]
    assert rows[0]["snapshot_id"] == newest["snapshot_id"] and rows[0]["version_count"] == 2


@pytest.mark.parametrize("saved_at", ["2026-01-01", "9999-01-01T00:00:00Z", "1960-01-01T00:00:00Z", None])
def test_invalid_import_timestamp_leaves_no_entry(tmp_path, saved_at):
    store = HistoryStore(tmp_path / "h.sqlite3")
    assert store.import_records([legacy(analysis(), saved_at)])["results"][0]["status"] == "invalid"
    assert not store.list_entries()["items"]


def test_backup_includes_wal_and_never_overwrites(tmp_path):
    store = HistoryStore(tmp_path / "source.sqlite3")
    store.initialize()
    with sqlite3.connect(store.path) as keeper:
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        saved = store.save(analysis(), "off")
        destination = tmp_path / "backup.sqlite3"
        backup_database(store.path, destination)
        check_database(destination)
        assert HistoryStore(destination).snapshot(saved["entry_id"], saved["snapshot_id"]).analysis_id == "first"
        original = destination.read_bytes()
        with pytest.raises(FileExistsError):
            backup_database(store.path, destination)
        assert destination.read_bytes() == original
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.Error):
        backup_database(missing, tmp_path / "unused.sqlite3")
    assert not missing.exists() and not (tmp_path / "unused.sqlite3").exists()


def test_database_path_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKBENCH_DB_PATH")
    expected = database_path()
    monkeypatch.chdir(tmp_path)
    assert database_path() == expected and expected.name == "workbench.sqlite3"


def test_history_api_save_read_import_delete_without_llm(monkeypatch):
    client = TestClient(app)
    value = analysis().model_dump(mode="json")
    response = client.post("/api/v0.1/history", json={"analysis": value, "enrichment_requested": "off"})
    assert response.status_code == 201
    saved = response.json()
    assert client.post("/api/v0.1/history", json={"analysis": value, "enrichment_requested": "off"}).status_code == 200
    entry_id, sid = saved["entry_id"], saved["snapshot_id"]
    assert client.get(f"/api/v0.1/history/{entry_id}/snapshots/{sid}").json() == value
    assert client.get(f"/api/v0.1/history/{entry_id}").json()["latest_snapshot"]["snapshot_id"] == sid
    assert len(client.get(f"/api/v0.1/history/{entry_id}/snapshots").json()["items"]) == 1
    assert client.post("/api/v0.1/history/import", json={"records": [legacy(analysis())]}).json()["results"][0]["status"] == "duplicate"
    assert client.delete(f"/api/v0.1/history/{entry_id}").json()["deleted_snapshots"] == 1
    assert client.get(f"/api/v0.1/history/{entry_id}/snapshots/{sid}").status_code == 404


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "cursor=bad", "language=xx", "status=no", "q=" + "x" * 501])
def test_history_api_invalid_query(query):
    response = TestClient(app).get("/api/v0.1/history?" + query)
    assert response.status_code == 422


def test_history_api_rejects_invalid_snapshot_and_body():
    client = TestClient(app)
    value = analysis().model_dump(mode="json")
    value["sentences"][0]["words"][0]["dependency"]["value"]["head_word_id"] = "absent"
    assert client.post("/api/v0.1/history", json={"analysis": value, "enrichment_requested": "off"}).status_code == 422
    assert client.post("/api/v0.1/history", content="{}").status_code == 415
    assert client.post("/api/v0.1/history", content=b"x" * 270337, headers={"content-type": "application/json"}).status_code == 413
    assert client.post("/api/v0.1/history/import", json={"records": [{}] * 11}).status_code == 422
    assert not client.get("/api/v0.1/history").json()["items"]


def test_database_failure_is_safe_and_restore_still_works(monkeypatch, tmp_path):
    path = tmp_path / "private-broken.sqlite3"
    path.mkdir()
    monkeypatch.setenv("WORKBENCH_DB_PATH", str(path))
    client = TestClient(app)
    response = client.get("/api/v0.1/history")
    assert response.status_code == 503 and str(path) not in response.text
    assert client.post("/api/v0.1/analyses/restore", json=analysis().model_dump(mode="json")).status_code == 200


def test_real_write_lock_times_out_without_partial_save_and_retry_recovers():
    store = history_store()
    original = store.save(analysis(), "off")
    client = TestClient(app)
    candidate = analysis("after-lock", text="autre")
    payload = {"analysis": candidate.model_dump(mode="json"), "enrichment_requested": "off"}
    blocker = sqlite3.connect(store.path, isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        # Exercise the real SQLite busy timeout, not a mocked 503.
        response = client.post("/api/v0.1/history", json=payload)
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "HISTORY_UNAVAILABLE" and error["retryable"] is True
        assert str(store.path) not in response.text and "database is locked" not in response.text
        # WAL readers and the current analysis remain usable while writing is blocked.
        assert len(client.get("/api/v0.1/history").json()["items"]) == 1
        assert store.snapshot(original["entry_id"], original["snapshot_id"]).analysis_id == "first"
        assert client.post("/api/v0.1/analyses/restore", json=payload["analysis"]).json() == payload["analysis"]
    finally:
        blocker.rollback()
        blocker.close()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM history_entries").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM analysis_snapshots").fetchone()[0] == 1
    assert client.post("/api/v0.1/history", json=payload).status_code == 201
    assert client.post("/api/v0.1/history", json=payload).status_code == 200
    assert len(store.list_entries()["items"]) == 2
