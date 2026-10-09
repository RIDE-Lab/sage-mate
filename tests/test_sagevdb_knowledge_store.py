from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin import knowledge_base as knowledge_base_module
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate

try:
    sagevdb_module = import_module("sagevdb")
except ImportError:
    pytest.skip("sagevdb is not installed in this environment", allow_module_level=True)

required_symbols = ("DatabaseConfig", "DistanceMetric", "IndexType", "SageVDB")
missing_symbols = [symbol for symbol in required_symbols if not hasattr(sagevdb_module, symbol)]
if missing_symbols:
    pytest.skip(
        f"sagevdb is installed but missing required API symbols: {', '.join(missing_symbols)}",
        allow_module_level=True,
    )


def test_sagevdb_backend_adds_and_searches_documents(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        knowledge_backend="sagevdb",
        sagevdb_embedding_backend="hash",
        sagevdb_dimension=128,
    )
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="Office hour preference",
            content="Students should send an agenda before office hours and include the current blocker.",
            tags=["meeting", "office-hour"],
            source_name="advisor-note",
        )
    )

    hits = store.search("What should I send before office hours?", top_k=1)

    assert hits
    assert hits[0].title == "Office hour preference"
    assert hits[0].score > 0.0


def test_sentence_transformer_backend_uses_real_embedding_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSentenceTransformerEmbedder:
        def __init__(self, settings: AppSettings, np_module) -> None:
            self._np = np_module
            self.dimension = 4

        def encode(self, text: str):
            vector = self._np.zeros(self.dimension, dtype=self._np.float32)
            normalized = text.lower()
            if "office" in normalized or "meeting" in normalized or "agenda" in normalized:
                vector[0] = 1.0
            if "gpu" in normalized or "cluster" in normalized:
                vector[1] = 1.0
            if "paper" in normalized or "reading" in normalized:
                vector[2] = 1.0
            if float(vector.sum()) == 0.0:
                vector[3] = 1.0
            return vector

    monkeypatch.setattr(
        knowledge_base_module,
        "SentenceTransformerTextEmbedder",
        FakeSentenceTransformerEmbedder,
    )

    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        knowledge_backend="sagevdb",
        sagevdb_embedding_backend="sentence-transformers",
        sagevdb_embedding_model="fake-model",
    )
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="Office hour preference",
            content="Students should send an agenda before office hours and include the current blocker.",
            tags=["meeting", "office-hour"],
            source_name="advisor-note",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="Cluster access policy",
            content="Students must complete the GPU safety checklist before requesting cluster access.",
            tags=["gpu", "cluster"],
            source_name="lab-policy",
        )
    )

    hits = store.search("What should I send before office hours?", top_k=1)

    assert hits
    assert hits[0].title == "Office hour preference"


def test_sagevdb_sage_anns_backend_uses_adapter_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sagevdb

    calls: list[dict[str, object]] = []

    class FakeANNSDatabase:
        def __init__(self) -> None:
            self._metadata: list[dict[str, str]] = []

        def build_index(self, vectors, metadata=None) -> None:
            self._metadata = list(metadata or [])

        def search(self, query, k=10, include_metadata=True):
            del query, k, include_metadata
            if not self._metadata:
                return []
            return [SimpleNamespace(id=0, score=0.0, metadata=self._metadata[0])]

    def fake_create_database(config, *, backend="cpp", algorithm=None, **kwargs):
        calls.append(
            {
                "dimension": config.dimension,
                "metric": config.metric,
                "backend": backend,
                "algorithm": algorithm,
                "kwargs": kwargs,
            }
        )
        return FakeANNSDatabase()

    monkeypatch.setattr(sagevdb, "create_database", fake_create_database)

    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        knowledge_backend="sagevdb",
        sagevdb_embedding_backend="hash",
        sagevdb_dimension=128,
        sagevdb_backend="sage-anns",
        sagevdb_anns_algorithm="faiss_hnsw",
    )
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="Office hour preference",
            content="Students should send an agenda before office hours and include the current blocker.",
            tags=["meeting", "office-hour"],
            source_name="advisor-note",
        )
    )

    hits = store.search("What should I send before office hours?", top_k=1)

    assert calls
    assert calls[-1]["backend"] == "sage-anns"
    assert calls[-1]["algorithm"] == "faiss_hnsw"
    assert calls[-1]["metric"] == sagevdb.DistanceMetric.INNER_PRODUCT
    assert hits
    assert hits[0].title == "Office hour preference"


def test_sagevdb_sage_anns_backend_local_integration(tmp_path: Path) -> None:
    if find_spec("sage_anns") is None:
        pytest.skip("sage_anns is not visible in this environment")

    import sage_anns

    algorithms = sage_anns.list_algorithms()
    assert "faiss_hnsw" in algorithms

    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        knowledge_backend="sagevdb",
        sagevdb_embedding_backend="hash",
        sagevdb_dimension=128,
        sagevdb_backend="sage-anns",
        sagevdb_anns_algorithm="faiss_hnsw",
    )
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="Office hour preference",
            content="Students should send an agenda before office hours and include the current blocker.",
            tags=["meeting", "office-hour"],
            source_name="advisor-note",
        )
    )

    hits = store.search("What should I send before office hours?", top_k=1)

    assert hits
    assert hits[0].title == "Office hour preference"
    assert hits[0].score > 0.0
    assert store.runtime_backend_name() == "sagevdb:SageANNSVectorStore"
