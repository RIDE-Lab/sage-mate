from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

import numpy as np
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


def test_knowledge_sagevdb_backend_can_differ_from_conversation_backend() -> None:
    settings = AppSettings(
        knowledge_sagevdb_backend="cpp",
        sagevdb_backend="sage-anns",
    )
    store = LocalKnowledgeStore.__new__(LocalKnowledgeStore)
    store._settings = settings

    assert store._normalize_sagevdb_backend() == "cpp"
    assert settings.sagevdb_backend == "sage-anns"


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


def test_sagevdb_restart_reuses_durable_index_without_reembedding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_dir = tmp_path / "knowledge"
    persistence_dir = tmp_path / "sagevdb-index"
    settings = AppSettings(
        knowledge_base_dir=knowledge_dir,
        knowledge_backend="sagevdb",
        sagevdb_embedding_backend="hash",
        sagevdb_dimension=128,
        knowledge_sagevdb_persistence_dir=persistence_dir,
    )
    first = LocalKnowledgeStore(settings)
    first.add_document(
        KnowledgeDocumentCreate(
            title="Persistent office hours",
            content="Students should send an agenda before office hours.",
            tags=["meeting"],
            source_name="persistent-note",
        )
    )
    first_generation = first._sagevdb_persistence.generation
    assert first._sagevdb_last_sync.embedded == 1

    def fail_if_embedded(self, texts):
        raise AssertionError(f"restart unexpectedly embedded {len(texts)} documents")

    monkeypatch.setattr(LocalKnowledgeStore, "_embed_documents", fail_if_embedded)
    restarted = LocalKnowledgeStore(settings)

    assert restarted._sagevdb_open_result.loaded is True
    assert restarted._sagevdb_persistence.generation == first_generation
    assert restarted._sagevdb_last_sync.embedded == 0
    assert restarted._sagevdb_last_sync.committed is False
    hits = restarted.search("What should students send before office hours?", top_k=1)
    assert hits
    assert hits[0].source_name == "persistent-note"


def test_sagevdb_persistence_sync_is_incremental(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        knowledge_backend="sagevdb",
        sagevdb_embedding_backend="hash",
        sagevdb_dimension=128,
        knowledge_sagevdb_persistence_dir=tmp_path / "sagevdb-index",
    )
    store = LocalKnowledgeStore(settings)
    first = store.add_document(
        KnowledgeDocumentCreate(
            title="First",
            content="alpha",
            tags=["one"],
            source_name="first",
        )
    )
    second = store.add_document(
        KnowledgeDocumentCreate(
            title="Second",
            content="beta",
            tags=["two"],
            source_name="second",
        )
    )
    assert store._sagevdb_last_sync.added == 1
    assert store._sagevdb_last_sync.embedded == 1

    store.update_document(
        first.document_id,
        KnowledgeDocumentCreate(
            title="First updated",
            content="alpha changed",
            tags=["one"],
            source_name="first",
        ),
    )
    assert store._sagevdb_last_sync.updated == 1
    assert store._sagevdb_last_sync.embedded == 1

    store.delete_documents([second.document_id])
    assert store._sagevdb_last_sync.deleted == 1
    assert store._sagevdb_last_sync.embedded == 0


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


def test_openai_embedding_provider_batches_normalizes_and_marks_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            inputs = calls[-1][1]["input"]
            assert isinstance(inputs, list)
            return {
                "data": [
                    {"index": index, "embedding": [3.0, 4.0] + ([0.0] * 30)}
                    for index, _ in enumerate(inputs)
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            assert kwargs["base_url"] == "https://gateway.example/v1/"
            assert kwargs["headers"]["Authorization"] == "Bearer test-key"

        def post(self, path: str, *, json: dict[str, object]):
            calls.append((path, json))
            return FakeResponse()

    monkeypatch.setattr(knowledge_base_module.httpx, "Client", FakeClient)
    settings = AppSettings(
        api_key="test-key",
        llm_base_url="https://gateway.example/v1",
        sagevdb_embedding_backend="openai",
        sagevdb_embedding_model="embedding-model",
        sagevdb_embedding_base_url="https://gateway.example/v1",
        sagevdb_dimension=32,
    )
    embedder = knowledge_base_module.OpenAITextEmbedder(settings, np)

    vectors = embedder.encode_many(["alpha", "beta"])
    query_vector = embedder.encode("question", is_query=True)

    assert calls[0] == (
        "embeddings",
        {
            "model": "embedding-model",
            "input": ["alpha", "beta"],
            "encoding_format": "float",
            "dimensions": 32,
        },
    )
    assert str(calls[1][1]["input"][0]).startswith("Instruct: ")
    assert np.isclose(np.linalg.norm(vectors[0]), 1.0)
    assert np.isclose(np.linalg.norm(query_vector), 1.0)


def test_openai_reranker_restores_scores_by_document_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            assert kwargs["base_url"] == "https://gateway.example/v1/"

        def post(self, path: str, *, json: dict[str, object]):
            assert path == "rerank"
            assert json["model"] == "reranker-model"
            assert json["top_n"] == 2
            return FakeResponse()

    monkeypatch.setattr(knowledge_base_module.httpx, "Client", FakeClient)
    settings = AppSettings(
        api_key="test-key",
        llm_base_url="https://gateway.example/v1",
        sagevdb_reranker_model="reranker-model",
        sagevdb_reranker_base_url="https://gateway.example/v1",
    )
    reranker = knowledge_base_module.OpenAIReranker(settings)

    assert reranker.rerank("question", ["first", "second"]) == [0.2, 0.9]


def test_sagevdb_remote_reranker_reorders_semantic_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_store = LocalKnowledgeStore(
        AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="local")
    )
    seed_store.add_document(
        KnowledgeDocumentCreate(
            title="First candidate",
            content="The first candidate is not the preferred semantic match.",
            tags=["candidate"],
            source_name="first",
        )
    )
    seed_store.add_document(
        KnowledgeDocumentCreate(
            title="Second candidate",
            content="The second candidate should be ranked first by the remote model.",
            tags=["candidate"],
            source_name="second",
        )
    )

    class FakeANNSDatabase:
        def __init__(self) -> None:
            self._metadata: list[dict[str, str]] = []

        def build_index(self, vectors, metadata=None) -> None:
            del vectors
            self._metadata = list(metadata or [])

        def search(self, query, k=10, include_metadata=True):
            del query, include_metadata
            return [
                SimpleNamespace(id=index, score=0.5, metadata=metadata)
                for index, metadata in enumerate(self._metadata[:k])
            ]

    class FakeReranker:
        def __init__(self, settings: AppSettings) -> None:
            del settings

        def rerank(self, query: str, documents: list[str]) -> list[float]:
            assert query == "semantic query"
            assert len(documents) == 2
            return [0.1, 0.9]

    monkeypatch.setattr(
        sagevdb_module,
        "create_database",
        lambda config, **kwargs: FakeANNSDatabase(),
    )
    monkeypatch.setattr(knowledge_base_module, "OpenAIReranker", FakeReranker)

    store = LocalKnowledgeStore(
        AppSettings(
            knowledge_base_dir=tmp_path,
            knowledge_backend="sagevdb",
            sagevdb_embedding_backend="hash",
            sagevdb_dimension=128,
            sagevdb_backend="sage-anns",
            sagevdb_reranker_enabled=True,
        )
    )

    hits = store.search("semantic query", top_k=1)

    assert hits
    assert hits[0].source_name == "second"


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
