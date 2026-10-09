from __future__ import annotations

from pathlib import Path

from sage_faculty_twin.runtime_env import _candidate_pythonpath_entries


def test_neuromem_sibling_source_is_opt_in(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "sage-mate"
    monkeypatch.delenv("DIGITAL_TWIN_USE_LOCAL_NEUROMEM_SOURCE", raising=False)

    entries = _candidate_pythonpath_entries(repo_root)

    assert repo_root.parent / "neuromem" not in entries


def test_neuromem_sibling_source_can_be_explicitly_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "sage-mate"
    monkeypatch.setenv("DIGITAL_TWIN_USE_LOCAL_NEUROMEM_SOURCE", "true")

    entries = _candidate_pythonpath_entries(repo_root)

    assert repo_root.parent / "neuromem" in entries
