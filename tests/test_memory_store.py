from __future__ import annotations

import json
import builtins
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.memory_store import (
    ConversationMemoryRecord,
    NeuroMemConversationStore,
    ProfileMemoryRecord,
)
from sage_faculty_twin.models import ChatRequest


def test_store_persists_neuromem_collections_without_records_or_profiles_dirs(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "conversation-memory",
        conversation_memory_index_type="segment",
    )
    store = NeuroMemConversationStore(settings)

    request = ChatRequest(
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        conversation_id="conv-persist",
        question="我想继续讨论推理系统选型。",
    )

    record = store.add_exchange(
        request,
        conversation_id="conv-persist",
        answer="建议先明确吞吐目标和延迟预算。",
        workflow_action="answer",
        interaction_domain="advising",
        knowledge_hit_count=1,
        booking_result=None,
    )
    consolidated = store.consolidate_profiles(record)

    reloaded_store = NeuroMemConversationStore(settings)

    recent_records = reloaded_store.list_recent_conversation_records("conv-persist")
    profiles = reloaded_store.list_profiles_for_student(
        student_name="Alice",
        student_email="alice@example.com",
    )

    assert consolidated >= 1
    assert [item.question for item in recent_records] == ["我想继续讨论推理系统选型。"]
    assert recent_records[0].answer == "建议先明确吞吐目标和延迟预算。"
    assert any(profile.category == "course_context" for profile in profiles)
    assert (settings.conversation_memory_dir / "memory_store.sqlite3").exists()
    assert not (settings.conversation_memory_dir / "records").exists()
    assert not (settings.conversation_memory_dir / "profiles").exists()
    if getattr(store._conversation_collection, "collection_type", "") == "neural_continual":
        assert "search" not in store._conversation_collection.index_metadata
    else:
        assert store._conversation_collection.index_metadata["search"]["type"] != "bm25"


def test_store_migrates_legacy_json_layout_once_and_removes_it(tmp_path: Path) -> None:
    base_dir = tmp_path / "conversation-memory"
    records_dir = base_dir / "records"
    profiles_dir = base_dir / "profiles"
    records_dir.mkdir(parents=True, exist_ok=True)
    profiles_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    legacy_root_record = ConversationMemoryRecord(
        memory_id="legacy-root-record",
        conversation_id="conv-legacy",
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        question="我们上次讨论了什么？",
        answer="上次主要讨论了推理系统选型。",
        workflow_action="answer",
        interaction_domain="advising",
        knowledge_hit_count=1,
        booking_summary=None,
        created_at=created_at,
    )
    legacy_records_record = ConversationMemoryRecord(
        memory_id="legacy-records-record",
        conversation_id="conv-legacy",
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        question="再补充一下下一步怎么做？",
        answer="下一步先把约束条件列清楚。",
        workflow_action="answer",
        interaction_domain="advising",
        knowledge_hit_count=0,
        booking_summary=None,
        created_at=datetime(2026, 6, 1, 12, 5, tzinfo=UTC),
    )
    legacy_profile = ProfileMemoryRecord(
        profile_id="alice-example-com-course_context",
        student_key="alice@example.com",
        student_name="Alice",
        student_email="alice@example.com",
        category="course_context",
        summary="该用户最近的主要交流场景是：科研指导",
        evidence="Question: 我们上次讨论了什么？",
        updated_at=created_at,
    )

    (base_dir / "legacy-root-record.json").write_text(
        json.dumps(legacy_root_record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (records_dir / "legacy-records-record.json").write_text(
        json.dumps(legacy_records_record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (profiles_dir / "alice-example-com-course_context.json").write_text(
        json.dumps(legacy_profile.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=base_dir,
        conversation_memory_index_type="segment",
    )

    store = NeuroMemConversationStore(settings)

    recent_records = store.list_recent_conversation_records("conv-legacy")
    profiles = store.list_profiles_for_student(
        student_name="Alice",
        student_email="alice@example.com",
    )

    assert [record.memory_id for record in recent_records] == [
        "legacy-records-record",
        "legacy-root-record",
    ]
    assert any(
        profile.profile_id == "alice-example-com-course_context" for profile in profiles
    )
    assert not (base_dir / "legacy-root-record.json").exists()
    assert not records_dir.exists()
    assert not profiles_dir.exists()
    assert (base_dir / "memory_store.sqlite3").exists()


def test_profile_upsert_replaces_existing_collection_entry(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "conversation-memory",
        conversation_memory_index_type="segment",
    )
    store = NeuroMemConversationStore(settings)

    older = ProfileMemoryRecord(
        profile_id="alice-example-com-course_context",
        student_key="alice@example.com",
        student_name="Alice",
        student_email="alice@example.com",
        category="course_context",
        summary="该用户最近的主要交流场景是：科研指导",
        evidence="Question: 第一次讨论科研指导。",
        updated_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
    )
    newer = ProfileMemoryRecord(
        profile_id="alice-example-com-course_context",
        student_key="alice@example.com",
        student_name="Alice",
        student_email="alice@example.com",
        category="course_context",
        summary="该用户最近的主要交流场景是：推理系统选型",
        evidence="Question: 第二次讨论推理系统选型。",
        updated_at=datetime(2026, 6, 1, 12, 30, tzinfo=UTC),
    )

    store._upsert_profile(older)
    store._persist_collection(store._profile_collection, store._profile_collection_dir)
    store._upsert_profile(newer)
    store._persist_collection(store._profile_collection, store._profile_collection_dir)

    reloaded_store = NeuroMemConversationStore(settings)
    profiles = reloaded_store.list_profiles_for_student(
        student_name="Alice",
        student_email="alice@example.com",
    )

    assert reloaded_store._profile_collection.size() == 1
    assert len(profiles) == 1
    assert profiles[0].summary == "该用户最近的主要交流场景是：推理系统选型"
    assert profiles[0].evidence == "Question: 第二次讨论推理系统选型。"


def test_store_canonicalizes_duplicate_profile_entries_on_startup(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "conversation-memory",
        conversation_memory_index_type="segment",
    )
    store = NeuroMemConversationStore(settings)

    older = ProfileMemoryRecord(
        profile_id="alice-example-com-course_context",
        student_key="alice@example.com",
        student_name="Alice",
        student_email="alice@example.com",
        category="course_context",
        summary="旧画像",
        evidence="Question: 旧问题。",
        updated_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
    )
    newer = ProfileMemoryRecord(
        profile_id="alice-example-com-course_context",
        student_key="alice@example.com",
        student_name="Alice",
        student_email="alice@example.com",
        category="course_context",
        summary="新画像",
        evidence="Question: 新问题。",
        updated_at=datetime(2026, 6, 1, 12, 30, tzinfo=UTC),
    )

    older_entry = store._build_profile_memory_entry(older)
    newer_entry = store._build_profile_memory_entry(newer)
    store._profile_collection.insert(
        older_entry.text, older_entry.metadata, index_names=["search"]
    )
    store._profile_collection.insert(
        newer_entry.text, newer_entry.metadata, index_names=["search"]
    )
    store._persist_collection(store._profile_collection, store._profile_collection_dir)

    deduped_store = NeuroMemConversationStore(settings)
    profiles = deduped_store.list_profiles_for_student(
        student_name="Alice",
        student_email="alice@example.com",
    )

    assert deduped_store._profile_collection.size() == 1
    assert len(profiles) == 1
    assert profiles[0].summary == "新画像"
    assert profiles[0].evidence == "Question: 新问题。"


def test_conversation_memory_uses_neural_continual_collection(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "conversation-memory",
        conversation_memory_collection_type="neural_continual",
        conversation_memory_index_type="segment",
    )
    store = NeuroMemConversationStore(settings)

    assert getattr(store._conversation_collection, "collection_type", "") == "neural_continual"

    request = ChatRequest(
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        conversation_id="conv-neural",
        question="我们上次讨论到哪一步了？",
    )
    store.add_exchange(
        request,
        conversation_id="conv-neural",
        answer="上次已经确定先做延迟与吞吐约束梳理。",
        workflow_action="answer",
        interaction_domain="advising",
        knowledge_hit_count=0,
        booking_result=None,
    )

    hits = store.search(request, conversation_id="conv-neural")
    runtime = store.runtime_snapshot()

    assert len(hits) >= 1
    assert runtime["conversation_stats"]["service_type"] == "online_continual_memory"
    assert runtime["conversation_stats"]["collection_type"] == "neural_continual"


def test_neural_continual_collection_type_persists_across_restart(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "conversation-memory",
        conversation_memory_collection_type="neural_continual",
        conversation_memory_index_type="segment",
    )
    store = NeuroMemConversationStore(settings)

    request = ChatRequest(
        student_name="Bob",
        student_email="bob@example.com",
        course_context="课程项目",
        conversation_id="conv-neural-reload",
        question="你还记得我之前的要求吗？",
    )
    store.add_exchange(
        request,
        conversation_id="conv-neural-reload",
        answer="记得，你希望优先保证响应延迟。",
        workflow_action="answer",
        interaction_domain="advising",
        knowledge_hit_count=0,
        booking_result=None,
    )

    reloaded_store = NeuroMemConversationStore(settings)
    hits = reloaded_store.search(request, conversation_id="conv-neural-reload")

    assert getattr(reloaded_store._conversation_collection, "collection_type", "") == "neural_continual"
    assert len(hits) >= 1


def test_explicit_sage_vdb_ann_index_fails_fast_without_sage_anns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def _import_with_missing_sage_anns(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sage_anns":
            raise ImportError("No module named sage_anns")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _import_with_missing_sage_anns)

    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "conversation-memory",
        conversation_memory_index_type="sage_vdb_ann",
    )

    with pytest.raises(RuntimeError, match="requires optional dependency 'sage_anns'"):
        NeuroMemConversationStore(settings)


def test_sage_vdb_ann_profile_index_uses_real_backend_and_reports_it(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sagevdb")
    pytest.importorskip("sage_anns")
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "conversation-memory",
        conversation_memory_collection_type="neural_continual",
        conversation_memory_index_type="sage_vdb_ann",
        sagevdb_backend="sage-anns",
        sagevdb_anns_algorithm="faiss_hnsw",
        sagevdb_dimension=128,
    )

    store = NeuroMemConversationStore(settings)
    profile_index = store._profile_collection.indexes["search"]

    assert profile_index.active_backend == "sage-anns:faiss_hnsw"
    profile_index.add("alpha", "faculty inference systems", {})
    assert profile_index.query("faculty inference systems", top_k=1) == ["alpha"]
    assert store.runtime_snapshot()["profile_stats"]["index_backends"] == {
        "search": "sage-anns:faiss_hnsw"
    }


def test_stale_sage_vdb_snapshot_config_is_migrated_without_faiss_fallback(
    tmp_path: Path,
) -> None:
    pytest.importorskip("sagevdb")
    pytest.importorskip("sage_anns")
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "conversation-memory",
        conversation_memory_collection_type="neural_continual",
        conversation_memory_index_type="sage_vdb_ann",
        sagevdb_backend="sage-anns",
        sagevdb_anns_algorithm="faiss_hnsw",
        sagevdb_dimension=128,
    )
    store = NeuroMemConversationStore(settings)
    store._persist_collection(
        store._profile_collection,
        store._profile_collection_dir,
    )
    with sqlite3.connect(store._sqlite_db_path) as connection:
        raw_metadata = connection.execute(
            "SELECT index_metadata FROM collection_snapshots WHERE name = ?",
            ("conversation-profile-memory",),
        ).fetchone()[0]
        metadata = json.loads(raw_metadata)
        metadata["search"]["config"]["backend_name"] = "cpp"
        metadata["search"]["config"]["allow_faiss_fallback"] = True
        connection.execute(
            "UPDATE collection_snapshots SET index_metadata = ? WHERE name = ?",
            (json.dumps(metadata), "conversation-profile-memory"),
        )
        connection.commit()

    reloaded = NeuroMemConversationStore(settings)
    profile_index = reloaded._profile_collection.indexes["search"]

    assert profile_index.active_backend == "sage-anns:faiss_hnsw"
    assert profile_index.allow_faiss_fallback is False
    with sqlite3.connect(reloaded._sqlite_db_path) as connection:
        migrated_raw = connection.execute(
            "SELECT index_metadata FROM collection_snapshots WHERE name = ?",
            ("conversation-profile-memory",),
        ).fetchone()[0]
    migrated_config = json.loads(migrated_raw)["search"]["config"]
    assert migrated_config["backend_name"] == "sage-anns"
    assert migrated_config["allow_faiss_fallback"] is False


def test_neural_conversation_memory_question_set_supports_followup_recall(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "conversation-memory",
        conversation_memory_collection_type="neural_continual",
        conversation_memory_index_type="segment",
    )
    store = NeuroMemConversationStore(settings)

    conversation_id = "conv-vamos-memory"

    store.add_exchange(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            course_context="科研指导",
            conversation_id=conversation_id,
            question="我们这周的项目主线是什么？",
        ),
        conversation_id=conversation_id,
        answer="主线是 VAMOS：围绕异构集群做显存感知与 SLO-aware 推理调度。",
        workflow_action="answer",
        interaction_domain="research",
        knowledge_hit_count=1,
        booking_result=None,
    )
    store.add_exchange(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            course_context="科研指导",
            conversation_id=conversation_id,
            question="当前优先优化指标是什么？",
        ),
        conversation_id=conversation_id,
        answer="优先保证首 token 延迟和稳定吞吐，同时控制显存占用波动。",
        workflow_action="answer",
        interaction_domain="research",
        knowledge_hit_count=1,
        booking_result=None,
    )

    question_set = [
        "上次我们讨论的 VAMOS 目标是什么？",
        "你还记得我们强调的 SLO 侧重点吗？",
    ]

    for question in question_set:
        hits = store.search(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id=conversation_id,
                question=question,
            ),
            conversation_id=conversation_id,
            top_k=3,
        )
        assert hits
        assert any(
            ("VAMOS" in hit.summary) or ("SLO" in hit.summary) or ("吞吐" in hit.summary)
            for hit in hits
        )

    runtime = store.runtime_snapshot()
    assert runtime["conversation_stats"]["service_type"] == "online_continual_memory"
    assert runtime["conversation_stats"]["collection_type"] == "neural_continual"
