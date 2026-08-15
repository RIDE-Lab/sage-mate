from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, InteractionIntent
from sage_faculty_twin.runtime_identity import (
    is_runtime_identity_query,
    render_runtime_identity_answer,
    RuntimeIdentityProvider,
)
from sage_faculty_twin.service import DigitalTwinService


RUNTIME_QUESTIONS = (
    "当前跑的是什么模型？",
    "现在后端用的是哪个大模型？",
    "线上实际部署了什么模型？",
    "这个系统正在使用几张 NPU？",
    "模型跑在几块华为卡上？",
    "当前推理引擎是什么？",
    "现在的量化方式是什么？",
    "实际 TP 和 DP 是多少？",
    "图模式是否已经启用？",
    "推测解码现在有没有开启？",
    "What model is currently serving requests?",
    "Which inference engine is running now?",
    "What hardware is this system deployed on?",
    "How many accelerator cards are currently used?",
    "Is speculative decoding enabled on the live backend?",
    "What quantization is the running model using?",
    "What is the current tensor parallel size?",
    "Is the deployed engine using graph mode or eager mode?",
    "Which backend version is live right now?",
    "What model and Ascend deployment are serving this site?",
    "张老师是怎么把一个大模型部署在华为 8 卡上面的？",
)


@pytest.mark.parametrize("question", RUNTIME_QUESTIONS)
def test_runtime_identity_query_variants(question: str) -> None:
    assert is_runtime_identity_query(question)


@pytest.mark.parametrize(
    "question",
    (
        "哪个模型更适合做课程实验？",
        "如何部署一个新的推理引擎？",
        "我现在在推理引擎和推理服务系统之间摇摆。",
        "按我的问题类型，您会建议我先理解哪一层？",
        "What model is best for a student project?",
        "How should I deploy a model on my own server?",
    ),
)
def test_general_model_advice_does_not_route_to_runtime_identity(question: str) -> None:
    assert not is_runtime_identity_query(question)


def _provider(
    tmp_path: Path,
    *,
    live_model: str,
    environ: dict[str, str],
) -> RuntimeIdentityProvider:
    settings = AppSettings(runtime_dir=tmp_path)
    return RuntimeIdentityProvider(
        settings,
        model_probe=lambda: live_model,
        versions_provider=lambda: {
            "stack_version_vllm_hust": "v0.23-test",
            "stack_version_vllm_ascend": "v0.23-ascend-test",
        },
        hardware_provider=lambda: {"npu": "0,1,2,3 · 4× 910B2"},
        environ=environ,
    )


def test_live_runtime_snapshot_is_structured_and_public_safe(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(
        json.dumps(
            {
                "model_type": "deepseek_v4",
                "architectures": ["DeepseekV4ForCausalLM"],
            }
        ),
        encoding="utf-8",
    )
    provider = _provider(
        tmp_path,
        live_model="vllm-ascend/DeepSeek-V4-Flash-w8a8-mtp",
        environ={
            "VLLM_ENGINE_MODEL_PATH": str(model_dir),
            "VLLM_ENGINE_NPU_DEVICES": "0,1,2,3",
            "VLLM_ENGINE_TP_SIZE": "4",
            "VLLM_ENGINE_ENABLE_EXPERT_PARALLEL": "1",
            "VLLM_ENGINE_QUANTIZATION": "ascend",
            "VLLM_ENGINE_COMPILATION_CONFIG": '{"cudagraph_mode":"FULL_DECODE_ONLY"}',
            "VLLM_ENGINE_EXTRA_ARGS_JSON": json.dumps(
                ["--data-parallel-size", "1", "--block-size", "128"]
            ),
            "DIGITAL_TWIN_API_KEY": "must-not-leak",
        },
    )

    identity = provider.snapshot()

    assert identity.status == "live"
    assert identity.served_model.endswith("DeepSeek-V4-Flash-w8a8-mtp")
    assert identity.checkpoint_family == "deepseek_v4"
    assert identity.architecture == "DeepseekV4ForCausalLM"
    assert identity.device_ids == ("0", "1", "2", "3")
    assert identity.device_count == 4
    assert identity.tensor_parallel_size == 4
    assert identity.data_parallel_size == 1
    assert identity.graph_mode == "graph"
    assert identity.speculative_enabled is False
    serialized = json.dumps(identity.public_dict(), ensure_ascii=False)
    assert "must-not-leak" not in serialized
    assert str(model_dir) not in serialized


def test_fixture_switch_requires_no_question_rules(tmp_path: Path) -> None:
    provider = _provider(
        tmp_path,
        live_model="glm/GLM-5-W8A8",
        environ={
            "VLLM_ENGINE_NPU_DEVICES": "4,5",
            "VLLM_ENGINE_TP_SIZE": "2",
            "VLLM_ENGINE_QUANTIZATION": "w8a8",
            "VLLM_ENGINE_EXTRA_ARGS_JSON": json.dumps(
                [
                    "--data-parallel-size",
                    "1",
                    "--speculative-config",
                    '{"method":"eagle","num_speculative_tokens":3}',
                ]
            ),
        },
    )

    identity = provider.snapshot()

    assert identity.served_model == "glm/GLM-5-W8A8"
    assert identity.device_ids == ("4", "5")
    assert identity.tensor_parallel_size == 2
    assert identity.speculative_enabled is True
    assert identity.speculative_method == "eagle"


def test_live_capability_overrides_stale_speculative_configuration(tmp_path: Path) -> None:
    settings = AppSettings(runtime_dir=tmp_path)
    provider = RuntimeIdentityProvider(
        settings,
        model_probe=lambda: {
            "id": "vllm-ascend/DeepSeek-V4-Flash-w8a8-mtp",
            "speculative_capability": {
                "status": "disabled",
                "detected_checkpoint_method": "dspark",
                "resolved_method": "none",
            },
        },
        versions_provider=lambda: {},
        hardware_provider=lambda: {},
        environ={
            "VLLM_ENGINE_EXTRA_ARGS_JSON": json.dumps(
                ["--speculative-config", '{"method":"mtp"}']
            )
        },
    )

    identity = provider.snapshot()

    assert identity.speculative_capability == "dspark"
    assert identity.speculative_enabled is False
    assert identity.speculative_method == "none"


def test_deployment_receipt_is_used_when_live_probe_fails(tmp_path: Path) -> None:
    (tmp_path / "engine-deployment.lock.env").write_text(
        "VLLM_ENGINE_SERVED_MODEL_NAME=receipt-model\n"
        "VLLM_ENGINE_NPU_DEVICES=4\\,5\\,6\\,7\n"
        "VLLM_ENGINE_TP_SIZE=4\n"
        "VLLM_ENGINE_QUANTIZATION=ascend\n",
        encoding="utf-8",
    )
    provider = _provider(tmp_path, live_model="", environ={})

    identity = provider.snapshot()

    assert identity.status == "receipt"
    assert identity.source == "deployment-lock"
    assert identity.served_model == "receipt-model"
    assert identity.serving_available is False
    assert identity.device_count == 4
    answer = render_runtime_identity_answer(identity)
    assert "部署锁定目标" in answer
    assert "不代表引擎当前正在提供推理" in answer


def test_probe_error_keeps_configuration_but_marks_serving_unavailable(
    tmp_path: Path,
) -> None:
    (tmp_path / "engine-deployment.lock.env").write_text(
        "VLLM_ENGINE_SERVED_MODEL_NAME=locked-model\n"
        "VLLM_ENGINE_NPU_DEVICES=0\\,1\n",
        encoding="utf-8",
    )
    provider = RuntimeIdentityProvider(
        AppSettings(runtime_dir=tmp_path),
        model_probe=lambda: (_ for _ in ()).throw(TimeoutError("probe timed out")),
        versions_provider=lambda: {},
        hardware_provider=lambda: {},
        environ={},
    )

    identity = provider.snapshot()

    assert identity.status == "receipt"
    assert identity.serving_available is False
    assert identity.served_model == "locked-model"
    assert "当前无法从实时 serving endpoint 确认" in render_runtime_identity_answer(identity)


def test_unknown_runtime_never_guesses_cuda_or_gpu(tmp_path: Path) -> None:
    provider = _provider(tmp_path, live_model="", environ={})

    identity = provider.snapshot()
    answer = render_runtime_identity_answer(identity)

    assert identity.status == "unknown"
    assert identity.serving_available is False
    assert "不会猜测" in answer
    assert "CUDA" not in answer
    assert "GPU" not in answer


def test_runtime_evidence_includes_timestamp_and_source(tmp_path: Path) -> None:
    provider = _provider(
        tmp_path,
        live_model="vllm-ascend/DeepSeek-V4-Flash-w8a8-mtp",
        environ={"VLLM_ENGINE_NPU_DEVICES": "0,1", "VLLM_ENGINE_TP_SIZE": "2"},
    )

    hit = provider.snapshot().to_knowledge_hit()

    assert "runtime" in hit.tags
    assert hit.metadata["collected_at"] in hit.excerpt
    assert hit.metadata["source_kind"] in hit.source_name


def test_end_to_end_chat_uses_runtime_evidence_without_llm_facts(tmp_path: Path) -> None:
    settings = AppSettings(
        runtime_dir=tmp_path,
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "memory",
        model_name="configured-stale-model",
        chat_runtime_pipeline_enabled=False,
    )
    service = DigitalTwinService(settings)
    provider = _provider(
        tmp_path,
        live_model="vllm-ascend/DeepSeek-V4-Flash-w8a8-mtp",
        environ={
            "VLLM_ENGINE_NPU_DEVICES": "0,1,2,3,4,5,6,7",
            "VLLM_ENGINE_TP_SIZE": "8",
            "VLLM_ENGINE_ENABLE_EXPERT_PARALLEL": "1",
            "VLLM_ENGINE_QUANTIZATION": "ascend",
            "VLLM_ENGINE_COMPILATION_CONFIG": '{"cudagraph_mode":"FULL_DECODE_ONLY"}',
            "VLLM_ENGINE_EXTRA_ARGS_JSON": '["--data-parallel-size","1"]',
        },
    )
    service._runtime_identity_provider = provider
    service._llm_client.classify_interaction_intent_sync = lambda *_args, **_kwargs: (
        InteractionIntent(
            action="answer",
            domain="research",
            retrieval_scopes=["profile"],
            exclude_scopes=[],
            decision_mode="direct_answer",
            confidence=1.0,
        )
    )

    response = asyncio.run(
        service.answer_in_process(
            ChatRequest(
                student_name="guest",
                question="当前跑的是什么模型，用了几张 NPU？",
                visitor_profile="general_visitor",
            )
        )
    )

    assert response.used_model == "vllm-ascend/DeepSeek-V4-Flash-w8a8-mtp"
    assert "DeepSeek-V4-Flash" in response.answer
    assert "8×" in response.answer
    assert "CUDA" not in response.answer
    assert response.knowledge_hits[0].metadata["source_kind"] == "live-serving-endpoint"
    assert response.answer_basis[0].basis_label == "实时运行状态"
    assert response.decision_mode == "runtime_identity"
    health = service.health()
    assert health["model_name"] == response.used_model
    assert health["runtime_device_count"] == "8"
    assert health["runtime_serving_available"] == "true"
    assert str(tmp_path) not in json.dumps(response.model_dump(), ensure_ascii=False)


def test_end_to_end_receipt_answer_does_not_claim_live_serving(tmp_path: Path) -> None:
    (tmp_path / "engine-deployment.lock.env").write_text(
        "VLLM_ENGINE_SERVED_MODEL_NAME=locked-model\n"
        "VLLM_ENGINE_NPU_DEVICES=0\\,1\n"
        "VLLM_ENGINE_TP_SIZE=2\n",
        encoding="utf-8",
    )
    settings = AppSettings(
        runtime_dir=tmp_path,
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "memory",
        chat_runtime_pipeline_enabled=False,
    )
    service = DigitalTwinService(settings)
    service._runtime_identity_provider = RuntimeIdentityProvider(
        settings,
        model_probe=lambda: "",
        versions_provider=lambda: {},
        hardware_provider=lambda: {},
        environ={},
    )
    service._llm_client.classify_interaction_intent_sync = lambda *_args, **_kwargs: (
        InteractionIntent(
            action="answer",
            domain="general",
            retrieval_scopes=[],
            exclude_scopes=[],
            decision_mode="direct_answer",
            confidence=1.0,
        )
    )

    response = asyncio.run(
        service.answer_in_process(
            ChatRequest(
                student_name="guest",
                question="当前运行的模型和 NPU 信息是什么？",
                visitor_profile="general_visitor",
            )
        )
    )

    assert response.used_model == "runtime-identity-provider"
    assert "部署锁定目标" in response.answer
    assert "不代表引擎当前正在提供推理" in response.answer
    assert "当前后端实际提供" not in response.answer
    assert response.answer_basis[0].basis_label == "部署配置与可用性"
    assert response.knowledge_hits[0].metadata["serving_available"] == "false"
    health = service.health()
    assert health["model_name"] == "locked-model"
    assert health["runtime_identity_status"] == "receipt"
    assert health["runtime_serving_available"] == "false"
