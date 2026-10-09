from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import sysconfig
import warnings
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_pythonpath_entries(repo_root: Path) -> list[Path]:
    entries = [
        repo_root / "src",
        repo_root.parent / "SAGE" / "src",
        repo_root.parent / "sageVDB",
    ]
    use_local_neuromem = os.environ.get(
        "DIGITAL_TWIN_USE_LOCAL_NEUROMEM_SOURCE", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if use_local_neuromem:
        entries.append(repo_root.parent / "neuromem")
    return entries


def _prepend_repo_paths(repo_root: Path) -> list[Path]:
    added: list[Path] = []
    for entry in reversed(_candidate_pythonpath_entries(repo_root)):
        if not entry.exists():
            continue
        entry_str = str(entry)
        if entry_str not in sys.path:
            sys.path.insert(0, entry_str)
            added.append(entry)
    return added


def _ensure_local_policy_preferred(repo_root: Path) -> None:
    local_policy_file = (
        repo_root.parent / "SAGE" / "src" / "sage" / "serving" / "integrations" / "policy.py"
    )
    if not local_policy_file.exists():
        return

    try:
        policy_module = importlib.import_module("sage.serving.integrations.policy")
    except Exception as exc:  # pragma: no cover - diagnostic path
        raise RuntimeError(
            "Failed to import 'sage.serving.integrations.policy'. Set "
            "PYTHONPATH to include ../SAGE/src."
        ) from exc

    policy_path = Path(getattr(policy_module, "__file__", "")).resolve()
    expected_root = (repo_root.parent / "SAGE" / "src").resolve()
    if expected_root not in policy_path.parents:
        raise RuntimeError(
            "Imported policy module from a non-local path: "
            f"{policy_path}. Expected local checkout under {expected_root}. "
            "Run commands with the project interpreter to avoid interpreter/path drift."
        )


def _auto_link_sagevdb_shared_libs(source_pkg: Path) -> bool:
    """Symlink compiled .so files from the PyPI install into the source tree.

    Replicates ``../sageVDB/scripts/link_shared_libs.sh`` in pure Python so
    the bootstrap can self-heal without any manual step.

    Returns True if at least one .so was linked successfully.
    """
    # Locate the installed sagevdb package (must be a *different* path).
    try:
        spec = importlib.util.find_spec("sagevdb")
    except Exception:
        return False
    if spec is None or spec.origin is None:
        return False
    installed_pkg = Path(spec.origin).parent
    if installed_pkg.resolve() == source_pkg.resolve():
        return False  # installed path IS the source — nothing to link

    ext_suffix = str(sysconfig.get_config_var("EXT_SUFFIX") or "")
    linked = 0
    for so_file in installed_pkg.glob("*.so"):
        if so_file.name.startswith("_sagevdb") and ext_suffix and not so_file.name.endswith(
            ext_suffix
        ):
            continue
        target = source_pkg / so_file.name
        if target.is_symlink() and target.resolve() == so_file.resolve():
            linked += 1  # already correct
        else:
            try:
                if target.exists() or target.is_symlink():
                    target.unlink()
                target.symlink_to(so_file)
                linked += 1
            except OSError:
                pass
    return linked > 0


def _validate_sagevdb_source(repo_root: Path) -> None:
    """Verify the source sageVDB checkout can export its compiled API.

    The source sageVDB directory is placed on PYTHONPATH so that local Python
    edits take effect without a reinstall.  However, the source tree does NOT
    ship the compiled C extension (.so files).  If those files are missing,
    ``sagevdb/__init__.py`` silently catches the ImportError and sets
    ``__all__ = []``, making DatabaseConfig / DistanceMetric / IndexType /
    create_database unavailable.

    **Auto-fix**: when the .so files are missing, this function automatically
    symlinks them from the PyPI install into the source tree (equivalent to
    ``bash ../sageVDB/scripts/link_shared_libs.sh``).
    """
    source_pkg = repo_root.parent / "sageVDB" / "sagevdb"
    if not source_pkg.is_dir():
        return  # source checkout absent — rely on the PyPI package

    # First attempt: try to import directly.
    mod = None
    try:
        mod = importlib.import_module("sagevdb")
    except Exception:
        pass

    if mod is not None and hasattr(mod, "DatabaseConfig"):
        return  # all good — source checkout is fully functional

    # Import failed or DatabaseConfig missing — try auto-linking .so files.
    # Invalidate the broken import first so we get a fresh load after linking.
    sys.modules.pop("sagevdb", None)
    if _auto_link_sagevdb_shared_libs(source_pkg):
        # Re-import after linking.
        try:
            mod = importlib.import_module("sagevdb")
            if hasattr(mod, "DatabaseConfig"):
                return  # auto-fix succeeded
        except Exception:
            pass

    sys.modules.pop("sagevdb", None)
    warnings.warn(
        "sageVDB source checkout at ../sageVDB is missing compiled C "
        "extension (.so) for this Python interpreter, so sagevdb-specific "
        "features will remain unavailable until a matching isage-vdb build is "
        "installed. Non-sagevdb code can continue to run. Use the project "
        "Python 3.12 environment or run: bash ../sageVDB/scripts/link_shared_libs.sh",
        RuntimeWarning,
        stacklevel=2,
    )


def _require_module(module_name: str, install_hint: str) -> None:
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - diagnostic path
        raise RuntimeError(
            f"Missing runtime dependency '{module_name}'. {install_hint}"
        ) from exc


def bootstrap_runtime_env(
    *,
    require_policy: bool = True,
    require_fastapi: bool = False,
) -> None:
    repo_root = _repo_root()

    # Avoid torch trying to auto-load optional device extensions (e.g. torch_npu).
    os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")

    _prepend_repo_paths(repo_root)
    _validate_sagevdb_source(repo_root)

    sage_source_present = (
        repo_root.parent / "SAGE" / "src" / "sage" / "serving" / "integrations" / "policy.py"
    ).exists()
    if sage_source_present:
        _ensure_local_policy_preferred(repo_root)

    _require_module(
        "pydantic_settings",
        "Run: python -m pip install -e .",
    )
    if require_fastapi:
        _require_module(
            "fastapi",
            "Run: python -m pip install -e .",
        )

    if require_policy and sage_source_present:
        _require_module(
            "sage.serving.integrations.policy",
            "Ensure local SAGE source is visible (../SAGE/src) on PYTHONPATH.",
        )
