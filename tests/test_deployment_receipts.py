from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sage_faculty_twin.config import AppSettings, REPO_ROOT
from sage_faculty_twin.deployment_receipts import (
    DeploymentReceiptStore,
    _load_contract_module,
)
from sage_faculty_twin.models import ChatRequest, InteractionIntent
from sage_faculty_twin.runtime_identity import RuntimeIdentityProvider
from sage_faculty_twin.service import DigitalTwinService


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(runtime_dir=tmp_path)


def _receipt(
    *,
    model: str = "deepseek/fixture-a",
    status: str = "active",
    generated_at: str | None = None,
) -> dict[str, object]:
    payload = {
        "status": status,
        "model": {
            "served_name": model,
            "checkpoint_family": "deepseek_v4",
            "architecture": "DeepseekV4ForCausalLM",
        },
        "engine": {
            "name": "vLLM-HUST",
            "core_commit": "a" * 40,
            "plugin_name": "vllm-ascend-hust",
            "plugin_commit": "b" * 40,
            "image": "private.registry.invalid/runtime:test",
        },
        "hardware": {
            "accelerator_kind": "Ascend NPU",
            "accelerator_model": "Ascend 910B2",
            "physical_device_ids": ["4", "5", "6", "7"],
            "logical_device_ids": ["0", "1", "2", "3"],
        },
        "parallelism": {
            "tensor_parallel_size": 4,
            "data_parallel_size": 1,
            "expert_parallel_enabled": False,
        },
        "execution": {"quantization": "w8a8", "graph_mode": "graph"},
        "speculative": {
            "requested_method": "dspark",
            "resolved_method": "none",
            "active": False,
            "reason": "proposer unavailable",
        },
        "provenance": {
            "source_uri": "vllm-hust-dev-hub://deployment/test-fixture",
            "import_origins": {
                "vllm": "/private/container/source/vllm/__init__.py",
                "vllm_ascend": "/private/container/source/vllm_ascend/__init__.py",
            },
        },
    }
    return _load_contract_module().create_receipt(payload, generated_at=generated_at)


def test_active_receipt_supersedes_previous_but_preserves_audit(tmp_path: Path) -> None:
    store = DeploymentReceiptStore(_settings(tmp_path))
    first = store.ingest(_receipt(model="deepseek/fixture-a"))
    failed = store.ingest(_receipt(model="deepseek/failed", status="failed"))
    assert store.active()["receipt_id"] == first["receipt_id"]
    second = store.ingest(_receipt(model="deepseek/fixture-b"))

    active = store.active()
    state = json.loads(
        (tmp_path / "data/deployment_receipts/public-state.json").read_text()
    )
    assert active is not None and active["receipt_id"] == second["receipt_id"]
    assert (
        next(
            item
            for item in state["receipts"]
            if item["receipt_id"] == first["receipt_id"]
        )["status"]
        == "superseded"
    )
    assert (
        next(
            item
            for item in state["receipts"]
            if item["receipt_id"] == failed["receipt_id"]
        )["status"]
        == "failed"
    )
    assert len(list((tmp_path / "data/deployment_receipts/audit").glob("*.json"))) == 3


def test_stale_receipt_is_not_authoritative(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DIGITAL_TWIN_DEPLOYMENT_RECEIPT_TTL_SECONDS", "60")
    store = DeploymentReceiptStore(_settings(tmp_path))
    stale_time = (datetime.now(UTC) - timedelta(minutes=2)).isoformat(
        timespec="seconds"
    )
    store.ingest(_receipt(generated_at=stale_time))

    assert store.active() is None
    assert store.runtime_mapping() == {}
    assert store.knowledge_hit() is None
    assert store.status()["deployment_receipt_sync_status"] == "stale"


def test_public_record_and_support_are_strictly_redacted(tmp_path: Path) -> None:
    store = DeploymentReceiptStore(_settings(tmp_path))
    store.ingest(_receipt())

    public_text = json.dumps(store.active(), ensure_ascii=False)
    support_text = store.knowledge_hit().model_dump_json()
    for forbidden in (
        "private.registry",
        "/private/container",
        "import_origins",
        "image",
        "token",
        "api_key",
    ):
        assert forbidden not in public_text.lower()
        assert forbidden not in support_text.lower()
    assert "vllm-hust-dev-hub://deployment/" in support_text


def test_sync_rejects_tampering_once_and_exposes_failure(tmp_path: Path) -> None:
    store = DeploymentReceiptStore(_settings(tmp_path))
    inbox = tmp_path / "data/deployment_receipts/inbox"
    inbox.mkdir(parents=True)
    bad = _receipt()
    bad["model"]["served_name"] = "tampered"  # type: ignore[index]
    (inbox / "bad.json").write_text(json.dumps(bad), encoding="utf-8")

    first = store.sync_inbox()
    second = store.sync_inbox()
    rejections = (
        (tmp_path / "data/deployment_receipts/rejections.jsonl")
        .read_text()
        .splitlines()
    )
    assert first["status"] == "rejected" and first["rejected"] == 1
    assert second["status"] == "rejected" and second["rejected"] == 0
    assert len(rejections) == 1
    assert "content hash mismatch" in rejections[0]
    assert "tampered" not in rejections[0]
    (inbox / "bad.json").unlink()
    assert store.sync_inbox()["status"] == "ok"


def test_validly_hashed_receipt_with_private_value_is_rejected(tmp_path: Path) -> None:
    receipt = _receipt()
    payload = {
        key: value
        for key, value in receipt.items()
        if key not in {"schema_version", "receipt_id", "generated_at", "integrity"}
    }
    payload["speculative"]["reason"] = "token=must-not-be-public"  # type: ignore[index]
    sensitive = _load_contract_module().create_receipt(payload)

    store = DeploymentReceiptStore(_settings(tmp_path))
    try:
        store.ingest(sensitive)
    except ValueError as exc:
        assert "non-public value" in str(exc)
    else:  # pragma: no cover - security regression sentinel
        raise AssertionError("sensitive receipt was accepted")


def test_runtime_identity_precedence_live_receipt_lock(tmp_path: Path) -> None:
    store = DeploymentReceiptStore(_settings(tmp_path))
    store.ingest(_receipt(model="receipt-model"))
    (tmp_path / "engine-deployment.lock.env").write_text(
        "VLLM_ENGINE_SERVED_MODEL_NAME=old-lock-model\nVLLM_ENGINE_TP_SIZE=1\n",
        encoding="utf-8",
    )
    live = {"value": ""}
    provider = RuntimeIdentityProvider(
        _settings(tmp_path),
        model_probe=lambda: live["value"],
        versions_provider=lambda: {},
        hardware_provider=lambda: {},
        versioned_receipt_provider=store.runtime_mapping,
        environ={},
    )

    receipt_identity = provider.snapshot()
    assert receipt_identity.source == "deployment-receipt"
    assert receipt_identity.served_model == "receipt-model"
    assert receipt_identity.serving_available is False
    assert receipt_identity.tensor_parallel_size == 4
    assert receipt_identity.checkpoint_family == "deepseek_v4"
    live["value"] = "live-model"
    assert provider.snapshot().served_model == "live-model"
    assert provider.snapshot().source == "live-serving-endpoint"


def test_runtime_question_cites_receipt_and_maintenance_reports_sync(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        runtime_dir=tmp_path / "runtime",
        knowledge_base_dir=tmp_path / "knowledge",
        conversation_memory_dir=tmp_path / "memory",
        chat_runtime_pipeline_enabled=False,
    )
    inbox = settings.runtime_dir / "data/deployment_receipts/inbox"
    inbox.mkdir(parents=True)
    receipt = _receipt(model="deepseek/citation-fixture")
    (inbox / "verified.json").write_text(json.dumps(receipt), encoding="utf-8")
    service = DigitalTwinService(settings)
    service._runtime_identity_provider = RuntimeIdentityProvider(
        settings,
        model_probe=lambda: "",
        versions_provider=lambda: {},
        hardware_provider=lambda: {},
        versioned_receipt_provider=service._deployment_receipt_store.runtime_mapping,
        environ={},
    )
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
                question="当前部署的模型、NPU 和 speculative 状态是什么？",
                visitor_profile="general_visitor",
            )
        )
    )
    receipt_hits = [
        hit for hit in response.knowledge_hits if "deployment-receipt" in hit.tags
    ]
    assert response.used_model == "runtime-identity-provider"
    assert "不代表引擎当前正在提供推理" in response.answer
    assert (
        receipt_hits and receipt_hits[0].metadata["receipt_id"] == receipt["receipt_id"]
    )
    assert response.answer_basis
    summary = service.list_knowledge_review_summary()
    assert summary.active_deployment_receipt_id == receipt["receipt_id"]
    assert summary.active_deployment_receipt_schema == "vllm-hust.deployment-receipt/v1"
    assert summary.deployment_receipt_sync_status == "ok"


def test_verified_writer_creates_contract_receipt_without_public_secrets(
    tmp_path: Path,
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(
        json.dumps(
            {"model_type": "deepseek_v4", "architectures": ["DeepseekV4ForCausalLM"]}
        ),
        encoding="utf-8",
    )
    models = {
        "data": [
            {
                "id": "deepseek/verified",
                "speculative_capability": {
                    "requested_method": "dspark",
                    "resolved_method": "none",
                    "status": "disabled",
                    "reason": "proposer unavailable",
                },
            }
        ]
    }
    env = os.environ | {
        "VLLM_ENGINE_MODEL_PATH": str(model_dir),
        "VLLM_ENGINE_NPU_DEVICES": "4,5,6,7",
        "VLLM_ENGINE_TP_SIZE": "4",
        "VLLM_ENGINE_COMPILATION_CONFIG": '{"mode":"graph"}',
        "VLLM_ENGINE_IMAGE": "private.registry.invalid/image:test",
    }
    result = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "tools/write_verified_deployment_receipt.py"),
            "--models-json",
            json.dumps(models),
            "--import-origins",
            "vllm=/private/vllm/__init__.py\nvllm_ascend=/private/vllm_ascend/__init__.py",
            "--runtime-dir",
            str(tmp_path / "runtime"),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    receipt_path = Path(result.stdout.strip())
    receipt = _load_contract_module().validate_receipt(
        json.loads(receipt_path.read_text())
    )
    assert receipt["model"]["served_name"] == "deepseek/verified"
    assert receipt["parallelism"]["tensor_parallel_size"] == 4
    assert receipt["execution"]["graph_mode"] == "graph"
    assert receipt_path.stat().st_mode & 0o777 == 0o600


def test_verifier_writes_receipt_only_after_all_runtime_gates() -> None:
    script = (REPO_ROOT / "tools/verify_sage_mate_engine.sh").read_text(
        encoding="utf-8"
    )
    writer = script.index("write_verified_deployment_receipt.py")
    assert writer > script.index("chat=OK")
    assert writer > script.index("graph_mode=ON")
    assert writer > script.index("import_origins=")
    assert writer < script.index('echo "[sage-mate-verify] PASS"')
