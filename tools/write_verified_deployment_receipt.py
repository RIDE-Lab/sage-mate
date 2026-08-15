#!/usr/bin/env python3
"""Write a private versioned receipt after all engine verification gates pass."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "deps/vllm-hust-dev-hub/scripts/deployment_receipt.py"


def _load_contract():
    spec = importlib.util.spec_from_file_location(
        "deployment_receipt_contract", CONTRACT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("deployment receipt contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_commit(path: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _model_identity() -> tuple[str, str]:
    model_path = os.environ.get("VLLM_ENGINE_MODEL_PATH") or os.environ.get(
        "VLLM_HUST_MODEL"
    )
    if not model_path:
        return "unknown", "unknown"
    try:
        config = json.loads(
            (Path(model_path) / "config.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return "unknown", "unknown"
    architectures = config.get("architectures") if isinstance(config, dict) else []
    return (
        str(config.get("model_type") or "unknown"),
        str(
            architectures[0]
            if isinstance(architectures, list) and architectures
            else "unknown"
        ),
    )


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _capability(models: dict[str, Any]) -> tuple[str, str, bool, str]:
    items = models.get("data") or []
    first = (
        items[0]
        if isinstance(items, list) and items and isinstance(items[0], dict)
        else {}
    )
    capability = first.get("speculative_capability") or {}
    if not isinstance(capability, dict):
        capability = {}
    requested = str(capability.get("requested_method") or "none")
    resolved = str(capability.get("resolved_method") or "none")
    active = str(capability.get("status") or "").lower() == "enabled"
    reason = str(capability.get("reason") or capability.get("status_reason") or "")
    return requested, resolved, active, reason


def _origins(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        name, origin = line.split("=", 1)
        if name.strip() in {"vllm", "vllm_ascend"} and origin.strip():
            result[name.strip()] = origin.strip()
    if set(result) != {"vllm", "vllm_ascend"}:
        raise ValueError("both verified import origins are required")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-json", required=True)
    parser.add_argument("--import-origins", required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    args = parser.parse_args()

    models = json.loads(args.models_json)
    items = models.get("data") or []
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise SystemExit("ERROR: verified models response has no model")
    served_name = str(items[0].get("id") or "").strip()
    if not served_name:
        raise SystemExit("ERROR: verified model id is empty")
    family, architecture = _model_identity()
    physical_ids = [
        item.strip()
        for item in (
            os.environ.get("VLLM_ENGINE_NPU_DEVICES")
            or os.environ.get("VLLM_ENGINE_ALLOWED_NPU_IDS")
            or ""
        ).split(",")
        if item.strip()
    ]
    if not physical_ids:
        raise SystemExit("ERROR: verified physical NPU ids are empty")
    requested, resolved, speculative_active, reason = _capability(models)
    graph_mode = (
        "eager"
        if _bool(os.environ.get("VLLM_ENGINE_ENFORCE_EAGER"))
        else "graph"
        if os.environ.get("VLLM_ENGINE_COMPILATION_CONFIG")
        else "unknown"
    )
    payload = {
        "status": "active",
        "model": {
            "served_name": served_name,
            "checkpoint_family": family,
            "architecture": architecture,
        },
        "engine": {
            "name": "vLLM-HUST",
            "core_commit": _git_commit(REPO_ROOT / "deps/vllm-hust"),
            "plugin_name": "vllm-ascend-hust",
            "plugin_commit": _git_commit(REPO_ROOT / "deps/vllm-ascend-hust"),
            "image": os.environ.get("VLLM_ENGINE_IMAGE") or "unknown",
        },
        "hardware": {
            "accelerator_kind": "Ascend NPU",
            "accelerator_model": os.environ.get("VLLM_ENGINE_ACCELERATOR_MODEL")
            or "Ascend NPU",
            "physical_device_ids": physical_ids,
            "logical_device_ids": [str(index) for index in range(len(physical_ids))],
        },
        "parallelism": {
            "tensor_parallel_size": int(
                os.environ.get("VLLM_ENGINE_TP_SIZE") or len(physical_ids)
            ),
            "data_parallel_size": int(os.environ.get("VLLM_ENGINE_DP_SIZE") or "1"),
            "expert_parallel_enabled": _bool(
                os.environ.get("VLLM_ENGINE_ENABLE_EXPERT_PARALLEL")
            ),
        },
        "execution": {
            "quantization": os.environ.get("VLLM_ENGINE_QUANTIZATION") or "unknown",
            "graph_mode": graph_mode,
        },
        "speculative": {
            "requested_method": requested,
            "resolved_method": resolved,
            "active": speculative_active,
            "reason": reason,
        },
        "provenance": {
            "source_uri": "vllm-hust-dev-hub://deployment/verified-engine",
            "import_origins": _origins(args.import_origins),
        },
    }
    contract = _load_contract()
    receipt = contract.create_receipt(payload)
    output = (
        args.runtime_dir
        / "data/deployment_receipts/inbox"
        / f"{receipt['receipt_id']}.json"
    )
    contract._write_json(output, receipt)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
