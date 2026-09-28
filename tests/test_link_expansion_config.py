import math
from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate


def _store_with_link(tmp_path: Path, **settings_overrides):
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        knowledge_backend="local",
        knowledge_search_cache_ttl_seconds=300,
        **settings_overrides,
    )
    store = LocalKnowledgeStore(settings)
    source = store.add_document(
        KnowledgeDocumentCreate(
            title="Quasar retrieval note",
            content="quasar retrieval",
            tags=[],
            source_name="source",
        )
    )
    linked = store.add_document(
        KnowledgeDocumentCreate(
            title="Unrelated linked note",
            content="no lexical overlap",
            tags=[],
            source_name="linked",
        )
    )
    store._link_graph = {source.document_id: [linked.document_id]}
    return store, source, linked


def test_link_expansion_is_opt_in(tmp_path: Path) -> None:
    store, _source, linked = _store_with_link(tmp_path)

    assert store._link_expansion_enabled is False
    assert linked.document_id not in {
        hit.document_id for hit in store.search("quasar", top_k=2)
    }


def test_link_expansion_configuration_changes_results_and_cache_key(
    tmp_path: Path,
) -> None:
    store, source, linked = _store_with_link(
        tmp_path,
        knowledge_link_expansion_enabled=True,
        knowledge_link_expansion_decay=0.25,
        knowledge_link_expansion_max_documents=1,
    )

    expanded = store.search("quasar", top_k=2)
    source_hit = next(hit for hit in expanded if hit.document_id == source.document_id)
    linked_hit = next(hit for hit in expanded if hit.document_id == linked.document_id)
    assert math.isclose(linked_hit.score, source_hit.score * 0.25)

    store._settings.knowledge_link_expansion_max_documents = 0
    without_expansion = store.search("quasar", top_k=2)
    assert linked.document_id not in {hit.document_id for hit in without_expansion}


def test_link_expansion_settings_load_from_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DIGITAL_TWIN_KNOWLEDGE_LINK_EXPANSION_ENABLED", "true")
    monkeypatch.setenv("DIGITAL_TWIN_KNOWLEDGE_LINK_EXPANSION_DECAY", "0.4")
    monkeypatch.setenv("DIGITAL_TWIN_KNOWLEDGE_LINK_EXPANSION_MAX_DOCUMENTS", "3")

    settings = AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="local")

    assert settings.knowledge_link_expansion_enabled is True
    assert settings.knowledge_link_expansion_decay == 0.4
    assert settings.knowledge_link_expansion_max_documents == 3
