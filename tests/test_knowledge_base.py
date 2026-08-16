import json
from importlib.util import find_spec
from pathlib import Path
from datetime import UTC, datetime

import pytest

from conftest import available_knowledge_backends, requires_neuromem_model
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore, _build_query_profile
from sage_faculty_twin.models import (
    ChatRequest,
    InteractionIntent,
    KnowledgeDocumentCreate,
    KnowledgeDocumentRecord,
    KnowledgeSearchHit,
)
from sage_faculty_twin.service import DigitalTwinService


def test_knowledge_store_adds_and_searches_documents(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    store = LocalKnowledgeStore(settings)

    record = store.add_document(
        KnowledgeDocumentCreate(
            title="Lab onboarding policy",
            content="Students should read the onboarding checklist before asking for GPU access.",
            tags=["lab", "policy"],
            source_name="owner-note",
        )
    )

    hits = store.search("How do I get GPU access for the lab?")

    assert record.document_id
    assert len(store.list_documents()) == 1
    assert hits
    assert hits[0].title == "Lab onboarding policy"


def test_project_policy_query_is_not_misclassified_as_teaching_experiment() -> None:
    policy_profile = _build_query_profile(
        "SAGE项目劳务津贴怎么考评", visitor_profile="lab_member"
    )
    course_profile = _build_query_profile("课程项目实验说明")

    assert "experiment" not in policy_profile.document_types
    assert "teaching" not in policy_profile.topic_domains
    assert "experiment" in course_profile.document_types
    assert "teaching" in course_profile.topic_domains


def test_knowledge_store_caches_searches_and_invalidates_on_write(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        knowledge_backend="local",
        knowledge_search_cache_ttl_seconds=300,
        knowledge_search_cache_max_entries=8,
    )
    store = LocalKnowledgeStore(settings)
    store.add_document(
        KnowledgeDocumentCreate(
            title="General GPU note",
            content="Students can ask about GPU access.",
            tags=["lab"],
            source_name="general-note",
        )
    )

    first = store.search("GPU access")
    second = store.search("GPU access")

    assert first
    assert second[0].document_id == first[0].document_id
    assert len(store._search_cache) == 1

    store.add_document(
        KnowledgeDocumentCreate(
            title="Specific GPU access policy",
            content="GPU access requires reading the onboarding checklist before applying.",
            tags=["lab", "policy"],
            source_name="specific-policy",
        )
    )

    assert len(store._search_cache) == 0
    after_write = store.search("GPU access onboarding checklist")
    assert after_write
    assert after_write[0].title == "Specific GPU access policy"


def test_knowledge_store_upsert_noop_preserves_created_at_and_file(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    store = LocalKnowledgeStore(settings)

    payload = KnowledgeDocumentCreate(
        title="Lab onboarding policy",
        content="Students should read the onboarding checklist before asking for GPU access.",
        tags=["lab", "policy"],
        source_name="owner-note",
    )
    created = store.add_document(payload, rebuild_indexes=False)
    target = tmp_path / f"{created.document_id}.json"
    before_content = target.read_text(encoding="utf-8")

    upserted, inserted = store.upsert_document(payload, rebuild_indexes=False)
    after_content = target.read_text(encoding="utf-8")

    assert inserted is False
    assert upserted.document_id == created.document_id
    assert upserted.created_at == created.created_at
    assert after_content == before_content


def test_knowledge_store_derives_and_returns_explicit_metadata(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    store = LocalKnowledgeStore(settings)

    record = store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜研究生论文写作课程材料｜第 7 讲 发表高水平论文",
            content="第 7 讲讨论高水平论文选题、贡献表述与投稿准备。",
            tags=[
                "homepage",
                "teaching",
                "courseware",
                "pdf",
                "lecture",
                "identity:teacher",
                "domain:teaching",
                "audience:graduate",
                "course:paper-writing",
                "material:lecture",
                "lecture:7",
            ],
            source_name="homepage:contents/teaching/graduate-paper-writing-course.md#公开课件::lecture-7.pdf",
        )
    )

    hits = store.search("研究生论文写作 第 7 讲讲什么？", visitor_profile="paper_writing_student")

    assert record.metadata["identity"] == "teacher"
    assert record.metadata["domain"] == "teaching"
    assert record.metadata["course_id"] == "paper-writing"
    assert record.metadata["material_type"] == "lecture"
    assert record.metadata["ordinal_type"] == "lecture"
    assert record.metadata["ordinal"] == "7"
    assert hits
    assert hits[0].metadata["course_id"] == "paper-writing"


def test_knowledge_store_backfills_metadata_for_legacy_records(tmp_path: Path) -> None:
    legacy_record = KnowledgeDocumentRecord(
        document_id="legacy-lecture",
        title="课件正文｜大模型推理基础设施课程材料｜第 5 讲 KV 缓存",
        content="第 5 讲重点解释 KV 缓存、分页管理与状态复用。",
        tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
        source_name="homepage:contents/teaching/intro-to-llm-inference-engines.md#讲义与导论::lecture-5.pdf",
        created_at=datetime.now(UTC),
    )
    (tmp_path / "legacy-lecture.json").write_text(
        legacy_record.model_dump_json(), encoding="utf-8"
    )

    store = LocalKnowledgeStore(AppSettings(knowledge_base_dir=tmp_path))
    loaded_record = store.list_documents()[0]

    assert loaded_record.metadata["identity"] == "teacher"
    assert loaded_record.metadata["domain"] == "teaching"
    assert loaded_record.metadata["course_id"] == "llm-inference"
    assert loaded_record.metadata["material_type"] == "lecture"


def test_knowledge_store_loads_reviewed_curated_metadata(tmp_path: Path) -> None:
    record = {
        "document_id": "curated-team-policy",
        "title": "课题组制度｜测试规则",
        "content": "这是一条仅供课题组成员查询的制度。",
        "tags": ["team-policy", "audience:team"],
        "source_name": "wps-national-project:team-policy/test",
        "metadata": {
            "visibility": "team",
            "source_files": ["制度.docx"],
            "redactions": ["个人明细"],
        },
        "created_at": datetime.now(UTC).isoformat(),
        "review_status": "reviewed",
        "freshness_status": "current",
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
    (tmp_path / "curated-team-policy.json").write_text(
        __import__("json").dumps(record, ensure_ascii=False), encoding="utf-8"
    )

    loaded = LocalKnowledgeStore(AppSettings(knowledge_base_dir=tmp_path)).list_documents()[0]

    assert loaded.review_status == "reviewed"
    assert loaded.freshness_status == "current"
    assert loaded.metadata["source_files"] == ["制度.docx"]


def test_knowledge_store_supports_sagevdb_backend(tmp_path: Path) -> None:
    if find_spec("sagevdb") is None:
        pytest.skip("sagevdb is not installed in this environment")
    try:
        from sagevdb import DatabaseConfig  # noqa: F401
    except ImportError:
        pytest.skip("installed sagevdb package does not expose the required API")

    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        knowledge_backend="sagevdb",
        sagevdb_embedding_backend="hash",
        sagevdb_dimension=128,
    )
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="Neuromem meeting note",
            content="Before asking for a meeting, send your agenda and current blocker.",
            tags=["meeting", "policy"],
            source_name="owner-note",
        )
    )

    hits = store.search("What should I send before a meeting?")

    assert store.backend_name() == "sagevdb"
    assert store.embedding_backend_name() == "hash"
    assert hits
    assert hits[0].title == "Neuromem meeting note"


def test_knowledge_store_prefers_matching_teaching_material_type(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜大模型推理基础设施课程材料｜Tutorial 12 课程项目工作坊",
            content="Tutorial 12 介绍课程项目工作坊、里程碑与协作方式。",
            tags=["homepage", "teaching", "courseware", "pdf", "tutorial"],
            source_name="homepage:teaching/tutorial_12.pdf",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜大模型推理基础设施课程材料｜第 5 讲 KV 缓存",
            content="第 5 讲重点解释 KV 缓存、分页管理与状态复用。",
            tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
            source_name="homepage:teaching/lecture_05.pdf",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜大模型推理基础设施课程材料｜实验 3 调度与连续批处理实验",
            content="实验 3 讲解连续批处理观察、调度指标与实验步骤。",
            tags=["homepage", "teaching", "courseware", "pdf", "experiment"],
            source_name="homepage:teaching/experiment_3.pdf",
        )
    )

    tutorial_hits = store.search("Tutorial 12 讲什么？")
    lecture_hits = store.search("第 5 讲 KV 缓存讲什么？")
    experiment_hits = store.search("实验 3 调度与连续批处理实验讲什么？")

    assert tutorial_hits
    assert tutorial_hits[0].tags[-1] == "tutorial"
    assert lecture_hits
    assert lecture_hits[0].tags[-1] == "lecture"
    assert experiment_hits
    assert experiment_hits[0].tags[-1] == "experiment"


@requires_neuromem_model
def test_neuromem_backend_prefers_matching_teaching_material_type(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="neuromem")
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜大模型推理基础设施课程材料｜Tutorial 12 课程项目工作坊",
            content="Tutorial 12 介绍课程项目工作坊、分组机制与展示要求。",
            tags=["homepage", "teaching", "courseware", "pdf", "tutorial"],
            source_name="homepage:contents/teaching/tutorial_12.pdf",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜大模型推理基础设施课程材料｜第 5 讲 KV 缓存",
            content="第 5 讲讲解 KV 缓存、分页映射与 decode 状态复用。",
            tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
            source_name="homepage:contents/teaching/lecture_05.pdf",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜大模型推理基础设施课程材料｜实验 3 调度与连续批处理实验",
            content="实验 3 让学生观察调度、吞吐变化与连续批处理行为。",
            tags=["homepage", "teaching", "courseware", "pdf", "experiment"],
            source_name="homepage:contents/teaching/experiment_3.pdf",
        )
    )

    assert store.search("Tutorial 12 讲什么？")[0].tags[-1] == "tutorial"
    assert store.search("第 5 讲 KV 缓存讲什么？")[0].tags[-1] == "lecture"
    assert (
        store.search("实验 3 调度与连续批处理实验讲什么？")[0].tags[-1] == "experiment"
    )


@requires_neuromem_model
def test_neuromem_backend_finds_exact_tutorial_ordinal(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="neuromem")
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜大模型推理基础设施课程材料｜Tutorial 7 执行优化与异构路径",
            content="Tutorial 7 解释执行优化、异构路径，以及 kernel 加速不一定改善端到端性能的原因。",
            tags=[
                "homepage",
                "teaching",
                "courseware",
                "pdf",
                "tutorial",
                "course:llm-inference",
                "material:tutorial",
                "tutorial:7",
            ],
            source_name="homepage:contents/teaching/intro-to-llm-inference-engines.md#tutorial-7.pdf",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜大模型推理基础设施课程材料｜第 7 讲 推理系统架构",
            content="第 7 讲介绍推理系统架构、调度路径与服务化设计。",
            tags=[
                "homepage",
                "teaching",
                "courseware",
                "pdf",
                "lecture",
                "course:llm-inference",
                "material:lecture",
                "lecture:7",
            ],
            source_name="homepage:contents/teaching/intro-to-llm-inference-engines.md#lecture-7.pdf",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜大模型推理基础设施课程材料｜Tutorial 12 课程项目工作坊",
            content="Tutorial 12 介绍课程项目工作坊、里程碑与协作方式。",
            tags=[
                "homepage",
                "teaching",
                "courseware",
                "pdf",
                "tutorial",
                "course:llm-inference",
                "material:tutorial",
                "tutorial:12",
            ],
            source_name="homepage:contents/teaching/intro-to-llm-inference-engines.md#tutorial-12.pdf",
        )
    )

    hits = store.search("大模型推理引擎这门课 tutorial 7 主要讲什么？", top_k=3)

    assert hits
    assert (
        hits[0].title
        == "课件正文｜大模型推理基础设施课程材料｜Tutorial 7 执行优化与异构路径"
    )


def test_knowledge_store_separates_same_lecture_number_across_courses(
    tmp_path: Path,
) -> None:
    for backend_name in available_knowledge_backends():
        if backend_name == "sagevdb":
            continue  # this test only exercises local + neuromem
        settings = AppSettings(
            knowledge_base_dir=tmp_path / backend_name,
            knowledge_backend="neuromem" if backend_name == "neuromem" else "local",
        )
        store = LocalKnowledgeStore(settings)

        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜大模型推理基础设施课程材料｜第 7 讲 推理系统架构",
                content="第 7 讲介绍推理系统架构、调度路径与服务化设计。",
                tags=[
                    "homepage",
                    "teaching",
                    "courseware",
                    "pdf",
                    "lecture",
                    "identity:teacher",
                    "domain:teaching",
                    "course:llm-inference",
                    "material:lecture",
                    "lecture:7",
                ],
                source_name="homepage:contents/teaching/intro-to-llm-inference-engines.md#讲义与导论::lecture-7.pdf",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜研究生论文写作课程材料｜第 7 讲 发表高水平论文",
                content="第 7 讲讨论高水平论文选题、贡献表述与投稿准备。",
                tags=[
                    "homepage",
                    "teaching",
                    "courseware",
                    "pdf",
                    "lecture",
                    "identity:teacher",
                    "domain:teaching",
                    "course:paper-writing",
                    "material:lecture",
                    "lecture:7",
                ],
                source_name="homepage:contents/teaching/graduate-paper-writing-course.md#公开课件::lecture-7.pdf",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜数据库实验课课程材料｜第 7 讲 索引与查询优化实验",
                content="第 7 讲围绕数据库索引、执行计划和查询优化实验展开。",
                tags=[
                    "homepage",
                    "teaching",
                    "courseware",
                    "pdf",
                    "lecture",
                    "identity:teacher",
                    "domain:teaching",
                    "course:database-lab",
                    "material:lecture",
                    "lecture:7",
                ],
                source_name="homepage:contents/teaching/database-lab.md#公开课件::lecture-7.pdf",
            )
        )

        llm_hits = store.search("大模型推理基础设施 第 7 讲讲什么？", top_k=2)
        writing_hits = store.search("研究生论文写作 第 7 讲讲什么？", top_k=2)
        database_hits = store.search("数据库实验课 第 7 讲讲什么？", top_k=2)

        assert llm_hits
        assert "course:llm-inference" in llm_hits[0].tags
        assert writing_hits
        assert "course:paper-writing" in writing_hits[0].tags
        assert database_hits
        assert "course:database-lab" in database_hits[0].tags


def test_knowledge_store_prioritizes_research_materials_over_courseware_noise(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="研究总览｜研究主线",
            content="研究主线围绕流处理系统、大模型推理与记忆增强。",
            tags=["homepage", "research", "publication", "overview"],
            source_name="homepage:contents/publications.md#首页导读",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="主页资料｜研究板块",
            content="主要研究大模型推理基础设施、智能系统与数据管理。",
            tags=["homepage", "profile"],
            source_name="homepage:contents/home.md#研究板块",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜研究生论文写作课程材料｜第 7 讲 发表高水平论文",
            content="这一讲讨论如何组织论文写作与研究表述。",
            tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
            source_name="homepage:contents/teaching/graduate-paper-writing-course.md#公开课件::lecture-7.pdf",
        )
    )

    hits = store.search("你的研究主线是什么？", top_k=3)

    assert hits
    assert hits[0].title == "研究总览｜研究主线"
    assert all("teaching" not in hit.tags for hit in hits[:2])


@requires_neuromem_model
def test_neuromem_backend_prioritizes_research_materials_over_courseware_noise(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="neuromem")
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="研究总览｜研究主线",
            content="研究主线围绕流处理系统、大模型推理与记忆增强。",
            tags=["homepage", "research", "publication", "overview"],
            source_name="homepage:contents/publications.md#首页导读",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="主页资料｜研究板块",
            content="主要研究大模型推理基础设施、智能系统与数据管理。",
            tags=["homepage", "profile"],
            source_name="homepage:contents/home.md#研究板块",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜研究生论文写作课程材料｜第 7 讲 发表高水平论文",
            content="这一讲讨论如何组织论文写作与研究表述。",
            tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
            source_name="homepage:contents/teaching/graduate-paper-writing-course.md#公开课件::lecture-7.pdf",
        )
    )

    hits = store.search("你主要研究什么？", top_k=3)

    assert hits
    assert hits[0].title in {"研究总览｜研究主线", "主页资料｜研究板块"}
    assert all("teaching" not in hit.tags for hit in hits[:2])


@requires_neuromem_model
def test_neuromem_backend_prioritizes_named_paper_queries_over_courseware_noise(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="neuromem")
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="论文提炼｜FlowRAG: Continual Learning for Dynamic Retriever in Retrieval-Augmented Generation",
            content="FlowRAG 主要解决动态语料下检索器持续学习与检索质量退化问题。",
            tags=[
                "homepage",
                "research",
                "publication",
                "paper-digest",
                "rag",
                "continual-learning",
            ],
            source_name="homepage:contents/research_papers/publications_summary.md#0.1 FlowRAG",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="主页资料｜研究板块",
            content="主要研究大模型推理基础设施、检索增强和数据系统。",
            tags=["homepage", "profile"],
            source_name="homepage:contents/home.md#研究板块",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课件正文｜研究生论文写作课程材料｜第 7 讲 发表高水平论文",
            content="这一讲讨论如何组织论文写作与研究表述。",
            tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
            source_name="homepage:contents/teaching/graduate-paper-writing-course.md#公开课件::lecture-7.pdf",
        )
    )

    hits = store.search("FlowRAG 主要做什么？", top_k=3)

    assert hits
    assert hits[0].title.startswith("论文提炼｜FlowRAG")
    assert all("teaching" not in hit.tags for hit in hits[:2])
    assert all("profile" not in hit.tags for hit in hits[:2])
    assert all(
        "flowrag" in hit.title.lower() or "flowrag" in (hit.source_name or "").lower()
        for hit in hits
    )


def test_search_dedupes_adjacent_chunks_from_same_courseware(tmp_path: Path) -> None:
    for backend_name in available_knowledge_backends():
        if backend_name == "sagevdb":
            continue  # this test only exercises local + neuromem
        settings = AppSettings(
            knowledge_base_dir=tmp_path / backend_name,
            knowledge_backend="neuromem" if backend_name == "neuromem" else "local",
        )
        store = LocalKnowledgeStore(settings)

        common_source = "homepage:contents/teaching/graduate-paper-writing-course.md#公开课件::contents/teaching/graduate-paper-writing-course/2026/lecture-7-beamer.pdf"
        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜研究生论文写作课程材料｜第 7 讲 发表高水平论文（第1部分）",
                content="第 7 讲介绍高水平论文选题、结构设计与叙事主线。",
                tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
                source_name=f"{common_source}::part-1",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜研究生论文写作课程材料｜第 7 讲 发表高水平论文（第2部分）",
                content="第 7 讲继续讨论实验组织、结果汇报与贡献表述。",
                tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
                source_name=f"{common_source}::part-2",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜研究生论文写作课程材料｜第 7 讲 发表高水平论文（第3部分）",
                content="第 7 讲补充论文打磨、投稿准备与答辩陈述。",
                tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
                source_name=f"{common_source}::part-3",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课程资料｜研究生论文写作课程材料｜公开课件",
                content="这里汇总第 7 讲到第 8 讲的公开课件链接。",
                tags=["homepage", "teaching", "courseware", "lecture"],
                source_name="homepage:contents/teaching/graduate-paper-writing-course.md#公开课件",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜大模型推理基础设施课程材料｜第 9 讲 异构平台适配（第1部分）",
                content="第 9 讲介绍 GPU、CPU 与异构路径适配。",
                tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
                source_name="homepage:contents/teaching/intro-to-llm-inference-engines.md#讲义与导论::lecture-9.pdf::part-1",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="论文提炼｜Select Edges Wisely",
                content="这篇论文讨论磁盘 ANN 图布局优化。",
                tags=["homepage", "research", "publication", "paper-digest"],
                source_name="homepage:contents/research_papers/publications_summary.md#0.7 Select Edges Wisely",
            )
        )

        hits = store.search("第 7 讲 发表高水平论文讲什么？", top_k=5)
        same_courseware_hits = [
            hit
            for hit in hits
            if hit.source_name and hit.source_name.startswith(common_source)
        ]

        assert hits
        assert len(same_courseware_hits) == 1
        assert "第1部分" in same_courseware_hits[0].title
        assert all(
            (hit.source_name or "").startswith(common_source)
            or hit.source_name
            == "homepage:contents/teaching/graduate-paper-writing-course.md#公开课件"
            for hit in hits
        )
        assert all("paper-digest" not in hit.tags for hit in hits)


def test_knowledge_store_prefers_research_and_preparation_materials_over_courseware(
    tmp_path: Path,
) -> None:
    for backend_name in available_knowledge_backends():
        if backend_name == "sagevdb":
            continue  # this test only exercises local + neuromem
        knowledge_dir = tmp_path / backend_name
        settings = AppSettings(
            knowledge_base_dir=knowledge_dir,
            knowledge_backend="neuromem" if backend_name == "neuromem" else "local",
        )
        store = LocalKnowledgeStore(settings)

        store.add_document(
            KnowledgeDocumentCreate(
                title="研究总览｜研究主线",
                content="研究主线聚焦大模型推理、记忆系统与流处理基础设施。",
                tags=["homepage", "research", "publication", "overview"],
                source_name=f"{backend_name}:research-overview",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="论文提炼｜FlowRAG: Continual Learning for Dynamic Retriever in Retrieval-Augmented Generation",
                content="FlowRAG 主要解决动态语料下检索器持续学习与检索增强生成质量下降的问题。",
                tags=["homepage", "research", "publication", "paper-digest", "rag"],
                source_name=f"{backend_name}:flowrag",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="主页资料｜研究板块",
                content="我主要研究大模型推理系统、检索增强和系统优化。",
                tags=["homepage", "profile"],
                source_name=f"{backend_name}:profile",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜研究生论文写作课程材料｜第 8 讲 研究生毕业论文中常见的问题",
                content="本讲多次讨论研究主线、研究问题、论文结构与研究主线表述方式。",
                tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
                source_name=f"{backend_name}:teaching-research-course",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课程答疑前建议先准备的内容（模板）",
                content="和老师约时间前，建议先准备简历、议程、当前问题与想讨论的方向。",
                tags=["course", "qa", "preparation"],
                source_name=f"{backend_name}:starter-template",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜大模型推理基础设施课程材料｜第 13 讲 课程项目工作坊",
                content="课程项目工作坊介绍了项目准备、演示准备与里程碑准备。",
                tags=["homepage", "teaching", "courseware", "pdf", "lecture"],
                source_name=f"{backend_name}:project-workshop",
            )
        )

        research_hits = store.search("你的研究主线是什么？", top_k=3)
        paper_hits = store.search("FlowRAG 主要做什么？", top_k=3)
        preparation_hits = store.search("和老师约时间前，我应该先准备什么？", top_k=3)

        assert research_hits
        assert research_hits[0].title == "研究总览｜研究主线"
        assert "teaching" not in research_hits[0].tags
        assert all("teaching" not in hit.tags for hit in research_hits)
        assert paper_hits
        assert paper_hits[0].title.startswith("论文提炼｜FlowRAG")
        assert all("teaching" not in hit.tags for hit in paper_hits)
        assert preparation_hits
        assert preparation_hits[0].title == "课程答疑前建议先准备的内容（模板）"
        assert "teaching" not in preparation_hits[0].tags
        assert all("teaching" not in hit.tags for hit in preparation_hits)


@requires_neuromem_model
def test_neuromem_backend_prefers_research_materials_for_research_queries(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="neuromem")
    store = LocalKnowledgeStore(settings)

    store.add_document(
        KnowledgeDocumentCreate(
            title="研究总览｜研究主线",
            content="主要研究方向包括流处理系统、状态管理、检索增强生成与智能体工作流。",
            tags=["homepage", "research", "publication", "overview"],
            source_name="homepage:contents/publications.md#首页导读",
        )
    )
    store.add_document(
        KnowledgeDocumentCreate(
            title="课程资料｜研究生论文写作课程材料",
            content="课程介绍论文写作结构、投稿准备与毕业论文常见问题。",
            tags=["homepage", "teaching", "courseware"],
            source_name="homepage:contents/teaching/graduate-paper-writing-course.md#intro",
        )
    )

    hits = store.search("你主要研究什么？")

    assert hits
    assert hits[0].title == "研究总览｜研究主线"
    assert "research" in hits[0].tags


def test_knowledge_store_reweights_paper_writing_results_by_visitor_profile(
    tmp_path: Path,
) -> None:
    for backend_name in available_knowledge_backends():
        if backend_name == "sagevdb":
            continue  # this test only exercises local + neuromem
        knowledge_dir = tmp_path / f"visitor-profile-{backend_name}"
        settings = AppSettings(
            knowledge_base_dir=knowledge_dir,
            knowledge_backend="neuromem" if backend_name == "neuromem" else "local",
        )
        store = LocalKnowledgeStore(settings)

        store.add_document(
            KnowledgeDocumentCreate(
                title="课程资料｜研究生论文写作课程材料",
                content="课程重点覆盖论文结构、related work、投稿准备和毕业论文常见问题。",
                tags=["homepage", "teaching", "courseware", "lecture"],
                source_name="homepage:contents/teaching/graduate-paper-writing-course.md#intro",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="研究总览｜论文写作智能体",
                content="研究方向聚焦自动写作 agent、审稿反馈吸收和协同 drafting workflow。",
                tags=["homepage", "research", "publication", "overview"],
                source_name=f"{backend_name}:paper-writing-agent-overview",
            )
        )

        paper_course_hits = store.search(
            "论文写作相关内容我应该先看什么？",
            top_k=2,
            visitor_profile="paper_writing_student",
        )
        lab_hits = store.search(
            "论文写作相关内容我应该先看什么？",
            top_k=2,
            visitor_profile="lab_member",
        )

        assert paper_course_hits
        assert paper_course_hits[0].title == "课程资料｜研究生论文写作课程材料"
        assert "teaching" in paper_course_hits[0].tags
        assert lab_hits
        assert lab_hits[0].title == "研究总览｜论文写作智能体"
        assert "research" in lab_hits[0].tags


def test_knowledge_store_enforces_audience_visibility_by_visitor_profile(
    tmp_path: Path,
) -> None:
    for backend_name in available_knowledge_backends():
        if backend_name == "sagevdb":
            continue  # this test only exercises local + neuromem
        knowledge_dir = tmp_path / f"audience-{backend_name}"
        settings = AppSettings(
            knowledge_base_dir=knowledge_dir,
            knowledge_backend="neuromem" if backend_name == "neuromem" else "local",
        )
        store = LocalKnowledgeStore(settings)

        for title, tags in (
            ("公开资料｜实验室简介", ["homepage", "profile"]),
            (
                "本科生资料｜数据库实验课导学",
                ["homepage", "teaching", "courseware", "audience:undergraduate"],
            ),
            (
                "研究生资料｜论文写作导学",
                ["homepage", "teaching", "courseware", "audience:graduate"],
            ),
            (
                "组内资料｜每周组会约定",
                ["homepage", "meeting", "policy", "audience:lab_member"],
            ),
            (
                "管理资料｜课题组成员信息总表",
                [
                    "homepage",
                    "profile",
                    "team",
                    "members",
                    "audience:manager",
                    "audience:admin",
                ],
            ),
            (
                "超管资料｜管理员密钥轮换说明",
                ["homepage", "policy", "audience:admin"],
            ),
        ):
            store.add_document(
                KnowledgeDocumentCreate(
                    title=title,
                    content="访问控制测试资料：所有身份都能命中这段关键词，用来验证 audience 过滤。",
                    tags=tags,
                    source_name=f"test:{backend_name}:{title}",
                )
            )

        query = "访问控制测试资料"
        anonymous_titles = {hit.title for hit in store.search(query, top_k=8)}
        visitor_titles = {
            hit.title
            for hit in store.search(query, top_k=8, visitor_profile="general_visitor")
        }
        undergraduate_titles = {
            hit.title
            for hit in store.search(query, top_k=8, visitor_profile="hust_undergraduate")
        }
        graduate_titles = {
            hit.title
            for hit in store.search(query, top_k=8, visitor_profile="paper_writing_student")
        }
        lab_titles = {
            hit.title for hit in store.search(query, top_k=8, visitor_profile="lab_member")
        }
        manager_titles = {
            hit.title for hit in store.search(query, top_k=8, admin_role="manager")
        }
        super_admin_titles = {
            hit.title for hit in store.search(query, top_k=8, admin_role="super_admin")
        }

        assert anonymous_titles == {"公开资料｜实验室简介"}
        assert visitor_titles == {"公开资料｜实验室简介"}
        assert undergraduate_titles == {"公开资料｜实验室简介", "本科生资料｜数据库实验课导学"}
        assert graduate_titles == {"公开资料｜实验室简介", "研究生资料｜论文写作导学"}
        assert lab_titles == {
            "公开资料｜实验室简介",
            "本科生资料｜数据库实验课导学",
            "研究生资料｜论文写作导学",
            "组内资料｜每周组会约定",
        }
        assert manager_titles == {
            "公开资料｜实验室简介",
            "本科生资料｜数据库实验课导学",
            "研究生资料｜论文写作导学",
            "组内资料｜每周组会约定",
            "管理资料｜课题组成员信息总表",
        }
        assert super_admin_titles == {
            "公开资料｜实验室简介",
            "本科生资料｜数据库实验课导学",
            "研究生资料｜论文写作导学",
            "组内资料｜每周组会约定",
            "管理资料｜课题组成员信息总表",
            "超管资料｜管理员密钥轮换说明",
        }


def test_team_visibility_is_authoritative_and_requires_lab_member(tmp_path: Path) -> None:
    store = LocalKnowledgeStore(AppSettings(knowledge_base_dir=tmp_path))
    store.add_document(
        KnowledgeDocumentCreate(
            title="课题组制度｜内部评分规则",
            content="内部评分规则采用二十分制。",
            tags=["team-policy", "audience:public"],
            source_name="wps-national-project:team-policy/scoring",
            metadata={"visibility": "team"},
        )
    )

    assert not store.search("内部评分规则", visitor_profile="general_visitor")
    hits = store.search("内部评分规则", visitor_profile="lab_member")
    assert hits and hits[0].title == "课题组制度｜内部评分规则"


def test_infer_default_audience_restricts_sensitive_sources(
    tmp_path: Path,
) -> None:
    """Documents from workspace/ or with proposal/roadmap tags should be
    auto-restricted to lab_member when no explicit audience tag is present."""
    for backend_name in available_knowledge_backends():
        if backend_name == "sagevdb":
            continue
        knowledge_dir = tmp_path / f"infer-audience-{backend_name}"
        settings = AppSettings(
            knowledge_base_dir=knowledge_dir,
            knowledge_backend="neuromem" if backend_name == "neuromem" else "local",
        )
        store = LocalKnowledgeStore(settings)

        for title, tags, source_name in (
            # Workspace sources should be auto-restricted
            (
                "VAMOS Proposal",
                ["research", "proposal"],
                "workspace/vamos/proposal",
            ),
            (
                "VAMOS Roadmap",
                ["research", "roadmap"],
                "workspace/vamos/roadmap",
            ),
            (
                "VAMOS README",
                ["research", "agenda"],
                "workspace/vamos",
            ),
            # Public content should remain unrestricted
            (
                "公开论文｜MorphStream",
                ["research", "publication"],
                "homepage:contents/research_papers/2025/morphstream.md",
            ),
            (
                "公开资料｜实验室简介",
                ["homepage", "profile"],
                "homepage:contents/index.md",
            ),
        ):
            store.add_document(
                KnowledgeDocumentCreate(
                    title=title,
                    content="敏感内容自动限制测试：所有身份都能命中这段关键词。",
                    tags=tags,
                    source_name=source_name,
                )
            )

        query = "敏感内容自动限制测试"
        visitor_titles = {
            hit.title
            for hit in store.search(query, top_k=8, visitor_profile="general_visitor")
        }
        lab_titles = {
            hit.title
            for hit in store.search(query, top_k=8, visitor_profile="lab_member")
        }

        # Guest should only see public content
        assert visitor_titles == {"公开论文｜MorphStream", "公开资料｜实验室简介"}
        # Lab member should see everything
        assert lab_titles == {
            "VAMOS Proposal",
            "VAMOS Roadmap",
            "VAMOS README",
            "公开论文｜MorphStream",
            "公开资料｜实验室简介",
        }


def test_service_prompt_includes_retrieved_owner_materials(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service.add_knowledge(
        KnowledgeDocumentCreate(
            title="Meeting preference",
            content="I prefer students to send an agenda and the current blocker before the meeting.",
            tags=["meeting"],
            source_name="advisor-note",
        )
    )

    hits = service.search_knowledge("What should I prepare before a meeting?").hits
    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": None,
                "question": "What should I prepare before a meeting?",
            },
        )(),
        knowledge_hits=hits,
    )

    assert hits
    assert "Reusable retrieved materials" in prompt
    assert "Meeting preference" in prompt
    assert "agenda" in prompt
    assert prompt.index("Reusable retrieved materials") < prompt.index("Student name: Alice")


def test_service_prompt_allows_general_accelerator_memory_guidance(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="local")
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "guest",
                "course_context": None,
                "visitor_profile": "general_visitor",
                "question": "如何优化大模型推理引擎在Ascend NPU上的内存管理？",
            },
        )(),
        knowledge_hits=[],
    )

    assert "General technical guidance" in prompt
    assert "Do not answer it only with '我没有直接研究经验'" in prompt
    assert "exactly 4 numbered tactics" in prompt
    assert "KV-cache or activation residency" in prompt
    assert "fragmentation or memory pools" in prompt
    assert "Fast-answer guidance" not in prompt


def test_service_prompt_keeps_private_materials_out_of_materialized_prefix(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": None,
                "visitor_profile": "lab_member",
                "question": "内部账号怎么处理？",
            },
        )(),
        knowledge_hits=[
            KnowledgeSearchHit(
                document_id="private-hit",
                title="内部账号记录",
                excerpt="这是一段不应进入 materialized prefix 的私有账号材料。",
                score=1.0,
                tags=["audience:private"],
                source_name="user_accounts/internal",
                metadata={"audience": "private"},
            ),
        ],
    )

    assert "Reusable retrieved materials" not in prompt
    assert "Relevant owner materials:" in prompt
    assert "内部账号记录" in prompt


def test_service_prompt_enforces_team_knowledge_answer_boundary(tmp_path: Path) -> None:
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Member",
                "course_context": None,
                "visitor_profile": "lab_member",
                "question": "Copilot 报销分几档？",
            },
        )(),
        knowledge_hits=[
            KnowledgeSearchHit(
                document_id="team-policy",
                title="课题组制度｜报销规则",
                excerpt="普通成员可查询通用报销档位。",
                score=10.0,
                tags=["team-policy", "audience:team"],
                source_name="wps-national-project:team-policy/reimbursement",
                metadata={"visibility": "team"},
            )
        ],
    )

    assert "Team-knowledge safety boundary" in prompt
    assert "Never provide or infer individual performance" in prompt
    assert "does not authorize Internet" in prompt
    assert "responsible project lead must confirm" in prompt


def test_structured_team_policy_fast_response_is_member_only_and_cited(
    tmp_path: Path, monkeypatch
) -> None:
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    hit = KnowledgeSearchHit(
        document_id="team-policy",
        title="课题组制度｜季度奖励",
        excerpt="季度奖项规则。",
        score=20.0,
        tags=["team-policy", "audience:team"],
        source_name="wps-national-project:team-policy/quarterly-ranking",
        metadata={
            "visibility": "team",
            "qa_pairs": [
                {
                    "match_all": ["季度"],
                    "match_any": ["奖项", "奖励"],
                    "answer": "特别贡献奖、一等奖、二等奖、三等奖。",
                }
            ],
        },
    )
    monkeypatch.setattr(service._knowledge_store, "search", lambda *args, **kwargs: [hit])

    member = service.try_fast_answer(
        ChatRequest(student_name="Member", visitor_profile="lab_member", question="季度有哪些奖项？")
    )
    guest = service.try_fast_answer(
        ChatRequest(
            student_name="Guest",
            visitor_profile="general_visitor",
            question="季度有哪些奖项？",
        )
    )

    assert member is not None
    assert member.answer == "特别贡献奖、一等奖、二等奖、三等奖。"
    assert member.used_model == "sage-policy-fast-path"
    assert member.knowledge_hits == [hit]
    assert member.answer_basis[0].source_label == hit.source_name
    assert guest is not None
    assert guest.used_model == "sage-policy-access-boundary"
    assert guest.knowledge_hits == []


def test_structured_knowledge_fast_response_supports_strict_string_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    member_hit = KnowledgeSearchHit(
        document_id="member-policy",
        title="固定学术交流安排",
        excerpt="每周三下午学术路线交流。",
        score=20.0,
        tags=["audience:lab_member"],
        source_name="wps-curated:member-schedule",
        metadata={
            "audience": "lab_member",
            "qa_pairs_json": json.dumps(
                [
                    {
                        "match_all": ["交流"],
                        "match_any": ["时间", "固定"],
                        "answer": "学术路线每周三下午14:00交流。",
                    }
                ],
                ensure_ascii=False,
            ),
        },
    )
    public_hit = KnowledgeSearchHit(
        document_id="public-job",
        title="系统研发工程师招聘",
        excerpt="公开招聘系统研发工程师。",
        score=20.0,
        tags=["audience:public"],
        source_name="wps-curated:public-job",
        metadata={
            "audience": "public",
            "qa_pairs_json": json.dumps(
                [
                    {
                        "match_all": ["工程师"],
                        "match_any": ["招聘"],
                        "answer": "公开招聘系统研发工程师3名。",
                    }
                ],
                ensure_ascii=False,
            ),
        },
    )

    monkeypatch.setattr(service._knowledge_store, "search", lambda *args, **kwargs: [member_hit])
    member = service.try_fast_answer(
        ChatRequest(
            student_name="Member",
            visitor_profile="lab_member",
            question="固定交流时间？",
            deep_thinking=False,
        )
    )
    guest = service.try_fast_answer(
        ChatRequest(
            student_name="Guest",
            visitor_profile="general_visitor",
            question="固定交流时间？",
            deep_thinking=False,
        )
    )

    assert member is not None
    assert member.answer == "学术路线每周三下午14:00交流。"
    assert guest is not None
    assert guest.used_model == "sage-policy-access-boundary"
    assert "不能向公开访客提供或推断" in guest.answer
    assert guest.knowledge_hits == []

    monkeypatch.setattr(service._knowledge_store, "search", lambda *args, **kwargs: [public_hit])
    public = service.try_fast_answer(
        ChatRequest(
            student_name="Guest",
            visitor_profile="general_visitor",
            question="系统研发工程师招聘",
            deep_thinking=False,
        )
    )

    assert public is not None
    assert public.answer == "公开招聘系统研发工程师3名。"
    assert public.answer_basis[0].basis_label == "公开资料"


def test_token_expense_question_is_not_treated_as_credential_exfiltration() -> None:
    request = ChatRequest(student_name="Member", question="Copilot 和 Token 报销分几档？")

    assert DigitalTwinService._check_sensitive_boundary_request(request) is None


def test_service_materialized_context_uses_stable_hit_order(tmp_path: Path) -> None:
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": None,
                "visitor_profile": "lab_member",
                "question": "两个材料都相关时怎么回答？",
            },
        )(),
        knowledge_hits=[
            KnowledgeSearchHit(
                document_id="b-doc",
                title="B material",
                excerpt="B excerpt",
                score=0.99,
                tags=["audience:lab_member"],
                source_name="kb/b",
                metadata={"audience": "lab_member"},
            ),
            KnowledgeSearchHit(
                document_id="a-doc",
                title="A material",
                excerpt="A excerpt",
                score=0.5,
                tags=["audience:lab_member"],
                source_name="kb/a",
                metadata={"audience": "lab_member"},
            ),
        ],
    )

    assert prompt.index("A material") < prompt.index("B material")


def test_service_prompt_filters_teaching_materials_for_research_queries(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": None,
                "question": "你主要研究什么？",
            },
        )(),
        knowledge_hits=[
            KnowledgeSearchHit(
                document_id="research-hit",
                title="研究总览｜研究主线",
                excerpt="主要研究方向包括流处理、状态管理与智能体工作流。",
                score=1.0,
                tags=["homepage", "research", "publication", "overview"],
                source_name="homepage:contents/publications.md#首页导读",
            ),
            KnowledgeSearchHit(
                document_id="teaching-hit",
                title="课程资料｜研究生论文写作课程材料",
                excerpt="课程介绍论文结构、写作方法与毕业论文常见问题。",
                score=0.9,
                tags=["homepage", "teaching", "courseware"],
                source_name="homepage:contents/teaching/graduate-paper-writing-course.md#intro",
            ),
        ],
        interaction_intent=InteractionIntent(
            action="answer",
            domain="research",
            retrieval_scopes=["publications", "profile"],
            exclude_scopes=["courseware"],
            confidence=0.9,
        ),
    )

    assert "current focus first" in prompt
    assert "LLM inference engines" in prompt
    assert "historical foundations or method background" in prompt
    assert "研究总览｜研究主线" in prompt
    assert "研究生论文写作课程材料" not in prompt


def test_service_filters_generic_profile_hits_for_preparation_guidance_queries(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    support = service._build_support()

    filtered = support._filter_knowledge_hits_by_intent(
        [
            KnowledgeSearchHit(
                document_id="profile-hit",
                title="主页资料｜研究板块",
                excerpt="当前研究大致分为三个相互衔接的板块。",
                score=1.0,
                tags=["homepage", "profile"],
                source_name="homepage:contents/home.md#研究板块",
            ),
            KnowledgeSearchHit(
                document_id="meeting-hit",
                title="Meeting preference",
                excerpt="Before asking for a meeting, send your agenda, current blocker, and latest draft.",
                score=0.8,
                tags=["meeting", "policy", "preparation"],
                source_name="advisor-note",
            ),
        ],
        InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.95,
        ),
    )

    assert [hit.document_id for hit in filtered] == ["meeting-hit"]


def test_service_drops_scope_matched_hit_without_query_evidence(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    support = service._build_support()
    intent = InteractionIntent(
        action="answer",
        domain="research",
        retrieval_scopes=["publications", "profile"],
        exclude_scopes=["courseware"],
        decision_mode="direct_answer",
        confidence=0.94,
    )

    filtered = support._filter_knowledge_hits_by_intent(
        [
            KnowledgeSearchHit(
                document_id="irrelevant-profile-pdf",
                title="2026 parallel distributed state management survey（第16部分）",
                excerpt="References and bibliography for distributed state management.",
                score=515.0,
                tags=["homepage", "profile", "attachment", "pdf"],
                source_name="research-paper-part-16",
            ),
            KnowledgeSearchHit(
                document_id="tensor-parallel-guide",
                title="大模型张量并行入门",
                excerpt="张量并行把矩阵按维度切分到多个设备上计算，再聚合结果。",
                score=18.0,
                tags=["research", "publication"],
                source_name="public-guide",
            ),
        ],
        intent,
        question="请用一个简单例子解释大模型推理中的张量并行，控制在150字以内。",
    )

    assert [hit.document_id for hit in filtered] == ["tensor-parallel-guide"]


def test_service_prompt_adds_meeting_preparation_checklist_guidance(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": "科研指导",
                "question": "和老师约时间前，我应该先准备什么？",
            },
        )(),
        knowledge_hits=[],
        interaction_intent=InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.95,
        ),
    )

    assert (
        "agenda, current blocker, draft or progress summary, and 2-3 concrete questions"
        in prompt
    )
    assert (
        "Do not ask for time slots unless the student explicitly asks to book a meeting."
        in prompt
    )


def test_mixed_joining_question_repeats_owner_facts_next_to_question(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    question = (
        "张老师您好，请介绍一下您目前主要研究什么，并说明学生如果想加入课题组"
        "应该提前准备什么？"
    )
    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": "科研指导",
                "question": question,
                "visitor_profile": "general_visitor",
                "attachments": [],
                "deep_thinking": False,
                "deep_thinking_explicit": False,
            },
        )(),
        knowledge_hits=[
            KnowledgeSearchHit(
                document_id="owner-current-focus",
                title="公开资料精选｜当前研究主线",
                excerpt=(
                    "当前工作主要围绕大模型推理引擎、推理服务系统与记忆智能体"
                    "中间件展开。"
                ),
                score=77.0,
                tags=["profile", "research-agenda"],
                source_name="public-profile:current-focus",
            )
        ],
        interaction_intent=InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.9,
        ),
    )

    grounding_position = prompt.index("Mandatory owner-fact grounding")
    question_position = prompt.index("Current user question")
    assert grounding_position < question_position
    assert "大模型推理引擎、推理服务系统与记忆智能体中间件" in prompt
    assert "do not substitute a plausible unrelated field" in prompt


def test_service_prompt_adds_project_scoping_guidance(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": "科研指导",
                "question": "老师，我现在想做一个跟推理系统有关的项目。如果题目太大，您一般会建议怎么收窄？",
            },
        )(),
        knowledge_hits=[],
        interaction_intent=InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.95,
        ),
    )

    assert "first pin down one core problem" in prompt
    assert "choose one setting or artifact" in prompt


def test_service_prompt_adds_draft_feedback_guidance(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": "科研指导",
                "question": "老师，我下周想带一个初稿来请您看。在您看之前，我自己最好先整理哪些信息，能让反馈更集中？",
            },
        )(),
        knowledge_hits=[],
        interaction_intent=InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.95,
        ),
    )

    assert "current goal, current version or evidence, main blocker" in prompt
    assert "Do not turn this into scheduling." in prompt


def test_vamos_question_set_prioritizes_vamos_research_materials(tmp_path: Path) -> None:
    backends = [b for b in available_knowledge_backends() if b in ("local", "sagevdb")]

    for backend_name in backends:
        settings = AppSettings(
            knowledge_base_dir=tmp_path / f"vamos-{backend_name}",
            knowledge_backend="sagevdb" if backend_name == "sagevdb" else "local",
            sagevdb_embedding_backend="hash",
            sagevdb_dimension=128,
        )
        store = LocalKnowledgeStore(settings)

        store.add_document(
            KnowledgeDocumentCreate(
                title="Current Research | 2026 CCF-Ant Proposal (VAMOS)",
                content=(
                    "VAMOS 聚焦异构集群下显存感知和 SLO-aware 推理服务。"
                    "核心目标是通过资源编排与请求调度提升稳定吞吐与时延表现。"
                ),
                tags=["research", "agenda", "vamos", "proposal", "slo-aware"],
                source_name="workspace/vamos/proposal",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="课件正文｜研究生论文写作课程材料｜第 7 讲 发表高水平论文",
                content="本讲关注论文选题、贡献表达与投稿准备。",
                tags=["homepage", "teaching", "courseware", "lecture"],
                source_name="homepage:contents/teaching/graduate-paper-writing-course.md#lecture-7",
            )
        )
        store.add_document(
            KnowledgeDocumentCreate(
                title="主页资料｜研究板块",
                content="研究方向包括大模型推理基础设施与智能系统。",
                tags=["homepage", "profile"],
                source_name="homepage:contents/home.md#research",
            )
        )

        question_set = [
            "VAMOS 这个方向主要解决什么问题？",
            "CCF-Ant proposal 里提到的 SLO-aware 推理服务是什么意思？",
            "异构集群与显存感知调度这条研究线的核心目标是什么？",
        ]

        for question in question_set:
            hits = store.search(question, top_k=3, visitor_profile="lab_member")
            assert hits
            assert hits[0].source_name == "workspace/vamos/proposal"
            assert "vamos" in hits[0].title.lower() or "slo" in hits[0].excerpt.lower()


def test_service_prompt_adds_teaching_question_logistics_guidance(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": "LaMP personalization benchmark",
                "question": "结合我的情况，我下次问课上问题时应该先整理哪几类信息？作业里端到端性能分析不清楚，而且问题比较碎。",
            },
        )(),
        knowledge_hits=[],
        interaction_intent=InteractionIntent(
            action="answer",
            domain="teaching",
            retrieval_scopes=["courseware"],
            exclude_scopes=["publications"],
            confidence=0.95,
        ),
    )

    assert "the exact problem, the observed phenomenon or performance result" in prompt
    assert "Do not answer with another clarification question" in prompt
    assert "end-to-end performance issue" in prompt
    assert "questions feel fragmented" in prompt
    assert "must explicitly reflect the student's stated pain points" in prompt
    assert "Reuse the student's own wording" in prompt


def test_service_prompt_adds_publication_entry_guidance(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": "科研指导",
                "question": "如果我想从您的研究里先挑一个主题开始读，哪类会更适合我？我想先建立主线理解。",
            },
        )(),
        knowledge_hits=[],
        interaction_intent=InteractionIntent(
            action="answer",
            domain="research",
            retrieval_scopes=["publications", "profile"],
            exclude_scopes=["courseware"],
            confidence=0.95,
        ),
    )

    assert "recommend one clear topic or research main line to start from" in prompt
    assert "use wording such as '主题', '主线', or '切入'" in prompt


def test_service_prompt_adds_follow_up_next_step_guidance(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": "科研指导",
                "question": "上次已被建议按主题整理问题，我现在已经整理成三类。按我现在的进展，下一步更适合先发邮件还是继续补实验？",
            },
        )(),
        knowledge_hits=[],
        interaction_intent=InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.95,
        ),
    )

    assert (
        "State the recommendation explicitly with wording like '下一步可以先...'"
        in prompt
    )
    assert "connect it to the student's current progress" in prompt
    assert "既然你已经整理成三类" in prompt


def test_service_prompt_adds_course_research_boundary_guidance(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    prompt = service._build_student_prompt(
        request=type(
            "Request",
            (),
            {
                "student_name": "Alice",
                "course_context": "科研指导",
                "question": "老师，我既想问课程作业，也想顺便聊下研究。这种情况您一般建议一次都问完，还是分开准备？",
            },
        )(),
        knowledge_hits=[],
        interaction_intent=InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.95,
        ),
    )

    assert "mixed course-and-research questions" in prompt
    assert "should be prepared separately" in prompt
    assert "Use wording such as '分开', '分别', '课程', '研究', and '准备'." in prompt


def test_synonym_expansion_recovers_informal_kv_cache_query(tmp_path: Path) -> None:
    """When a student types 'KV部分复用' (informal), synonym expansion should
    match documents that use the canonical 'KV Cache' terminology.
    """
    for backend_name in available_knowledge_backends():
        if backend_name == "sagevdb":
            continue  # only local + neuromem exercised here
        settings = AppSettings(
            knowledge_base_dir=tmp_path / f"synonym-kv-{backend_name}",
            knowledge_backend="neuromem" if backend_name == "neuromem" else "local",
        )
        store = LocalKnowledgeStore(settings)
        store.add_document(
            KnowledgeDocumentCreate(
                title="大模型推理基础设施｜KV Cache管理与复用策略",
                content=(
                    "KV Cache是大模型推理中的核心技术，通过缓存键值对避免重复计算。"
                    "部分复用（partial reuse）策略允许不同请求共享前缀KV Cache。"
                ),
                tags=["teaching", "courseware", "lecture", "course:llm-inference"],
                source_name="homepage:contents/teaching/intro-to-llm-inference-engines.md",
            )
        )

        # The informal phrasing that previously caused retrieval failure.
        hits = store.search("KV部分复用的核心概念是什么？", top_k=3)
        assert len(hits) > 0, (
            f"[{backend_name}] Synonym expansion should recover KV Cache "
            "documents for the informal query 'KV部分复用'"
        )
        assert any("KV Cache" in hit.title for hit in hits)


def test_expand_query_synonyms_is_idempotent() -> None:
    """Calling _expand_query_synonyms twice should not double-expand."""
    from sage_faculty_twin.knowledge_base import _expand_query_synonyms

    once = _expand_query_synonyms("KV部分复用")
    twice = _expand_query_synonyms(once)
    # The second call should produce the same result because all terms are
    # already present after the first expansion.
    assert once == twice


def test_expand_query_synonyms_leaves_unrelated_queries_unchanged() -> None:
    """Queries that don't match any synonym group should be returned as-is."""
    from sage_faculty_twin.knowledge_base import _expand_query_synonyms

    query = "明天下午三点的会议"
    assert _expand_query_synonyms(query) == query
