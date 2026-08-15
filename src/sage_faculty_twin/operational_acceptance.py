"""Model-independent operational self-knowledge acceptance contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


OPERATIONAL_QUESTIONS = (
    "当前运行的模型、架构和量化方式是什么？",
    "现在后端实际跑的是哪个模型？",
    "目前用的是 GPU 还是 Ascend NPU，一共多少卡？",
    "当前 TP、DP、EP 分别是多少？",
    "线上是 graph mode 还是 eager mode？",
    "现在是否启用了 speculative execution？",
    "推测解码为什么没有启用？",
    "请给出当前模型、NPU 数量和并行配置。",
    "当前推理后端的模型身份和硬件是什么？",
    "现在服务用的模形和量化是什么？",
    "那现在用了几张卡？",
    "听说线上是 CUDA + TensorRT-LLM，这个说法对吗？",
    "既然现在跑在 NVIDIA GPU 上，请告诉我显卡数量。",
    "当前 SAGE、vLLM-HUST 和 vLLM-Ascend-HUST 是如何协作的？",
    "What model and architecture are currently serving requests?",
    "Is the live backend using GPU or Ascend NPU, and how many devices?",
    "What are the current TP, DP, and EP settings?",
    "Is the serving engine using graph mode or eager mode?",
    "Is speculative decoding active, and what method was resolved?",
    "I assume this site uses CUDA and TensorRT-LLM. Is that correct?",
    "How do SAGE, vLLM-HUST, and vLLM-Ascend-HUST cooperate here?",
    "Which quantization is active on the current backend?",
    "What model is live rn, and how many accelerator cards does it use?",
    "Does the current answer come from runtime evidence or model memory?",
)


def _text(value: object, default: str = "unknown") -> str:
    rendered = str(value or "").strip()
    return rendered if rendered else default


def _aliases(value: str, *extra: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(item for item in (value, *extra) if item and item != "unknown")
    )


@dataclass(frozen=True, slots=True)
class OperationalExpectedFacts:
    model: str
    architecture: str
    accelerator: str
    device_count: str
    tensor_parallel_size: str
    data_parallel_size: str
    expert_parallel_enabled: str
    quantization: str
    graph_mode: str
    speculative_enabled: str
    speculative_method: str
    speculative_reason: str
    runtime_status: str = "live"
    runtime_available: bool = True
    forbidden_facts: tuple[str, ...] = ()

    @classmethod
    def from_health(cls, health: dict[str, Any]) -> OperationalExpectedFacts:
        status = _text(health.get("runtime_identity_status"))
        accelerator = _text(health.get("runtime_accelerator"))
        forbidden: tuple[str, ...] = ()
        lowered = accelerator.lower()
        if "ascend" in lowered or "910" in lowered or "npu" in lowered:
            forbidden = ("cuda", "nvidia gpu", "tensorrt-llm", "tensorrt_llm")
        elif "nvidia" in lowered or "gpu" in lowered:
            forbidden = ("ascend npu", "910b", "910b2")
        return cls(
            model=_text(health.get("model_name")),
            architecture=_text(health.get("runtime_architecture")),
            accelerator=accelerator,
            device_count=_text(health.get("runtime_device_count")),
            tensor_parallel_size=_text(health.get("runtime_tp_size")),
            data_parallel_size=_text(health.get("runtime_dp_size")),
            expert_parallel_enabled=_text(health.get("runtime_ep_enabled")),
            quantization=_text(health.get("runtime_quantization")),
            graph_mode=_text(health.get("runtime_graph_mode")),
            speculative_enabled=_text(health.get("runtime_speculative_enabled")),
            speculative_method=_text(health.get("runtime_speculative_method")),
            speculative_reason=_text(health.get("runtime_speculative_reason")),
            runtime_status=status,
            runtime_available=status == "live",
            forbidden_facts=forbidden,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OperationalExpectedFacts:
        values = {
            field: payload[field]
            for field in cls.__dataclass_fields__
            if field in payload
        }
        if "forbidden_facts" in values:
            values["forbidden_facts"] = tuple(values["forbidden_facts"])
        return cls(**values)

    def required_aliases(self) -> dict[str, tuple[str, ...]]:
        if self.runtime_status == "unknown":
            return {
                "uncertainty": ("unavailable", "unknown", "无法", "未知", "不会猜测")
            }
        ep = self.expert_parallel_enabled.lower()
        speculative = self.speculative_enabled.lower()
        aliases = {
            "model": _aliases(self.model),
            "architecture": _aliases(self.architecture),
            "accelerator": _aliases(
                self.accelerator, "ascend" if "910" in self.accelerator else ""
            ),
            "device_count": _aliases(
                f"{self.device_count}×",
                f"{self.device_count}x",
                f"{self.device_count} 张",
                f"{self.device_count} cards",
                f"{self.device_count} devices",
            ),
            "tensor_parallel": _aliases(f"tp={self.tensor_parallel_size}"),
            "data_parallel": _aliases(f"dp={self.data_parallel_size}"),
            "expert_parallel": _aliases(
                "ep=on" if ep in {"true", "on", "1"} else "ep=off",
                f"ep={ep}",
            ),
            "quantization": _aliases(self.quantization),
            "graph_mode": _aliases(self.graph_mode),
            "speculative": _aliases(
                "已启用" if speculative in {"true", "on", "1"} else "未启用",
                "enabled" if speculative in {"true", "on", "1"} else "not enabled",
                self.speculative_method,
            ),
        }
        if not self.runtime_available:
            aliases["availability"] = (
                "configured deployment target",
                "not evidence that the engine is currently serving",
                "部署锁定目标",
                "不代表引擎当前正在提供推理",
                "不可用或尚未验证",
            )
        return aliases


def _normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def evaluate_operational_response(
    *,
    question: str,
    expected: OperationalExpectedFacts,
    status_code: int,
    elapsed_seconds: float,
    body: dict[str, Any],
) -> dict[str, Any]:
    answer = _normalize(body.get("answer"))
    aliases = expected.required_aliases()
    required_checks = {
        key: any(_normalize(alias) in answer for alias in candidates)
        for key, candidates in aliases.items()
        if candidates
    }
    lowered_question = _normalize(question)
    if "sage" in lowered_question and "vllm-hust" in lowered_question:
        expected_accelerator = expected.accelerator.lower()
        expects_ascend = any(
            marker in expected_accelerator for marker in ("ascend", "910", "npu")
        )
        required_checks.update(
            {
                "collaboration_sage": "sage" in answer,
                "collaboration_engine": "vllm-hust" in answer,
                "collaboration_plugin": (
                    ("ascend plugin" in answer or "ascend 插件" in answer)
                    if expects_ascend
                    else ("platform backend" in answer or "平台后端" in answer)
                ),
            }
        )
    if "为什么" in lowered_question or "why" in lowered_question:
        required_checks["speculative_reason"] = (
            _normalize(expected.speculative_reason) in answer
        )
    contradictions = [
        forbidden
        for forbidden in expected.forbidden_facts
        if _normalize(forbidden) in answer
    ]
    knowledge_hits = body.get("knowledge_hits") or []
    runtime_hits = [
        hit
        for hit in knowledge_hits
        if "runtime" in {str(tag).lower() for tag in (hit.get("tags") or [])}
    ]
    support_ok = bool(runtime_hits and body.get("answer_basis"))
    used_model_ok = (
        body.get("used_model") == expected.model
        if expected.runtime_available
        else body.get("used_model") == "runtime-identity-provider"
    )
    route_ok = body.get("decision_mode") == "runtime_identity"
    timing = body.get("request_timing") or {}
    trace_id = str(timing.get("trace_id") or "")
    required_passed = sum(required_checks.values())
    denominator = max(1, len(required_checks) + len(expected.forbidden_facts))
    contradiction_score = round(
        (len(contradictions) + len(required_checks) - required_passed) / denominator,
        4,
    )
    passed = bool(
        status_code == 200
        and all(required_checks.values())
        and not contradictions
        and support_ok
        and used_model_ok
        and route_ok
    )
    return {
        "question": question,
        "status_code": status_code,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "passed": passed,
        "required_facts": required_checks,
        "forbidden_facts_found": contradictions,
        "contradiction_score": contradiction_score,
        "used_model": body.get("used_model"),
        "used_model_ok": used_model_ok,
        "route": timing.get("route"),
        "decision_mode": body.get("decision_mode"),
        "route_ok": route_ok,
        "trace_id": trace_id,
        "stage_durations_ms": timing.get("stage_durations_ms") or {},
        "knowledge_hit_count": len(knowledge_hits),
        "runtime_evidence_count": len(runtime_hits),
        "reference_coverage": 1.0 if support_ok else 0.0,
        "support_ok": support_ok,
    }
