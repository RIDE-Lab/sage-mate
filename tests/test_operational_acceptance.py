from __future__ import annotations

import json

import pytest

from sage_faculty_twin.config import REPO_ROOT
from sage_faculty_twin.operational_acceptance import (
    OPERATIONAL_QUESTIONS,
    OperationalExpectedFacts,
    evaluate_operational_response,
)
from sage_faculty_twin.runtime_identity import (
    RuntimeIdentity,
    is_runtime_identity_query,
    render_runtime_identity_answer,
)


FIXTURES = (
    {
        "model_name": "deepseek/DeepSeek-V4-Flash-W8A8",
        "runtime_identity_status": "receipt",
        "runtime_architecture": "DeepseekV4ForCausalLM",
        "runtime_accelerator": "Ascend 910B2",
        "runtime_device_count": "8",
        "runtime_tp_size": "8",
        "runtime_dp_size": "1",
        "runtime_ep_enabled": "true",
        "runtime_quantization": "w8a8",
        "runtime_graph_mode": "graph",
        "runtime_speculative_enabled": "false",
        "runtime_speculative_method": "none",
        "runtime_speculative_reason": "proposer unavailable",
    },
    {
        "model_name": "deepseek/DeepSeek-V2",
        "runtime_identity_status": "live",
        "runtime_architecture": "DeepseekV2ForCausalLM",
        "runtime_accelerator": "NVIDIA H100 GPU",
        "runtime_device_count": "4",
        "runtime_tp_size": "4",
        "runtime_dp_size": "1",
        "runtime_ep_enabled": "false",
        "runtime_quantization": "fp8",
        "runtime_graph_mode": "graph",
        "runtime_speculative_enabled": "true",
        "runtime_speculative_method": "mtp",
        "runtime_speculative_reason": "enabled by verified profile",
    },
    {
        "model_name": "zai-org/GLM-5-W8A8",
        "runtime_identity_status": "receipt",
        "runtime_architecture": "GlmForCausalLM",
        "runtime_accelerator": "Ascend 910C",
        "runtime_device_count": "2",
        "runtime_tp_size": "2",
        "runtime_dp_size": "1",
        "runtime_ep_enabled": "false",
        "runtime_quantization": "w8a8",
        "runtime_graph_mode": "graph",
        "runtime_speculative_enabled": "false",
        "runtime_speculative_method": "none",
        "runtime_speculative_reason": "checkpoint has no compatible proposer",
    },
)


def _body(expected: OperationalExpectedFacts, *, answer: str | None = None) -> dict:
    enabled = expected.speculative_enabled.lower() == "true"
    rendered = answer or (
        f"Model {expected.model}; architecture {expected.architecture}; "
        f"accelerator {expected.device_count}× {expected.accelerator}; "
        f"TP={expected.tensor_parallel_size}, DP={expected.data_parallel_size}, "
        f"EP={'on' if expected.expert_parallel_enabled.lower() == 'true' else 'off'}; "
        f"quantization {expected.quantization}; execution {expected.graph_mode}; "
        f"speculative decoding {'enabled' if enabled else 'not enabled'} "
        f"({expected.speculative_method})."
        + (
            " This is the configured deployment target and is not evidence that the engine "
            "is currently serving."
            if not expected.runtime_available
            else ""
        )
    )
    return {
        "answer": rendered,
        "used_model": (
            expected.model
            if expected.runtime_available
            else "runtime-identity-provider"
        ),
        "decision_mode": "runtime_identity",
        "knowledge_hits": [
            {
                "title": "Runtime evidence",
                "tags": ["runtime", "deployment"],
                "source_name": "runtime:fixture",
            }
        ],
        "answer_basis": [{"basis_label": "Runtime"}],
        "request_timing": {
            "trace_id": "fixture-trace",
            "route": "fast_path",
            "stage_durations_ms": {"knowledge_retrieve": 1.2},
        },
    }


@pytest.mark.parametrize("question", OPERATIONAL_QUESTIONS)
def test_all_operational_variants_route_to_authoritative_identity(
    question: str,
) -> None:
    assert is_runtime_identity_query(question)


@pytest.mark.parametrize("health", FIXTURES)
def test_same_gate_adapts_to_model_and_hardware_fixtures(health: dict) -> None:
    expected = OperationalExpectedFacts.from_health(health)
    result = evaluate_operational_response(
        question="fixture",
        expected=expected,
        status_code=200,
        elapsed_seconds=0.2,
        body=_body(expected),
    )
    assert result["passed"] is True
    assert result["contradiction_score"] == 0
    assert result["reference_coverage"] == 1.0


@pytest.mark.parametrize("health", FIXTURES)
def test_production_renderer_passes_independent_fixture_gate(health: dict) -> None:
    expected = OperationalExpectedFacts.from_health(health)
    identity = RuntimeIdentity(
        status=health["runtime_identity_status"],
        source="fixture",
        collected_at="2026-08-15T00:00:00+00:00",
        served_model=expected.model,
        serving_available=expected.runtime_available,
        checkpoint_family="fixture-family",
        architecture=expected.architecture,
        accelerator_model=expected.accelerator,
        device_count=int(expected.device_count),
        tensor_parallel_size=int(expected.tensor_parallel_size),
        data_parallel_size=int(expected.data_parallel_size),
        expert_parallel_enabled=expected.expert_parallel_enabled == "true",
        quantization=expected.quantization,
        graph_mode=expected.graph_mode,
        speculative_enabled=expected.speculative_enabled == "true",
        speculative_method=expected.speculative_method,
        speculative_reason=expected.speculative_reason,
    )
    body = _body(expected, answer=render_runtime_identity_answer(identity))
    result = evaluate_operational_response(
        question="How do SAGE, vLLM-HUST, and vLLM-Ascend-HUST cooperate here?",
        expected=expected,
        status_code=200,
        elapsed_seconds=0.1,
        body=body,
    )
    assert result["passed"] is True


def test_runtime_conflict_is_reported_as_contradiction() -> None:
    expected = OperationalExpectedFacts.from_health(FIXTURES[0])
    stale = OperationalExpectedFacts.from_health(FIXTURES[1])
    result = evaluate_operational_response(
        question="misleading fixture",
        expected=expected,
        status_code=200,
        elapsed_seconds=0.1,
        body=_body(stale),
    )
    assert result["passed"] is False
    assert result["used_model_ok"] is False
    assert result["contradiction_score"] > 0


def test_forbidden_gpu_template_fails_ascend_fixture() -> None:
    expected = OperationalExpectedFacts.from_health(FIXTURES[0])
    body = _body(expected)
    body["answer"] += " The backend uses CUDA on NVIDIA GPU with TensorRT-LLM."
    result = evaluate_operational_response(
        question="misleading premise",
        expected=expected,
        status_code=200,
        elapsed_seconds=0.1,
        body=body,
    )
    assert result["passed"] is False
    assert set(result["forbidden_facts_found"]) == {
        "cuda",
        "nvidia gpu",
        "tensorrt-llm",
    }


def test_no_runtime_requires_explicit_uncertainty_not_gpu_guess() -> None:
    expected = OperationalExpectedFacts.from_health(
        {"runtime_identity_status": "unknown"}
    )
    body = _body(expected, answer="当前运行时身份无法读取，我不会猜测模型和硬件。")
    body["used_model"] = "runtime-identity-provider"
    result = evaluate_operational_response(
        question="当前模型是什么？",
        expected=expected,
        status_code=200,
        elapsed_seconds=0.1,
        body=body,
    )
    assert result["passed"] is True


def test_missing_runtime_support_fails_even_when_words_are_correct() -> None:
    expected = OperationalExpectedFacts.from_health(FIXTURES[0])
    body = _body(expected)
    body["knowledge_hits"] = []
    body["answer_basis"] = []
    result = evaluate_operational_response(
        question="fixture",
        expected=expected,
        status_code=200,
        elapsed_seconds=0.1,
        body=body,
    )
    assert result["passed"] is False
    assert result["reference_coverage"] == 0


def test_machine_readable_smoke_tool_contains_required_diagnostics() -> None:
    source = (REPO_ROOT / "tools/validate_operational_self_knowledge.py").read_text(
        encoding="utf-8"
    )
    source += (REPO_ROOT / "src/sage_faculty_twin/operational_acceptance.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "contradiction_score",
        "reference_coverage",
        "trace_id",
        "stage_durations_ms",
        "expected-fixture",
        "operational-self-knowledge/v1",
    ):
        assert required in source
    json.dumps({"questions": OPERATIONAL_QUESTIONS}, ensure_ascii=False)
    deployment_verifier = (REPO_ROOT / "tools/verify_hosted_web_deploy.py").read_text(
        encoding="utf-8"
    )
    assert "validate_operational_self_knowledge.py" in deployment_verifier
    assert "operational-self-knowledge-latest.json" in deployment_verifier
