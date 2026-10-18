import json
from pathlib import Path
import runpy
import sqlite3

import pytest

export = runpy.run_path(str(Path(__file__).parents[1] / "tools/quarantine_legacy_memory.py"))["export_quarantine"]


def database(path, owned=False):
    rows = {
        "test": {"text": "full test text", "metadata": {"entry_kind": "conversation_record", "memory_id": "t",
            "student_name": "smoke-check", "record_payload": {"question": "full question", "owner_id": "guest:a" if owned else None}}},
        "attachment": {"text": "full attachment", "metadata": {"entry_kind": "attachment_excerpt", "memory_id": "t"}},
        "unknown": {"text": "real content mentions test", "metadata": {"entry_kind": "conversation_record", "memory_id": "u"}},
    }
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE collection_snapshots(name TEXT,raw_data TEXT,config TEXT,index_metadata TEXT,updated_at TEXT)")
        connection.execute("INSERT INTO collection_snapshots VALUES(?,?,?,?,?)",
                           ("conversation-memory", json.dumps(rows), "{}", "{}", "now"))
    return rows


def test_export_is_lossless_private_and_source_unchanged(tmp_path):
    source = tmp_path / "source.db"
    rows = database(source)
    before = source.read_bytes()
    destination = tmp_path / "quarantine"
    result = export(source, destination)
    assert result["counts"]["conversation-memory"] == {"test-labelled": 2, "unknown-origin": 1}
    recovered = {}
    for group in ("test-labelled", "unknown-origin"):
        recovered.update(json.loads((destination / (group + ".json")).read_text())["conversation-memory"])
    assert recovered == rows and source.read_bytes() == before
    assert destination.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in destination.iterdir())
    with pytest.raises(ValueError, match="must_be_new"):
        export(source, destination)


def test_owned_records_fail_before_creating_archive(tmp_path):
    source = tmp_path / "source.db"
    database(source, owned=True)
    with pytest.raises(ValueError, match="separate_review"):
        export(source, tmp_path / "quarantine")
    assert not (tmp_path / "quarantine").exists()


def test_wal_snapshot_and_profile_partition(tmp_path):
    source = tmp_path / "source.db"
    database(source)
    profiles = {
        "test-profile": {"metadata": {"profile_payload": {"student_key": "fixture-user"}}},
        "unknown-profile": {"metadata": {"profile_payload": {"summary": "smoke test"}}},
    }
    with sqlite3.connect(source) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO collection_snapshots VALUES(?,?,?,?,?)",
                       ("conversation-profile-memory", json.dumps(profiles), "{}", "{}", "now"))
        writer.commit()
        result = export(source, tmp_path / "archive")
        assert result["counts"]["conversation-profile-memory"] == {
            "test-labelled": 1, "unknown-origin": 1,
        }
        with sqlite3.connect(tmp_path / "archive/original.sqlite3") as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert restored.execute("SELECT COUNT(*) FROM collection_snapshots").fetchone()[0] == 2
