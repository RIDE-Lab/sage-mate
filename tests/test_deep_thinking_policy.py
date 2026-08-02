from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, InteractionIntent, KnowledgeSearchHit
from sage_faculty_twin.service import (
    ChatWorkflowContext,
    FacultyTwinWorkflowSupport,
    _answer_does_not_complete_requested_task,
    _answer_is_irrelevant_to_question,
    _answer_language_mismatches_question,
    _strip_internal_thinking_content,
)


def _build_context(*, deep_thinking: bool = True, deep_thinking_explicit: bool = False) -> ChatWorkflowContext:
    return ChatWorkflowContext(
        request=ChatRequest(
            student_name="Alice",
            question="请给我解释一下批处理调度。",
            deep_thinking=deep_thinking,
            deep_thinking_explicit=deep_thinking_explicit,
        ),
        conversation_id="conv-deep-thinking",
        owner_name="Prof. Zhang",
        used_model="Qwen3-32B",
        interaction_intent=InteractionIntent(
            action="answer",
            domain="general",
            decision_mode="direct_answer",
        ),
    )


def test_auto_disable_deep_thinking_for_simple_requests(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)

    assert service._should_enable_deep_thinking(_build_context()) is False


def test_explicit_deep_thinking_overrides_auto_disable(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)

    assert service._should_enable_deep_thinking(
        _build_context(deep_thinking=True, deep_thinking_explicit=True)
    ) is True


def test_explicit_deep_thinking_adds_deep_answer_guidance() -> None:
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)

    guidance = FacultyTwinWorkflowSupport._build_deep_answer_guidance(context.request)

    assert "deeper analysis" in guidance
    assert "<think>" in guidance


def test_strip_internal_thinking_content_removes_think_blocks() -> None:
    answer = "<think>internal chain</think>\n\n最终回答。"

    assert _strip_internal_thinking_content(answer) == "最终回答。"


def test_strip_internal_thinking_content_removes_unclosed_think_blocks() -> None:
    answer = "最终回答。\n\n<think>internal chain"

    assert _strip_internal_thinking_content(answer) == "最终回答。"


def test_strip_internal_thinking_content_keeps_plain_answer() -> None:
    answer = "这是直接给用户的回答。"

    assert _strip_internal_thinking_content(answer) == answer


def test_degenerate_answer_detection_rejects_empty_and_repeated_symbols() -> None:
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("")
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("   ")
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("。")
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("！？……")
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("<think>internal</think>最终回答")
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("*" * 200)
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("-FIRST  `")
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("-FIRST" + "\t " * 200)
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("正常开头" + "F" * 100)
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("不便讨论。" * 80)
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "Response instructions: Respond as the digital twin of the faculty owner."
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "这类内部信息不便在此讨论，请直接联系张老师。"
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer("这部分我需要额外确认。")
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "这部分我需要额外确认。如果您提供更多资料，我可以继续检索相关论文和信息。"
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "经过分析，我决定选择[具体方案]作为研究主线，下一步再说明实施步骤。"
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "很抱歉，我无法提供这个问题的答案。如果您有其他问题，请随时告诉我。"
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "这类问题已超出模型能力范围，建议开启联网检索获取实时参考。"
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "Answer: 这个问题是关于大模型推理服务系统的核心权衡。"
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "实践经验总结的实践经验的实践经验总结"
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "#4. 要点、700 个汉字以内。"
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "Based on the current conversation context, there is no new specific request."
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "医学影像需要保证流程可靠。"
        + "提高经济效益增加经济收益提高经济收益增加经济效益" * 12
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "先给一个判断。" + "补齐这两点，才能提高推进效率。" * 3
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(
        "1. 使用连续批处理提高吞吐。\n2. **温度管理**"
    )


def test_degenerate_answer_detection_keeps_normal_short_and_structured_answers() -> None:
    assert not FacultyTwinWorkflowSupport._is_degenerate_answer("OK")
    assert not FacultyTwinWorkflowSupport._is_degenerate_answer(
        "可以从算子融合、KV Cache 管理、批处理调度和通信重叠几个方向优化。"
    )


def test_chinese_question_rejects_long_english_identity_boilerplate() -> None:
    answer = (
        "My apologies for the roundabout question. My name is Zhang, and I am a "
        "digital assistant based on the faculty knowledge base. Please allow me "
        "to assist you further with questions about my operational limits."
    )

    assert _answer_language_mismatches_question(
        "如果我想快速了解张老师的研究路线，最值得先问哪三个问题？",
        answer,
    )
    assert FacultyTwinWorkflowSupport._is_degenerate_answer(answer)


def test_language_guard_keeps_chinese_answer_with_english_terms() -> None:
    answer = (
        "可以先问三个问题：当前核心研究问题是什么；这条路线如何从早期工作发展而来；"
        "下一步最需要突破的系统瓶颈是什么。涉及 LLM、KV Cache 和 GPU 的术语可以保留英文。"
    )

    assert not _answer_language_mismatches_question(
        "如何快速了解张老师的研究路线？",
        answer,
    )


def test_language_guard_rejects_mostly_english_mixed_answer() -> None:
    answer = (
        "This answer discusses a fictional professor and unrelated conference planning. " * 20
        + "医学影像分割"
    )

    assert _answer_language_mismatches_question("医学影像分割有什么前景？", answer)
    assert _answer_language_mismatches_question(
        "请把上面的优化措施排成三步。",
        "iệt?",
    )


def test_relevance_guard_rejects_domain_free_answers() -> None:
    assert _answer_is_irrelevant_to_question(
        "如何优化大模型在 Ascend NPU 上的推理效率？",
        "请先检查输入格式、数值范围、元素顺序和时间戳。",
    )
    assert _answer_is_irrelevant_to_question(
        "如何优化大模型在 Ascend NPU 上的推理效率？",
        "使用算子融合优化 NPU 推理，并通过 HiPerf 查看 GPU利用率。",
    )
    assert _answer_is_irrelevant_to_question(
        "如何优化大模型在 Ascend NPU 上的推理效率？",
        "对 NPU 推理使用量化感知训练，并配合 NCCL 优化跨卡通信。",
    )
    assert _answer_is_irrelevant_to_question(
        "前文讨论 Ascend NPU 大模型推理。当前问题：请排成三步。",
        "第三步优化并行通信，提高模型训练速度。",
    )
    assert _answer_is_irrelevant_to_question(
        "如何优化大模型在 Ascend NPU 上的推理效率？",
        "重点优化 NPU 推理的 KV Cache、内存生命周期和缓存碎片。",
    )
    assert _answer_is_irrelevant_to_question(
        "为什么首 token 延迟和吞吐量往往互相冲突？",
        "分析时应保持客观和中立，并尊重参与者。",
    )
    assert not _answer_is_irrelevant_to_question(
        "为什么首 token 延迟和吞吐量往往互相冲突？",
        "首 token 延迟偏向小批次快速调度，吞吐则偏向大批次提高设备利用率。",
    )


def test_task_completion_guard_rejects_three_question_refusal() -> None:
    answer = (
        "这部分我需要额外确认。如果您想快速了解张老师的研究路线，"
        "可以开启联网搜索获取实时参考资料。"
    )

    assert _answer_does_not_complete_requested_task(
        "如果我想快速了解张老师的研究路线，最值得先问哪三个问题？",
        answer,
    )
    assert _answer_does_not_complete_requested_task(
        "请用三个要点解释召回率和回答准确性的关系。",
        "召回率和准确性需要结合具体系统综合分析。",
    )


def test_task_completion_guard_keeps_three_numbered_questions() -> None:
    answer = "1. 当前核心问题是什么？\n2. 路线如何发展？\n3. 下一步瓶颈是什么？"

    assert not _answer_does_not_complete_requested_task(
        "最值得先问哪三个问题？",
        answer,
    )


def test_task_completion_guard_rejects_generic_guidance_refusal() -> None:
    assert _answer_does_not_complete_requested_task(
        "如何优化大模型在 Ascend NPU 上的推理效率？",
        "这部分我需要额外确认，请直接联系张老师。",
    )
    assert _answer_does_not_complete_requested_task(
        "请把上面的优化措施按实施优先级排成三步。",
        "由于您未提供具体的优化措施，我无法直接进行排序，请先提供相关内容。",
    )
    assert _answer_does_not_complete_requested_task(
        "这周如果想提高推进效率，我最该补哪块背景或工具链？",
        "背景：科研指导\n\n问题：这周如果想提高推进效率，我最该补哪块背景或工具链？",
    )


def test_compact_general_answer_is_used_without_grounding(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context()
    context.request.question = "如何优化大模型在 Ascend NPU 上的推理效率？"

    assert service._should_use_compact_general_answer(context)
    assert service._build_llm_serving_policy_context(context)["max_tokens"] == 512
    compact_prompt = service._build_compact_answer_system_prompt(context.request.question)
    assert "KV Cache" in compact_prompt
    assert "不要把训练优化写成推理优化" in compact_prompt
    assert "NEVER fabricate or invent any academic reference" in compact_prompt
    assert "严格遵循用户指定的" in compact_prompt
    assert "三条简短依据" not in compact_prompt
    assert service._should_use_curated_technical_guidance(context.request.question)
    assert service._should_use_curated_technical_guidance(
        "为什么首 token 延迟和吞吐量往往互相冲突？"
    )


def test_tensor_parallel_explanation_uses_safe_compact_prompt(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context()
    context.request.question = "请用一个简单例子解释大模型推理中的张量并行，控制在150字以内。"

    assert service._should_use_compact_general_answer(context)
    compact_prompt = service._build_compact_answer_system_prompt(context.request.question)
    assert "提供简单具体的例子" in compact_prompt
    assert "内容、格式和长度" in compact_prompt
    assert "When no source context is supplied" in compact_prompt
    assert "推理阶段如何把同一层的矩阵计算切到多个设备" in compact_prompt
    assert "不要改写成训练或数据并行示例" in compact_prompt


def test_general_technical_fast_path_preempts_llm_handoff_classification(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context()
    context.request.question = "为什么大模型推理中首 token 延迟和吞吐量往往互相冲突？"

    intent = service._build_fast_path_interaction_intent(context)

    assert intent is not None
    assert intent.action == "answer"
    assert intent.domain == "research"
    assert intent.decision_mode == "direct_answer"


def test_curated_deep_guidance_covers_common_research_decisions() -> None:
    questions = (
        "这周如果想提高推进效率，我最该补哪块背景或工具链？",
        "我的实验结果波动很大，这周应该先补统计分析、性能 profiling，还是实验管理工具？",
        "我在推理引擎和推理服务系统之间摇摆，如何判断哪个更适合作为研究主线？",
        "如果目标是降低 TTFT，我该如何安排本周的文献、实验和实现优先级？",
    )

    assert all(
        FacultyTwinWorkflowSupport._should_use_curated_deep_guidance(question)
        for question in questions
    )
    assert not FacultyTwinWorkflowSupport._should_use_curated_deep_guidance(
        "请解释什么是连续批处理。"
    )


def test_ascend_follow_up_uses_fast_path_from_session_context(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context()
    context.request.question = "请把上面的优化措施按实施优先级排成三步。"
    context.recent_session_context = "前面讨论了如何优化 Ascend NPU 大模型推理效率。"

    intent = service._build_fast_path_interaction_intent(context)

    assert intent is not None
    assert intent.action == "answer"
    assert intent.domain == "research"


def test_general_question_uses_fast_path_without_llm_classification(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context()
    context.request.question = "请解释检索增强生成中召回率和准确性的关系。"

    intent = service._build_fast_path_interaction_intent(context)

    assert intent is not None
    assert intent.action == "answer"
    assert intent.domain == "general"
    assert intent.decision_mode == "direct_answer"


def test_general_follow_up_uses_fast_path_from_session_context(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context()
    context.request.question = "请把上面的结论再压缩成两点。"
    context.recent_session_context = "前面讨论了检索增强生成的召回率和准确性。"

    intent = service._build_fast_path_interaction_intent(context)

    assert intent is not None
    assert intent.action == "answer"
    assert intent.domain == "general"


def test_medical_segmentation_is_a_general_technical_question(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)

    question = "深度学习图像分割在医学影像中的应用前景如何？"
    guidance = service._build_general_technical_response_guidance(
        question,
        InteractionIntent(action="answer", domain="research"),
    )

    assert service._looks_like_general_technical_question(question)
    assert "cross-center generalization" in guidance
    assert "clinical validation" in guidance


def test_general_question_ignores_incidental_knowledge_hits(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context()
    context.request.question = "如何优化大模型推理？"
    context.knowledge_hits = [object()]  # type: ignore[list-item]

    assert service._should_use_compact_general_answer(context)


def test_general_question_ignores_incidental_memory_hits(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)
    context.request.question = "如何判断哪个系统方向更适合作为研究主线？"
    context.memory_hits = [object()]  # type: ignore[list-item]

    assert service._should_use_compact_general_answer(context)


def test_faculty_specific_question_keeps_knowledge_hits_on_full_path(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context()
    context.request.question = "张老师的论文主要研究哪些推理优化问题？"
    context.knowledge_hits = [object()]  # type: ignore[list-item]

    assert not service._should_use_compact_general_answer(context)


def test_ungrounded_general_answer_uses_compact_path(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context()
    context.request.question = "请比较两个常见方法的优缺点。"

    assert service._should_use_compact_general_answer(context)


def test_explicit_deep_thinking_uses_compact_path_without_grounding(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)
    context.request.question = "请深入比较两个常见方法的优缺点。"

    assert service._should_use_compact_general_answer(context)


def test_explicit_deep_thinking_keeps_grounded_answer_on_full_path(tmp_path: Path) -> None:
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)
    context.request.question = "请深入比较张老师的两个研究方向。"
    context.knowledge_hits = [object()]  # type: ignore[list-item]

    assert not service._should_use_compact_general_answer(context)


def test_compact_retry_uses_bounded_output_budget(tmp_path: Path) -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.max_tokens: list[int | None] = []

        def answer_question_sync(self, *args: object, **kwargs: object) -> str:
            self.max_tokens.append(kwargs.get("max_tokens"))  # type: ignore[arg-type]
            return "1. 建立基线。\n2. 定位瓶颈。\n3. 优化执行路径。\n4. 做消融验证。"

    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    service._llm_client = FakeLlmClient()
    context = _build_context()

    answer = service._retry_answer_with_compact_prompt(context)

    assert answer.startswith("1. 建立基线")
    assert service._llm_client.max_tokens == [384]


def test_owner_grounded_retry_keeps_retrieved_profile_facts(tmp_path: Path) -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.user_prompts: list[str] = []

        def answer_question_sync(
            self,
            _system_prompt: str,
            user_prompt: str,
            **_kwargs: object,
        ) -> str:
            self.user_prompts.append(user_prompt)
            return (
                "当前工作主要围绕大模型推理引擎、推理服务系统与记忆智能体中间件展开。"
                "加入前建议准备简历、项目代码和一两个具体研究问题。"
            )

    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    service._llm_client = FakeLlmClient()
    context = _build_context()
    context.request.question = "张老师目前主要研究什么？加入课题组前应该准备什么？"
    context.knowledge_hits = [
        KnowledgeSearchHit(
            document_id="owner-focus",
            title="公开资料精选｜当前研究主线",
            excerpt="当前工作主要围绕大模型推理引擎、推理服务系统与记忆智能体中间件展开。",
            score=77.0,
            tags=["profile", "research-agenda"],
            source_name="public-profile:current-focus",
        )
    ]

    answer = service._retry_answer_with_compact_prompt(context)

    assert "大模型推理引擎" in answer
    assert "Mandatory owner-fact grounding" in service._llm_client.user_prompts[0]
    assert "记忆智能体中间件" in service._llm_client.user_prompts[0]


def test_owner_fallback_extracts_research_summary_from_retrieved_hit(tmp_path: Path) -> None:
    context = _build_context()
    context.request.question = "张老师目前主要研究什么？加入课题组前应该准备什么？"
    context.knowledge_hits = [
        KnowledgeSearchHit(
            document_id="portable-owner-focus",
            title="公开资料精选｜当前研究主线",
            excerpt="当前工作主要围绕可移植系统甲、服务系统乙与中间件丙展开。",
            score=77.0,
            tags=["profile", "research-agenda"],
            source_name="public-profile:current-focus",
        )
    ]

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "可移植系统甲、服务系统乙与中间件丙" in answer
    assert "项目/代码证据" in answer


def test_explicit_deep_retry_regenerates_instead_of_using_generic_template(
    tmp_path: Path,
) -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        def answer_question_sync(
            self,
            system_prompt: str,
            user_prompt: str,
            **kwargs: object,
        ) -> str:
            self.calls.append((system_prompt, user_prompt, kwargs))
            return (
                "先补最常阻塞推进的工具链，而不是泛读背景。复盘最近三次卡点：如果实验不可复现，"
                "先完善配置、版本和指标记录；如果性能定位靠猜，先补 profiling；如果研究问题边界"
                "不清，再用代表论文、baseline 和未解缺口建立领域地图。本周只选出现次数最多的一类，"
                "设一个可验收结果，例如复现实验一键运行或定位出最大耗时环节。"
            )

    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    service._llm_client = FakeLlmClient()
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)
    context.request.question = "这周如果想提高推进效率，我最该补哪块背景或工具链？"
    context.request.course_context = "科研指导"

    answer = service._retry_answer_with_compact_prompt(context)

    assert "先补最常阻塞推进的工具链" in answer
    assert "背景、目标、约束和评估指标" not in answer
    assert len(service._llm_client.calls) == 1
    system_prompt, user_prompt, kwargs = service._llm_client.calls[0]
    assert "严格遵循用户指定的" in system_prompt
    assert "NEVER fabricate or invent any academic reference" in system_prompt
    assert "先明确判断" not in system_prompt
    assert "背景：科研指导" in user_prompt
    assert kwargs["max_tokens"] == 768
    assert kwargs["temperature"] == 0.2
    assert kwargs["enable_thinking"] is False


def test_deterministic_fallback_answer_handles_ascend_question() -> None:
    context = _build_context()
    context.request.question = "如何优化大模型在Ascend NPU上的推理效率？"

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "Ascend NPU" in answer
    assert "KV Cache" in answer
    assert "吞吐" in answer
    assert not FacultyTwinWorkflowSupport._is_degenerate_answer(answer)


def test_deterministic_fallback_answer_handles_ascend_priority_follow_up() -> None:
    context = _build_context()
    context.request.question = "请把上面的优化措施按实施优先级排成三步。"
    context.recent_session_context = "前面讨论了 Ascend NPU 大模型推理优化。"

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "1. 先建立可复现基线" in answer
    assert "2. 再优化单卡执行与内存" in answer
    assert "3. 最后扩展并发与多卡" in answer


def test_deterministic_fallback_answer_handles_medical_segmentation() -> None:
    context = _build_context()
    context.request.question = "深度学习图像分割在医学影像中的应用前景如何？"

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "U-Net" in answer
    assert "跨中心" in answer
    assert "临床验证" in answer


def test_deterministic_fallback_answer_handles_three_question_guidance() -> None:
    context = _build_context()
    context.request.question = "如果我想快速了解张老师的研究路线，最值得先问哪三个问题？"

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "三个问题" in answer
    assert "现在最核心的问题" in answer
    assert not FacultyTwinWorkflowSupport._is_degenerate_answer(answer)


def test_deterministic_fallback_answer_handles_first_token_throughput_tradeoff() -> None:
    context = _build_context()
    context.request.question = "为什么大模型推理服务同时优化首 token 延迟和吞吐会有冲突？"

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "首 token 延迟" in answer
    assert "吞吐" in answer
    assert "调度目标不同" in answer
    assert not FacultyTwinWorkflowSupport._is_degenerate_answer(answer)


def test_deterministic_fallback_handles_medical_priority_followup() -> None:
    context = _build_context()
    context.request.question = "请把上面的核心挑战归纳成三个优先级，并说明理由。"
    context.recent_session_context = "上一轮讨论了医学影像分割落地的核心挑战。"

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "临床安全" in answer
    assert "跨中心泛化" in answer
    assert "推理效率" in answer


def test_deterministic_fallback_handles_weekly_progress_question() -> None:
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)
    context.request.question = "这周如果想提高推进效率，我最该补哪块背景或工具链？"

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "最近一周反复卡住" in answer
    assert "profiling" in answer
    assert "背景、目标、约束和评估指标" not in answer


def test_deterministic_fallback_handles_research_direction_choice() -> None:
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)
    context.request.question = (
        "我在推理引擎和推理服务系统之间摇摆，如何判断哪个更适合作为研究主线？"
    )

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "真实请求、调度日志和多租户场景" in answer
    assert "两周探针实验" in answer
    assert "[具体方案]" not in answer


def test_deterministic_fallback_handles_ttft_weekly_priorities() -> None:
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)
    context.request.question = "如果目标是降低 TTFT，我该如何安排本周的文献、实验和实现优先级？"

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "先实验定位" in answer
    assert "TTFT P95" in answer


def test_deterministic_fallback_handles_unstable_experiment_results() -> None:
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)
    context.request.question = (
        "我的实验结果波动很大，这周应该先补统计分析、性能 profiling，还是实验管理工具？"
    )

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "先补实验管理和可复现性" in answer
    assert "独立重复至少 5 次" in answer


def test_deterministic_fallback_handles_research_innovation_question() -> None:
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)
    context.request.question = (
        "设计一个新的推理调度研究问题时，怎样判断它是否具有足够的学术创新性？"
    )

    answer = FacultyTwinWorkflowSupport._build_deterministic_fallback_answer(context)

    assert "系统性缺口" in answer
    assert "related-work 矩阵" in answer
    assert "背景、目标、约束和评估指标" not in answer


def test_hosted_web_deep_call_does_not_enable_engine_thinking(tmp_path: Path) -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def answer_question_sync(
            self,
            system_prompt: str,
            user_prompt: str,
            *,
            enable_thinking: bool = True,
            thinking_token_budget: int | None = None,
            **kwargs: object,
        ) -> str:
            kwargs["enable_thinking"] = enable_thinking
            if thinking_token_budget is not None:
                kwargs["thinking_token_budget"] = thinking_token_budget
            self.kwargs = kwargs
            return "OK"

    fake_llm = FakeLlmClient()
    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    service._llm_client = fake_llm
    context = _build_context(deep_thinking=True, deep_thinking_explicit=True)

    answer = service._call_answer_question_sync(
        "system",
        "user",
        context=context,
        enable_thinking=True,
    )

    assert answer == "OK"
    assert fake_llm.kwargs is not None
    assert fake_llm.kwargs["enable_thinking"] is False
    assert "thinking_token_budget" not in fake_llm.kwargs


def test_compact_call_can_disable_vllm_reuse_hints(tmp_path: Path) -> None:
    class FakeLlmClient:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def answer_question_sync(
            self,
            system_prompt: str,
            user_prompt: str,
            *,
            use_reuse_hints: bool = True,
            **kwargs: object,
        ) -> str:
            self.kwargs = {"use_reuse_hints": use_reuse_hints, **kwargs}
            return "OK"

    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    service._llm_client = FakeLlmClient()

    service._call_answer_question_sync(
        "system",
        "user",
        context=_build_context(),
        enable_thinking=False,
        use_reuse_hints=False,
    )

    assert service._llm_client.kwargs["use_reuse_hints"] is False


def test_glm4_answer_path_disables_unreliable_streaming(tmp_path: Path) -> None:
    class FakeLlmClient:
        model_name = "zai-org/GLM-4-32B-0414"

        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def answer_question_sync(
            self,
            system_prompt: str,
            user_prompt: str,
            *,
            token_callback: object | None = None,
            **kwargs: object,
        ) -> str:
            self.kwargs = {"token_callback": token_callback, **kwargs}
            return "OK"

    service = object.__new__(FacultyTwinWorkflowSupport)
    service._settings = AppSettings(knowledge_base_dir=tmp_path)
    service._llm_client = FakeLlmClient()

    service._call_answer_question_sync(
        "system",
        "user",
        context=_build_context(),
        token_callback=lambda _: None,
        enable_thinking=False,
    )

    assert service._llm_client.kwargs["token_callback"] is None
