"""Test-level runtime bootstrap.

Ensures PYTHONPATH includes local source checkouts (SAGE, sageVDB, neuromem)
before test modules are collected. Without this, importing
``sage_faculty_twin.llm_client`` or ``sage_faculty_twin.api`` at collection
time triggers ``bootstrap_runtime_env()`` which may fail if the SAGE source
is not yet on ``sys.path``.

This conftest is intentionally lightweight: it delegates to the same
``bootstrap_runtime_env`` used at runtime, but with ``require_policy=False``
so collection succeeds even when the full SAGE stack is not available.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Prevent any test from triggering network downloads of models or datasets.
# If a model is not already cached, SentenceTransformer / huggingface_hub
# will raise an error instead of silently downloading.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# Ensure the project src is importable before any test module is collected.
_repo_root = Path(__file__).resolve().parent.parent
_src = str(_repo_root / "src")
# A few repository-level verification tests import helpers from ``tools``.
# Keep the test runner hermetic by exposing the checkout root explicitly
# instead of requiring callers to set PYTHONPATH themselves.
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
if _src not in sys.path:
    sys.path.insert(0, _src)

# Prepend sibling source checkouts so local edits are visible.
for sibling in ("SAGE/src", "sageVDB", "neuromem"):
    entry = str(_repo_root.parent / sibling)
    if Path(entry).exists() and entry not in sys.path:
        sys.path.insert(0, entry)

# Bootstrap the runtime environment without hard-requiring the SAGE policy
# module.  Test modules that import sage_faculty_twin.llm_client will trigger
# a second bootstrap_runtime_env(require_policy=True) call, but the paths are
# already set up so it will succeed when the SAGE source checkout is present.
from sage_faculty_twin.runtime_env import bootstrap_runtime_env  # noqa: E402

bootstrap_runtime_env(require_policy=False, require_fastapi=False)

# ── Optional-dependency skip markers ──────────────────────────────────────────
# Some tests require sentence-transformers (for faiss / dense retrieval).
# When the package is not importable (e.g. CANN / torch_npu missing),
# auto-skip those tests instead of crashing.
import pytest  # noqa: E402

try:
    import sentence_transformers as _st  # noqa: F401
    _HAS_SENTENCE_TRANSFORMERS = True
except Exception:
    _HAS_SENTENCE_TRANSFORMERS = False


def _embedding_model_is_cached(model_name: str = "BAAI/bge-small-zh-v1.5") -> bool:
    """Check if a sentence-transformers model is available locally.

    Returns True only when the model weights already exist in the
    HuggingFace or sentence-transformers cache — never triggers a download.
    """
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
        for repo in info.repos:
            if repo.repo_id == model_name:
                return True
    except Exception:
        pass

    # Fallback: check sentence-transformers cache directory directly.
    cache_dir = Path.home() / ".cache" / "torch" / "sentence_transformers" / model_name
    if cache_dir.is_dir():
        return True

    # Also check huggingface hub cache.
    hf_cache = Path(
        os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface" / "hub")
    )
    model_slug = model_name.replace("/", "--")
    if (hf_cache / f"models--{model_slug}").is_dir():
        return True

    return False


# Tests must never trigger network downloads.  Skip neuromem-related tests
# when the embedding model is not already cached locally.
_HAS_LOCAL_EMBEDDING_MODEL = (
    _HAS_SENTENCE_TRANSFORMERS and _embedding_model_is_cached()
)

_MODULES_REQUIRING_SENTENCE_TRANSFORMERS = frozenset({
    "test_knowledge_import",
})


def available_knowledge_backends() -> tuple[str, ...]:
    """Return backends safe to use in tests (never triggers downloads)."""
    backends = ["local"]
    if _HAS_LOCAL_EMBEDDING_MODEL:
        backends.append("neuromem")
    try:
        import sagevdb  # noqa: F401
        if hasattr(sagevdb, "DatabaseConfig"):
            backends.append("sagevdb")
    except Exception:
        pass
    return tuple(backends)


# Expose for test modules to use as a skip decorator.
requires_neuromem_model = pytest.mark.skipif(
    not _HAS_LOCAL_EMBEDDING_MODEL,
    reason=(
        "embedding model BAAI/bge-small-zh-v1.5 not cached locally — "
        "tests must not download models from the network. "
        "Pre-cache with: python -c \"from sentence_transformers import "
        "SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')\""
    ),
)


@pytest.fixture(autouse=True)
def _isolate_conversation_memory(tmp_path, monkeypatch):
    """Redirect conversation_memory_dir to a temp path so tests never
    pollute the live production SQLite memory store."""
    isolated = tmp_path / "conversation_memory"
    monkeypatch.setenv("DIGITAL_TWIN_CONVERSATION_MEMORY_DIR", str(isolated))


def pytest_collection_modifyitems(config, items):
    if _HAS_SENTENCE_TRANSFORMERS and _HAS_LOCAL_EMBEDDING_MODEL:
        return  # all good — run everything

    if not _HAS_SENTENCE_TRANSFORMERS:
        skip_reason = (
            "sentence-transformers not importable "
            "(torch_npu / CANN libhccl.so missing)"
        )
    else:
        skip_reason = (
            "embedding model BAAI/bge-small-zh-v1.5 not cached locally — "
            "tests must not download models from the network. "
            "Pre-cache with: python -c \"from sentence_transformers import "
            "SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')\""
        )

    for item in items:
        mod = item.module.__name__.rsplit(".", 1)[-1]
        if mod in _MODULES_REQUIRING_SENTENCE_TRANSFORMERS:
            item.add_marker(pytest.mark.skip(reason=skip_reason))
