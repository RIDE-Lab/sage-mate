"""First-turn regressions for opportunity-oriented scientific review."""

from __future__ import annotations

import pytest

from sage_faculty_twin.chat_contracts import ChatIntake, InteractionDecision
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, InteractionIntent
from sage_faculty_twin.research_review import (
    build_research_review_guidance,
    is_research_review_request,
    research_review_answer_issues,
)
from sage_faculty_twin.service import (
    ChatWorkflowContext,
    DigitalTwinService,
    FacultyTwinWorkflowSupport,
    _answer_does_not_complete_requested_task,
)


FIRST_TURN_SCENARIOS = (
    "请评价这个研究：通过跨虚拟请求的稳定前缀复用降低重复 prefill，已有运行结果，但本轮没有附证据卡，仓库也还没有独立核验。",
    "请评审这个算子论文方向：针对重要应用中的常见 shape 设计新机制，应该怎样判断贡献？",
    "请评价是否继续研究：旧机制在当前工作负载得到负结果，能否复用已有实现换一个新视角？",
    "请评价虚拟请求之间复用 KV 或 output 的研究机会，并检查机制可行性与运行成本。",
)


@pytest.mark.parametrize("question", FIRST_TURN_SCENARIOS)
def test_first_turn_research_reviews_receive_the_full_contract(question: str) -> None:
    guidance = build_research_review_guidance(question, domain="research")

    assert is_research_review_request(question, domain="research")
    assert "研究潜力" in guidance
    assert "已有证据" in guidance
    assert "投稿成熟度" in guidance
    assert "材料未展示" in guidance
    assert "仓库/结果尚未核验" in guidance
    assert "最有价值的研究机会" in guidance
    assert "下一步决定性实验" in guidance
    assert "实现/运行成本" in guidance

    assert "本轮输入保真约束" in guidance


def test_initial_prompt_contains_review_contract_without_a_correction_turn(
    tmp_path,
) -> None:
    settings = AppSettings(_env_file=None, knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    support = service._build_support()
    question = FIRST_TURN_SCENARIOS[0]
    request = ChatRequest(
        student_name="member",
        visitor_profile="lab_member",
        question=question,
    )
    intent = InteractionIntent(
        action="answer",
        domain="research",
        decision_mode="direct_answer",
        confidence=1.0,
    )
    context = ChatWorkflowContext(
        request=request,
        conversation_id="first-turn-review",
        owner_name=settings.owner_name,
        used_model=settings.model_name,
        intake=ChatIntake.from_request(request, conversation_id="first-turn-review"),
        interaction_decision=InteractionDecision(intent=intent, source="test"),
        interaction_intent=intent,
    )

    support.build_prompt(context)

    assert context.system_prompt is not None
    assert "科研评审契约" in context.system_prompt
    assert "没有独立重跑不等于没有实验" in context.system_prompt
    assert "禁止改写成没有实验" in context.system_prompt
    assert context.user_prompt is not None
    assert question in context.user_prompt


def test_review_contract_does_not_turn_owner_fact_lookup_into_a_review() -> None:
    question = "张老师现在的研究方向是什么？"

    assert not is_research_review_request(question, domain="research")
    assert build_research_review_guidance(question, domain="research") == ""


def test_canned_direction_checklist_is_retired() -> None:
    question = "这个候选研究方向是否值得继续？请从 baseline、公平对比和消融来分析。"

    assert not FacultyTwinWorkflowSupport._should_use_curated_direction_evaluation(
        question
    )


def test_lab_member_review_uses_research_fast_intent_on_the_first_turn(tmp_path) -> None:
    settings = AppSettings(_env_file=None, knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    support = service._build_support()
    request = ChatRequest(
        student_name="member",
        visitor_profile="lab_member",
        question=FIRST_TURN_SCENARIOS[0],
    )
    context = ChatWorkflowContext(
        request=request,
        conversation_id="review-intent",
        owner_name=settings.owner_name,
        used_model=settings.model_name,
        intake=ChatIntake.from_request(request, conversation_id="review-intent"),
    )

    intent = support._build_fast_path_interaction_intent(context)

    assert intent is not None
    assert intent.domain == "research"
    assert intent.decision_mode == "advise_only"


def test_review_with_workload_wording_does_not_use_faculty_fact_shortcut(
    tmp_path,
) -> None:
    settings = AppSettings(_env_file=None, knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    support = service._build_support()
    request = ChatRequest(
        student_name="member",
        visitor_profile="lab_member",
        question="请评价是否继续研究：旧机制在当前工作负载得到负结果。",
    )
    intent = InteractionIntent(
        action="answer",
        domain="research",
        decision_mode="advise_only",
        confidence=1.0,
    )
    context = ChatWorkflowContext(
        request=request,
        conversation_id="review-not-fact",
        owner_name=settings.owner_name,
        used_model=settings.model_name,
        intake=ChatIntake.from_request(request, conversation_id="review-not-fact"),
        interaction_decision=InteractionDecision(intent=intent, source="test"),
        interaction_intent=intent,
    )

    assert support._build_grounded_fact_answer(context) is None


@pytest.mark.parametrize(
    ("question", "bad_answer", "expected_issue"),
    (
        (
            FIRST_TURN_SCENARIOS[0],
            "没有证据卡就说明尚未做过实验，这只是纯蓝图。",
            "contradicts_reported_evidence",
        ),
        (
            FIRST_TURN_SCENARIOS[0],
            "缺少证据卡，所以无法给出可靠评价，只能算有想法、无验证。",
            "contradicts_reported_evidence",
        ),
        (
            FIRST_TURN_SCENARIOS[1],
            "这个工作只有一个 shape，因此必须先证明全链路普适收益。",
            "invents_shape_exclusivity",
        ),
        (
            FIRST_TURN_SCENARIOS[2],
            "既然得到负结果，就应停止整个课题。换场景一定成功。",
            "overgeneralizes_negative_result",
        ),
        (
            "请评价这个调度研究方向是否值得继续。",
            "若加速超过 30% 就继续，否则立即停止。",
            "invents_numeric_stop_gate",
        ),
        (
            "请评价这个调度研究方向是否值得继续。",
            "至少三次重复都成功才继续，否则停止。",
            "invents_numeric_stop_gate",
        ),
    ),
)
def test_bad_first_answers_are_rejected_before_delivery(
    question: str,
    bad_answer: str,
    expected_issue: str,
) -> None:
    issues = research_review_answer_issues(question, bad_answer)

    assert expected_issue in issues
    assert _answer_does_not_complete_requested_task(question, bad_answer)


@pytest.mark.parametrize(
    ("question", "answer"),
    (
        (
            FIRST_TURN_SCENARIOS[0],
            "当前判断：研究潜力可判断；已有运行属于作者报告，因材料未展示且仓库未核验，效果待验证；投稿成熟度尚不足。\n"
            "最有价值的研究机会：先定位并复用已有日志与产物，检验收益来自哪个机制。\n"
            "下一步决定性实验：核验同一配置下的原始结果；若可复现则强化机制主张，否则收窄效果边界。",
        ),
        (
            FIRST_TURN_SCENARIOS[1],
            "当前判断：代表性应用可以支持研究潜力，但常见 shape 的覆盖范围未知，投稿证据仍需补齐。\n"
            "最有价值的研究机会：解释该算子机制为何匹配这些 shape，并明确调优基线与收益边界。\n"
            "下一步决定性实验：比较正确性、强调优基线和多类代表性 shape；结果决定贡献是机制发现还是局部实现优化。",
        ),
        (
            FIRST_TURN_SCENARIOS[2],
            "当前判断：负结果只否定旧机制在当前负载下的净收益，不足以否定整个问题。\n"
            "最有价值的研究机会：复用实现和测量资产，研究元数据与复用收益不匹配时的选择机制。\n"
            "下一步决定性实验：固定实现成本，只改变能区分新旧假设的工作负载特征；若开销仍覆盖收益则收缩机制边界，反之形成新问题。",
        ),
        (
            FIRST_TURN_SCENARIOS[3],
            "当前判断：KV 复用要求可证明的 token 前缀与状态兼容，output 复用还要求请求、采样和权限语义等价；潜力存在但证据未知。\n"
            "最有价值的研究机会：把虚拟请求的等价性证明与缓存生命周期绑定，而不是把相似请求直接视为可复用。\n"
            "下一步决定性实验：测量校验、索引、驻留和失效成本是否小于省下的计算，并加入跨租户隔离与错误复用检查；结果决定应复用 KV、output，还是不复用。",
        ),
    ),
)
def test_mechanistic_first_answers_pass_the_epistemic_guard(
    question: str,
    answer: str,
) -> None:
    assert research_review_answer_issues(question, answer) == ()
    assert not _answer_does_not_complete_requested_task(question, answer)


def test_numeric_example_is_allowed_only_when_explicitly_qualified() -> None:
    question = "请评价这个研究方向是否值得继续。"
    answer = (
        "待确认建议：可以先试 30% 作为示例值，最终门槛需根据净收益模型和资源预算确定。"
    )

    assert research_review_answer_issues(question, answer) == ()


def test_turn_fidelity_constraints_are_derived_from_the_current_input() -> None:
    guidance = build_research_review_guidance(
        "请评价常见 shape 的算子机制；已有实验得到负结果，并考虑虚拟请求 KV/output 复用。",
        domain="research",
    )

    assert "禁止改写成没有实验" in guidance
    assert "数量未知的代表性 shape 集合" in guidance
    assert "新视角的效果仍未知" in guidance
    assert "分别分析 KV 状态复用与最终 output 复用" in guidance
    assert "token 前缀、位置、模型/适配器" in guidance
    assert "禁止把激活相似描述为数学安全或无损共享" in guidance
    assert "请求等价证书和成本感知复用路由" in guidance
    assert "若等价性探针需要先完成原本要省掉的计算" in guidance
    assert "不得新增百分比、加速比、样本数或期限" in guidance


def test_missing_evidence_cannot_erase_a_concrete_research_opportunity() -> None:
    question = FIRST_TURN_SCENARIOS[0]
    answer = "已有运行但当前未核验，所以研究机会暂不可识别。"

    assert "collapses_potential_into_evidence" in research_review_answer_issues(
        question, answer
    )


def test_each_numeric_example_requires_its_own_nearby_qualification() -> None:
    question = "请评价这个研究方向是否值得继续。"
    answer = (
        "待确认建议：可以先把 30% 作为示例值，需根据资源预算修订；"
        "如果收益低于 10% 就停止。"
    )

    assert "invents_numeric_stop_gate" in research_review_answer_issues(
        question, answer
    )
