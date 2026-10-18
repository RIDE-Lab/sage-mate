"""Read-only legacy export; never assigns owners or modifies the live store.

Test labels are evidence, not proof of AI authorship. Both output groups are
private quarantine archives and must stay outside the serving runtime.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


MARKERS = re.compile(r"pytest|fixture|smoke|regression|e2e|benchmark|验收|测试|回归|冒烟|探针|audit|probe|test", re.I)
COLLECTIONS = {"conversation-memory", "conversation-profile-memory"}


def test_hint(metadata):
    payload = metadata.get("record_payload") or metadata.get("profile_payload") or {}
    identity = " ".join(str(payload.get(key, metadata.get(key, "")) or "") for key in
                        ("student_name", "student_email", "conversation_id", "student_key", "session_id"))
    return bool(MARKERS.search(identity) or re.search(
        r"@(example\.(com|org|net)|test\.(com|invalid)|invalid)(?:\b|$)", identity, re.I))


def export_quarantine(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError("quarantine_destination_must_be_new")
    # Backup API produces a consistent snapshot even if the source uses WAL.
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as live:
        snapshot = sqlite3.connect(":memory:")
        live.backup(snapshot)
    try:
        rows = snapshot.execute("SELECT name,raw_data,config,index_metadata,updated_at FROM collection_snapshots").fetchall()
        if not rows or any(row[0] not in COLLECTIONS for row in rows):
            raise ValueError("unexpected_legacy_collections")
        groups = {"test-labelled": {}, "unknown-origin": {}}
        counts = {}
        for name, raw, config, index_metadata, updated_at in rows:
            entries = json.loads(raw)
            buckets = {}
            for key, entry in entries.items():
                metadata = entry.get("metadata", {})
                payload = metadata.get("record_payload") or metadata.get("profile_payload") or {}
                if metadata.get("owner_id") or payload.get("owner_id"):
                    raise ValueError("owned_record_requires_separate_review")
                if metadata.get("entry_kind") != "attachment_excerpt":
                    buckets[metadata.get("memory_id", key)] = "test-labelled" if test_hint(metadata) else "unknown-origin"
            for group in groups:
                groups[group][name] = {}
            for key, entry in entries.items():
                metadata = entry.get("metadata", {})
                group = buckets.get(metadata.get("memory_id", key), "unknown-origin")
                groups[group][name][key] = entry
            counts[name] = {group: len(groups[group][name]) for group in groups}
            assert sum(counts[name].values()) == len(entries)
        destination.mkdir(mode=0o700)
        backup_path = destination / "original.sqlite3"
        fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(backup_path) as backup:
            snapshot.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("quarantine_snapshot_integrity_failure")
        hashes = {"original.sqlite3": hashlib.sha256(backup_path.read_bytes()).hexdigest()}
        for group, collections in groups.items():
            data = json.dumps(collections, ensure_ascii=False, sort_keys=True).encode()
            filename = group + ".json"
            with (destination / filename).open("xb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(data)
            hashes[filename] = hashlib.sha256(data).hexdigest()
        manifest = {"version": 1, "policy": "unowned-private-quarantine-no-auto-claim",
                    "classification": "identity-test-hints-v1-not-authorship-proof",
                    "counts": counts, "sha256": hashes}
        with (destination / "manifest.json").open("x") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(manifest, output, sort_keys=True, indent=2)
        return manifest
    finally:
        snapshot.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    print(json.dumps(export_quarantine(args.source, args.destination), sort_keys=True))
