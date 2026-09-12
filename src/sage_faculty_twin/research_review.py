"""Scientific-review behavior shared by SAGE Mate answer paths.

This module governs epistemic judgment and review structure.  It deliberately
does not contain faculty facts or private methodology material; access to
those sources remains the responsibility of the knowledge-retrieval layer.
"""

from __future__ import annotations

import re


_REVIEW_ACTION_MARKERS = (
    "评价",
    "评审",
    "审查",
    "判断",
    "值得",
    "继续做",
    "继续推进",
    "研究问题",
    "研究机会",
    "创新",
    "贡献",
    "投稿",
    "成熟度",
    "有没有价值",
    "是否有价值",
    "该不该",
    "怎么推进",
    "能否作为",
    "负结果",
    "review",
    "research idea",
    "negative result",
)

_RESEARCH_OBJECT_MARKERS = (
    "研究",
    "课题",
    "方向",
    "机制",
    "算子",
    "kernel",
    "benchmark",
    "基准",
    "安全",
    "系统",
    "国产卡",
    "npu",
    "kv",
    "output",
    "复用",
    "想法",
    "idea",
    "方法",
    "方案",
    "论文",
    "实验",
)

_REPORTED_EVIDENCE_MARKERS = (
    "已有运行",
    "已经运行",
    "跑过",
    "测过",
    "已有实验",
    "实验结果",
    "观察到",
    "数据显示",
    "负结果",
    "失败结果",
)

_FALSE_ABSENCE_CLAIMS = (
    "没有任何实验",
    "完全没有实验",
    "尚未做过实验",
    "还没做过实验",
    "只是纯蓝图",
    "纯蓝图",
    "有想法、无验证",
    "有想法无验证",
)

_UNQUALIFIED_NUMERIC_GATE = re.compile(
    r"(?:[<>≥≤]\s*)?\d+(?:\.\d+)?"
    r"(?:\s*(?:到|至|[-—~～])\s*\d+(?:\.\d+)?)?"
    r"\s*(?:%|倍|个|次|天|周|个月)",
    re.IGNORECASE,
)

_UNQUALIFIED_CHINESE_NUMERIC_GATE = re.compile(
    r"(?:(?:至少|不少于|不低于|超过|大于|低于|小于|连续)\s*"
    r"[一二两三四五六七八九十百]+\s*(?:个|次|天|周|个月)"
    r"|[一二两三四五六七八九十百]+\s*(?:次重复|个样本|次实验|个\s*shape))",
    re.IGNORECASE,
)


def _asks_active_request_reuse(compact_text: str) -> bool:
    return (
        "复用" in compact_text
        and ("kv" in compact_text or "output" in compact_text)
        and any(
            marker in compact_text
            for marker in (
                "虚拟请求",
                "主动构造",
                "主动合成",
                "主动生成",
                "主动执行",
                "提前执行",
                "预执行",
            )
        )
    )


def is_research_review_request(question: str, *, domain: str | None = None) -> bool:
    """Return whether the turn asks for scientific judgment, not a fact lookup."""

    lowered = question.lower()
    has_action = any(marker in lowered for marker in _REVIEW_ACTION_MARKERS)
    has_object = any(marker in lowered for marker in _RESEARCH_OBJECT_MARKERS)
    return domain == "research" and has_action and has_object


def build_research_review_guidance(
    question: str,
    *,
    domain: str | None,
) -> str:
    """Build the common first-turn scientific-review contract.

    The contract tells the model how to reason, but does not predetermine the
    verdict.  This keeps early-stage opportunity finding distinct from factual
    verification and paper-readiness assessment.
    """

    if not is_research_review_request(question, domain=domain):
        return ""

    compact_question = re.sub(r"\s+", "", question.lower())
    fidelity_constraints: list[str] = []
    if any(marker in compact_question for marker in _REPORTED_EVIDENCE_MARKERS):
        fidelity_constraints.append(
            "输入已明确报告存在运行或实验结果；只能标为作者已报告但当前材料未展示/尚未独立核验，禁止改写成没有实验"
        )
    if "常见shape" in compact_question or "常见形状" in compact_question:
        fidelity_constraints.append(
            "‘常见 shape’表示数量未知的代表性 shape 集合，禁止改写成单一或只有一个 shape，也不要擅自要求 universal 覆盖"
        )
    if any(
        marker in compact_question
        for marker in ("负结果", "失败结果", "没有收益", "未见收益", "效果为负")
    ):
        fidelity_constraints.append(
            "负结果的有效范围仅限输入给出的旧机制、工作负载和条件；新视角的效果仍未知"
        )
    if _asks_active_request_reuse(compact_question):
        fidelity_constraints.append(
            "保留用户提出的主动构造并执行虚拟请求这一创新核心，并分别分析 KV 状态复用与最终 output 复用，"
            "不能把两者混成同一机制或未经评估就排除 output 路径。先比较主动执行相对被动缓存、按需前缀缓存和"
            "已有前缀预热的新增价值。至少认真评估一条 output 路径：在可证明等价且资源空闲时主动执行规范化请求，"
            "保存完整 output，命中时直接返回；正确性保持的 KV 复用至少要求"
            "token 前缀、位置、模型/适配器及相关执行状态兼容；最终 output 复用要求规范化请求、生成配置、"
            "模型/分词器/模板/系统策略、工具状态、随机性、权限和新鲜度语义满足相应等价条件。语义/激活近似"
            "只能作为允许质量变化的新假设，不能冒充精确复用；"
            "token 不同通常意味着 KV 状态不同，禁止把激活相似描述为数学安全或无损共享。优先研究可审计的"
            "请求等价证书和成本感知复用路由，并把主动请求选择作为待验证机制；强基线至少包括不复用、被动 output memoization、"
            "按需 prefix/KV cache 和已有 prefix warmup。完整净收益须扣除预测、虚拟执行、验证、存储、失效、"
            "误预测与机会成本；若等价性探针需要先完成原本要省掉的计算，也必须计入成本。研究建议必须明确"
            "适用场景、机制输入、产出的可复用 KV 或完整 output、正确性条件、强基线、归因消融和净收益边界"
        )
    if any(marker in compact_question for marker in ("字节减少", "流量减少", "搬运量减少")) and any(
        marker in compact_question for marker in ("耗时增加", "延迟增加", "时间增加", "反而更慢")
    ):
        fidelity_constraints.append(
            "‘传输字节减少但耗时增加’只是联合观察，不是已证实原因。至少列出竞争解释：搬运不在关键路径、"
            "有效带宽下降、计算通信重叠被破坏、同步/启动/排队开销上升、工作负载差异或测量噪声；并给出能"
            "区分它们的测量，如分阶段关键路径、字节数与有效带宽、timeline 重叠/等待、队列与同步事件、"
            "同负载配对重复。禁止直接断言搬运不是瓶颈"
        )
    if not re.search(r"\d", question):
        fidelity_constraints.append(
            "用户没有给出数字目标；答案不得新增百分比、加速比、样本数或期限，改用净收益关系和结果分支"
            "表达判据，不要给示例数字"
        )
    fidelity_note = "；".join(fidelity_constraints)

    return (
        "\n科研评审契约（行为要求，不是可引用资料）：目标是在保持科学标准的同时寻找可验证的研究机会，"
        "形成研究问题，并用实验推进论文。\n"
        "一、分别判断三个维度，不得互相替代：研究潜力看问题重要性、已有方法的关键边界和可能的知识增量；"
        "已有证据看作者报告了什么、当前材料展示了什么、哪些已独立核验，以及证据能否支持因果归因；"
        "投稿成熟度看论证、定位、实现、评测和可复现性是否足以支撑当前投稿主张。证据不足只能使效果或"
        "成熟度待验证，不能自动推出没有研究价值、只是纯蓝图或三个维度都无法判断。\n"
        "二、建立事实状态账本：严格区分‘材料未展示’、‘仓库/结果尚未核验’和‘输入明确说尚未完成’。"
        "没有独立重跑不等于没有实验；已有运行或结果应先索引、定位产物并核验，不能默认要求作者从头重做。"
        "只使用当前输入与可见证据；缺失信息写未知。不得把‘常见 shape’改写成‘只有一个 shape’，也不得"
        "在自我纠错时沿用上一轮回答自行添加的假设。\n"
        "三、按课题类型选择标准：重要且有代表性的单一应用类别可以充分 motivate 研究，并不要求 universal。"
        "算子论文重点检查机制、正确性、代表性 shape、调优基线和收益边界；只有主张全链路收益时，端到端"
        "收益才是相应硬证据；只有主张跨硬件可移植性时，跨硬件实验才是相应硬证据。"
        "工具看可用性与开发/诊断增益，benchmark 看代表性、区分度与可复现性，安全"
        "工作看威胁模型和防护有效性，系统机制看因果路径、开销与边界，不能套用同一门槛。\n"
        "四、负结果只约束已测试的机制、工作负载和条件。先解释最可能的失败机制，再提出一个最大化复用"
        "现有代码、数据和测量资产的新视角，以及能区分新旧假设的最小实验；不得默认终止整个课题，也不得"
        "承诺换场景必然成功。\n"
        "五、判据必须来自净收益模型、资源约束、统计精度或用户明确目标。不得凭空规定命中率、加速比、"
        "样本数或期限；任何示例数字都要标为‘待确认建议’，并说明如何据实确定。\n"
        "六、内部按七个问题组织判断：具体问题、重要性、现有方法的关键局限、机制、可行性、验证、知识增量。"
        "主动找一个最值得尝试的创新方向，不要只罗列缺口或批量要求补 baseline/消融。对于国产卡，先说明"
        "哪些已核实的架构特性会让旧假设失效；平台特异性不自动等于工程贡献，适配成功也不自动等于研究贡献。\n"
        "七、论文与实验可以并行：允许先写清问题、设计和待验证主张，但不得把实验计划当作已完成结果。"
        "技术建议必须交代机制、成立条件、实现/运行成本、主要失败模式，以及验证净收益的实验；不能用术语"
        "堆砌代替推理。观察到相关指标共同变化时先列竞争解释和区分测量，不能把合理解释写成已证实因果。"
        "净收益为正只说明方案在相应条件下可能可行；创新成立还需用机制消融证明收益来自所提机制，并优于"
        "相关强替代方案，但在证据未齐时应保留其探索价值而不是提前否定。\n"
        "默认只输出三个紧凑部分：‘当前判断’（分别概括三维度）、‘最有价值的研究机会’（只选一个）、"
        "‘下一步决定性实验’（说明不同结果会如何改变结论）。每部分只写一个紧凑正文段落，"
        "避免前言、嵌套清单和多组泛化实验。\n"
        f"本轮输入保真约束：{fidelity_note}。回答前重新对照当前用户输入逐项检查，不得沿用模型自行补出的事实。\n"
    )


def research_review_answer_issues(question: str, answer: str | None) -> tuple[str, ...]:
    """Detect only high-confidence first-answer review contradictions.

    This is intentionally narrow.  Nuanced scientific quality belongs in the
    model contract; the runtime guard only rejects statements that directly
    contradict facts in the user's turn or turn a bounded result into an
    unsupported universal verdict.
    """

    if not answer or not is_research_review_request(question, domain="research"):
        return ()

    compact_question = re.sub(r"\s+", "", question.lower())
    compact_answer = re.sub(r"\s+", "", answer.lower())
    issues: list[str] = []

    if any(marker in compact_question for marker in _REPORTED_EVIDENCE_MARKERS) and any(
        marker in compact_answer for marker in _FALSE_ABSENCE_CLAIMS
    ):
        issues.append("contradicts_reported_evidence")
    if any(marker in compact_question for marker in _REPORTED_EVIDENCE_MARKERS) and any(
        marker in compact_answer
        for marker in (
            "研究机会暂不可识别",
            "无法识别研究机会",
            "没有研究潜力",
            "无法给出可靠评价",
            "无法评价研究价值",
        )
    ):
        issues.append("collapses_potential_into_evidence")

    if ("常见shape" in compact_question or "常见形状" in compact_question) and any(
        marker in compact_answer
        for marker in ("只有一个shape", "仅有一个shape", "单一shape")
    ):
        issues.append("invents_shape_exclusivity")

    has_negative_result = any(
        marker in compact_question
        for marker in ("负结果", "失败结果", "没有收益", "未见收益", "效果为负")
    )
    if has_negative_result and any(
        marker in compact_answer
        for marker in ("停止整个课题", "整个课题没有价值", "课题毫无价值")
    ):
        issues.append("overgeneralizes_negative_result")
    if has_negative_result and any(
        marker in compact_answer
        for marker in ("换场景一定成功", "换场景必然成功", "新场景必然有效")
    ):
        issues.append("promises_unverified_success")

    question_has_number = bool(re.search(r"\d", question))
    suggestion_markers = (
        "待确认建议",
        "示例数字",
        "示例值",
        "建议值",
        "需根据",
        "由统计精度",
    )
    unqualified_numeric_gate = False
    if not question_has_number:
        numeric_matches = list(_UNQUALIFIED_NUMERIC_GATE.finditer(answer))
        numeric_matches.extend(_UNQUALIFIED_CHINESE_NUMERIC_GATE.finditer(answer))
        for match in sorted(numeric_matches, key=lambda item: item.start()):
            clause_start = max(
                answer.rfind(separator, 0, match.start())
                for separator in ("。", "；", ";", "\n")
            )
            clause_ends = [
                position
                for separator in ("。", "；", ";", "\n")
                if (position := answer.find(separator, match.end())) >= 0
            ]
            clause_end = min(clause_ends) if clause_ends else len(answer)
            clause = answer[clause_start + 1 : clause_end]
            if not any(marker in clause for marker in suggestion_markers):
                unqualified_numeric_gate = True
                break
    if unqualified_numeric_gate:
        issues.append("invents_numeric_stop_gate")

    has_inverse_transfer_observation = (
        any(marker in compact_question for marker in ("字节减少", "流量减少", "搬运量减少"))
        and any(marker in compact_question for marker in ("耗时增加", "延迟增加", "时间增加", "反而更慢"))
    )
    asserts_transfer_is_not_bottleneck = any(
        marker in compact_answer
        for marker in (
            "说明搬运不是瓶颈",
            "证明搬运不是瓶颈",
            "因此搬运不是瓶颈",
            "可见搬运不是瓶颈",
        )
    )
    if has_inverse_transfer_observation and asserts_transfer_is_not_bottleneck:
        issues.append("asserts_unverified_cause")

    asks_active_virtual_output = _asks_active_request_reuse(compact_question)
    if asks_active_virtual_output:
        output_path_markers = (
            "outputmemoization",
            "output缓存",
            "完整output",
            "最终output",
            "请求等价",
            "规范化请求",
        )
        rejects_without_evaluation = any(
            marker in compact_answer
            for marker in ("不考虑output", "排除output", "output不可行", "只研究kv")
        )
        if rejects_without_evaluation or not any(
            marker in compact_answer for marker in output_path_markers
        ):
            issues.append("drops_requested_output_reuse")
        required_evaluation_groups = (
            ("场景", "工作负载", "请求分布", "空闲"),
            ("输入",),
            ("正确性", "等价证书", "请求等价"),
            ("不复用", "无缓存"),
            ("被动output", "outputmemoization"),
            ("prefixwarmup", "前缀预热", "静态预热"),
            ("机制消融", "归因消融", "收益来源"),
        )
        if any(
            not any(marker in compact_answer for marker in group)
            for group in required_evaluation_groups
        ):
            issues.append("incomplete_virtual_reuse_evaluation")
        net_cost_markers = (
            "预测",
            "虚拟执行",
            "验证",
            "校验",
            "存储",
            "失效",
            "误预测",
            "机会成本",
        )
        if sum(marker in compact_answer for marker in net_cost_markers) < 5:
            issues.append("incomplete_virtual_reuse_net_benefit")
        if "output" in compact_question:
            output_equivalence_markers = (
                "模型",
                "分词器",
                "模板",
                "系统策略",
                "工具状态",
                "生成配置",
                "采样",
                "随机性",
                "权限",
                "新鲜度",
            )
            if sum(marker in compact_answer for marker in output_equivalence_markers) < 3:
                issues.append("underspecified_output_equivalence")

    if "净收益" in compact_answer and any(
        marker in compact_answer
        for marker in ("净收益为正就证明创新", "净收益为正说明创新成立", "正净收益证明创新")
    ):
        issues.append("conflates_net_benefit_with_innovation")

    return tuple(issues)
