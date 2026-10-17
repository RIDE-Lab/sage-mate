import importlib.util
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[1] / "tools/check_dependency_lock.py"
spec = importlib.util.spec_from_file_location("dependency_lock", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_repository_git_pins_match_lock():
    assert module.main() == 0


@pytest.mark.parametrize("source", [
    {"registry": "https://pypi.org/simple"},
    {"git": "https://example.test/other.git#" + "a" * 40},
    {"git": "https://example.test/repo.git#" + "b" * 40},
])
def test_rejects_registry_wrong_repository_and_stale_commit(source):
    project = {"project": {"dependencies": ["example @ git+https://example.test/repo.git@" + "a" * 40]}}
    assert module.check_pins(project, {"package": [{"name": "example", "source": source}]})


def test_accepts_exact_resolved_commit():
    commit = "a" * 40
    project = {"project": {"dependencies": [f"example @ git+https://example.test/repo.git@{commit}"]}}
    lock = {"package": [{"name": "example", "source": {"git": f"https://example.test/repo.git?rev={commit}#{commit}"}}]}
    assert module.check_pins(project, lock) == []
