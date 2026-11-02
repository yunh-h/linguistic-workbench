import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from test_history import analysis
from workbench.api import app
from workbench.history import HistoryError, HistoryStore
from workbench.history_backup import backup_database, check_database


def test_old_database_migrates_and_preserves_snapshots(tmp_path):
    path = tmp_path / "v1.sqlite3"
    sql = (Path(__file__).resolve().parents[1] / "src/workbench/migrations/001_history.sql").read_text()
    original = analysis()
    with sqlite3.connect(path) as db:
        db.executescript(sql)
        db.execute("INSERT INTO history_entries(language, original_text, created_at_ms) VALUES(?,?,?)",
                   ("fr", original.input.text, 1))
        db.execute("""INSERT INTO analysis_snapshots(entry_id,analysis_id,schema_version,status,
            enrichment_requested,origin,observed_at_ms,stored_at_ms,result_json)
            VALUES(1,?,'0.1.0','complete','off','app',1,1,?)""",
            (original.analysis_id, original.model_dump_json()))
    check_database(path)  # Existing backups remain readable before migration.
    store = HistoryStore(path)
    assert store.snapshot(1, 1).analysis_id == original.analysis_id
    assert store.translations(1)["items"] == []
    store.save_translation(1, 1, "s1", "我的翻譯")
    backup = tmp_path / "backup.sqlite3"
    backup_database(path, backup)
    assert HistoryStore(backup).translations(1)["items"][0]["text"] == "我的翻譯"
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_user_translation_persists_across_versions_and_deletes(tmp_path):
    store = HistoryStore(tmp_path / "h.sqlite3")
    first = store.save(analysis(), "off")
    second = store.save(analysis("second"), "off")
    entry = first["entry_id"]
    store.save_translation(entry, first["snapshot_id"], "s1", "原本的譯文")
    assert store.snapshot(entry, second["snapshot_id"]).annotations == []
    assert store.translations(entry)["items"] == [{"span_start": 0, "span_end": 4, "text": "原本的譯文"}]
    with pytest.raises(HistoryError):
        store.save_translation(entry, second["snapshot_id"], "missing", "錯誤")
    assert store.translations(entry)["items"][0]["text"] == "原本的譯文"
    store.save_translation(entry, second["snapshot_id"], "s1", "新譯文")
    assert store.translations(entry)["items"][0]["text"] == "新譯文"
    store.save_translation(entry, first["snapshot_id"], "s1", " ")
    assert store.translations(entry)["items"] == []
    store.save_translation(entry, first["snapshot_id"], "s1", "可刪除")
    store.delete(entry)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM user_translations").fetchone()[0] == 0


def test_translation_api_validates_target_and_text():
    client = TestClient(app)
    original = analysis().model_dump(mode="json")
    saved = client.post("/api/v0.1/history", json={"analysis": original, "enrichment_requested": "off"}).json()
    url = f"/api/v0.1/history/{saved['entry_id']}/translations"
    data = {"snapshot_id": saved["snapshot_id"], "sentence_id": "s1", "text": "我的譯文"}
    assert client.put(url, json=data).status_code == 200
    assert client.get(url).json()["items"][0]["text"] == "我的譯文"
    assert client.put(url, json={**data, "sentence_id": "missing"}).status_code == 422
    assert client.put(url, json={**data, "text": "x" * 4001}).status_code == 422
    assert client.put(url, json={**data, "snapshot_id": 999}).status_code == 404
    assert client.get(url).json()["items"][0]["text"] == "我的譯文"
    assert client.get(f"/api/v0.1/history/{saved['entry_id']}/snapshots/{saved['snapshot_id']}").json() == original
