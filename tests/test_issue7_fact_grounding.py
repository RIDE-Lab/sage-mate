from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, InteractionIntent, KnowledgeSearchHit
from sage_faculty_twin.service import ChatWorkflowContext, FacultyTwinWorkflowSupport


def _service(tmp_path: Path) -> FacultyTwinWorkflowSupport:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    return service


def _context(question: str, domain: str, hits: list[KnowledgeSearchHit]) -> ChatWorkflowContext:
    return ChatWorkflowContext(
        request=ChatRequest(student_name="访客", question=question),
        conversation_id="issue7-test",
        owner_name="张书豪",
        used_model="test-model",
        interaction_intent=InteractionIntent(action="answer", domain=domain),
        knowledge_hits=hits,
    )


def test_research_fact_answer_reuses_evidence_without_model_expansion(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context(
        "张老师目前主要研究哪些方向？",
        "research",
        [
            KnowledgeSearchHit(
                document_id="research-1",
                title="公开研究主线",
                excerpt="当前工作主要围绕大模型推理引擎、推理服务系统与记忆智能体中间件展开。",
                score=90,
                tags=["profile", "research-agenda", "audience:public"],
                source_name="public-profile:research",
            )
        ],
    )

    answer = service._build_grounded_fact_answer(context)

    assert answer is not None
    assert "大模型推理引擎" in answer
    assert "分布式算法" not in answer
    assert "资料没有明确说明" in answer


def test_course_fact_answer_is_explicitly_unknown_without_course_evidence(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context(
        "大模型推理基础设施课程主要学习什么？",
        "teaching",
        [
            KnowledgeSearchHit(
                document_id="adjacent",
                title="公开研究主线",
                excerpt="当前工作主要围绕大模型推理引擎展开。",
                score=50,
                tags=["profile", "audience:public"],
                source_name="public-profile:research",
            )
        ],
    )

    answer = service._build_grounded_fact_answer(context)

    assert answer is not None
    assert "课程资料不足" in answer
    assert "通用模板" in answer


def test_course_fact_answer_does_not_depend_on_classifier_domain(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context(
        "大模型推理基础设施课程主要学习什么？",
        "general",
        [
            KnowledgeSearchHit(
                document_id="course",
                title="课程资料｜推理入门",
                excerpt="课程围绕 Prefill、Decode、KV Cache 和推理系统 Benchmark 展开。",
                score=80,
                tags=["teaching", "courseware", "audience:public"],
                source_name="public-course:inference",
            )
        ],
    )
    context.request.course_context = "大模型推理基础设施课程"

    answer = service._build_grounded_fact_answer(context)

    assert answer is not None
    assert "Prefill" in answer
    assert "反向传播" not in answer


def test_private_audience_evidence_is_not_used_for_visitor_fact_answer(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context(
        "张老师目前主要研究哪些方向？",
        "research",
        [
            KnowledgeSearchHit(
                document_id="private",
                title="组内研究材料",
                excerpt="未公开合作项目与内部计划。",
                score=99,
                tags=["research", "audience:lab_member"],
                source_name="private-materials:internal",
            )
        ],
    )

    answer = service._build_grounded_fact_answer(context)

    assert answer is not None
    assert "公开资料不足" in answer
    assert "未公开合作项目" not in answer


def test_student_course_fact_uses_fast_teaching_intent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context("大模型推理基础设施课程主要学习什么？", "general", [])
    context.request.visitor_profile = "student"
    context.request.course_context = "大模型推理基础设施课程"

    intent = service._build_fast_path_interaction_intent(context)

    assert intent is not None
    assert intent.domain == "teaching"
    assert intent.confidence == 0.98
