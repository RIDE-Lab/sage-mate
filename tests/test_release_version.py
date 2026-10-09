"""Prevent application/package/lock/release-note version drift."""

import json
import tomllib
from datetime import date
from pathlib import Path

from sage_faculty_twin import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_release_versions_are_consistent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    application = [p for p in lock["package"] if p["name"] == project["project"]["name"]]
    assert len(application) == 1
    assert project["project"]["version"] == application[0]["version"] == __version__
    entries = json.loads((ROOT / "release/runtime-seed/data/changelog.json").read_text())
    assert entries[0]["version"] == f"v{__version__}"
    assert len({entry["version"] for entry in entries}) == len(entries)
    date.fromisoformat(entries[0]["date"])
    assert entries[0]["summary"] and entries[0]["highlights"]
