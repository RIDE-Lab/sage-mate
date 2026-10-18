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
@pytest.mark.parametrize("extras", ["", "[delivery-markdown]"])
def test_rejects_registry_wrong_repository_and_stale_commit(source, extras):
    project = {"project": {"dependencies": ["example" + extras + " @ git+https://example.test/repo.git@" + "a" * 40]}}
    assert module.check_pins(project, {"package": [{"name": "example", "source": source}]})


@pytest.mark.parametrize("extras", ["", "[delivery-markdown]", "[first, second]"])
def test_accepts_exact_resolved_commit(extras):
    commit = "a" * 40
    project = {"project": {"dependencies": [f"example{extras} @ git+https://example.test/repo.git@{commit}"]}}
    lock = {"package": [{"name": "example", "source": {"git": f"https://example.test/repo.git?rev={commit}#{commit}"}}]}
    assert module.check_pins(project, lock) == []


def test_extras_never_relax_the_full_commit_pin_requirement():
    project = {"project": {"dependencies": ["example[delivery-markdown] @ git+https://example.test/repo.git@abcdef0"]}}
    assert module.check_pins(project, {"package": []}) == ["Git dependencies must use full commit pins"]


def test_package_names_are_normalized_without_changing_repository_identity():
    commit = "a" * 40
    project = {"project": {"dependencies": [f"Example_Name[markdown] @ git+https://example.test/repo.git@{commit}"]}}
    lock = {"package": [{"name": "example-name", "source": {"git": f"https://example.test/repo.git#{commit}"}}]}
    assert module.check_pins(project, lock) == []
