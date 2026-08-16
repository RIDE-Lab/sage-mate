#!/usr/bin/env python3
"""Normalize deployment metadata for an explicitly configured vLLM model.

The automatic model resolver already emits this contract. This helper supplies
the same fields when an operator chooses a checkpoint path directly, without
encoding any model-family or machine-specific naming rules.
"""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from typing import Any


def inspect_model(model_path: Path, served_name: str) -> dict[str, str]:
    config: dict[str, Any] = {}
    config_path = model_path / "config.json"
    if config_path.is_file():
        try:
            candidate = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            candidate = {}
        if isinstance(candidate, dict):
            config = candidate

    architectures = config.get("architectures")
    architecture = ""
    if isinstance(architectures, list) and architectures:
        architecture = str(architectures[0] or "").strip()

    family = str(config.get("model_type") or "").strip()
    configured_id = str(config.get("_name_or_path") or "").strip()
    if configured_id and (configured_id.startswith("/") or configured_id in {".", ".."}):
        configured_id = ""

    return {
        "VLLM_ENGINE_ACTUAL_MODEL_ID": configured_id or served_name,
        "VLLM_ENGINE_MODEL_SOURCE": "local" if model_path.exists() else "configured",
        "VLLM_ENGINE_MODEL_FAMILY": family or architecture or "unknown",
        "VLLM_ENGINE_ARCHITECTURE": architecture or "unknown",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--served-name", required=True)
    args = parser.parse_args()

    for key, value in inspect_model(args.model_path, args.served_name).items():
        print(f"{key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
