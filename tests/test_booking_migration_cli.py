import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def tool():
    path = Path(__file__).resolve().parents[1] / "tools/migrate_booking_notifications.py"
    spec = importlib.util.spec_from_file_location("booking_migration_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("active,returncode", [("active", 0), ("activating", 0), ("", 1)])
def test_apply_refuses_active_or_unverifiable_service_before_opening_store(tmp_path, monkeypatch, active, returncode):
    module = tool()
    path = tmp_path / "existing.db"
    path.touch()
    monkeypatch.setattr("sys.argv", ["migrate", str(path), "--apply"])
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=active, returncode=returncode))
    monkeypatch.setattr(module, "StatefulProcessRuntime", lambda **kw: pytest.fail("must not open state"))
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


def test_missing_store_is_not_created(tmp_path, monkeypatch):
    module = tool()
    path = tmp_path / "missing.db"
    monkeypatch.setattr("sys.argv", ["migrate", str(path)])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert not path.exists()
