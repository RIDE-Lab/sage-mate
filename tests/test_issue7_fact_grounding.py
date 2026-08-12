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


def test_empty_fact_route_is_rendered_as_unknown_not_http500(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._trace_callback = None
    context = _context("这门课的实验分组规则是什么？", "teaching", [])
    context.request.course_context = "未收录课程"

    response = service.render_chat_response(context)

    assert "资料不足" in response.answer
    assert response.answer_basis == []


def test_stack_relation_answer_is_chinese_and_evidence_bound(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context(
        "SAGE、NeuroMem、SageVDB 和 vLLM-HUST 在系统里分别负责什么？",
        "research",
        [
            KnowledgeSearchHit(
                document_id="sage-paper",
                title="论文提炼｜SAGE Framework",
                excerpt="SAGE organizes retrieval, memory, tools, and reasoning workflows.",
                score=90,
                tags=["research", "publication", "audience:public"],
                source_name="public-profile:sage",
            )
        ],
    )

    answer = service._build_grounded_fact_answer(context)

    assert answer is not None
    assert "上下层协作关系" in answer
    assert "Support" in answer


def test_system_project_list_uses_canonical_overview_and_rejects_paper_noise(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._knowledge_store = type("Store", (), {
        "list_documents": lambda self: [
            type("Record", (), {
                "document_id": "systems",
                "title": "主页资料｜当前系统建设",
                "content": "- SAGE：大模型推理服务系统\n- Neuromem：记忆智能体中间件\n- vLLM-HUST：推理引擎底座",
                "tags": ["homepage", "profile", "research", "audience:public"],
                "source_name": "homepage:contents/home.md#当前系统建设",
                "metadata": {},
            })(),
            type("Record", (), {
                "document_id": "paper",
                "title": "论文 PDF",
                "content": "图像识别和自然语言处理工具包",
                "tags": ["publication", "pdf", "audience:public"],
                "source_name": "homepage:paper.pdf",
                "metadata": {},
            })(),
        ]
    })()
    context = _context(
        "课题组目前有哪些系统或开源项目？",
        "research",
        [
            KnowledgeSearchHit(
                document_id="paper",
                title="论文 PDF",
                excerpt="图像识别和自然语言处理工具包",
                score=99,
                tags=["publication", "pdf", "audience:public"],
                source_name="homepage:paper.pdf",
            )
        ],
    )

    answer = service._build_grounded_fact_answer(context)

    assert answer is not None
    assert "SAGE" in answer and "Neuromem" in answer and "vLLM-HUST" in answer
    assert "图像识别" not in answer
    assert [hit.title for hit in context.knowledge_hits] == ["主页资料｜当前系统建设"]


def test_explicit_shuhao_alias_is_treated_as_owner_identity(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context(
        "张书豪老师是谁？",
        "general",
        [
            KnowledgeSearchHit(
                document_id="wrong-teacher",
                title="其他教师资料",
                excerpt="北京大学教师，研究图像识别。",
                score=99,
                tags=["profile", "audience:public"],
                source_name="external:wrong-teacher",
            ),
            KnowledgeSearchHit(
                document_id="owner",
                title="主页资料｜张书豪",
                excerpt="张书豪，华中科技大学计算机学院教授，研究大模型推理系统。",
                score=80,
                tags=["homepage", "profile", "audience:public"],
                source_name="homepage:contents/home.md#张书豪",
            ),
        ],
    )

    answer = service._build_grounded_fact_answer(context)

    assert answer is not None
    assert "华中科技大学" in answer
    assert "北京大学" not in answer


def test_collaboration_question_has_actionable_next_steps(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context(
        "我们想和张老师合作，应该如何推进？",
        "advising",
        [
            KnowledgeSearchHit(
                document_id="profile",
                title="主页资料｜研究板块",
                excerpt="研究聚焦推理系统、状态管理和运行时优化。",
                score=80,
                tags=["profile", "research", "audience:public"],
                source_name="public-profile:research",
            )
        ],
    )

    answer = service._build_grounded_fact_answer(context)

    assert answer is not None
    assert "一页纸" in answer
    assert "分工" in answer
    assert "正式确认" in answer
