"""Validated deployment-receipt lifecycle and public evidence adapter."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

from .config import REPO_ROOT, AppSettings
from .models import KnowledgeSearchHit


_RECEIPT_TOOL = REPO_ROOT / "deps/vllm-hust-dev-hub/scripts/deployment_receipt.py"
_PUBLIC_SOURCE_PREFIX = "vllm-hust-dev-hub://deployment/"
_UNSAFE_PUBLIC_VALUE = re.compile(
    r"(?i)(?:bearer\s+|(?:api[_-]?key|token|password|secret)\s*[:=]|"
    r"(?:https?://)?(?:localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|"
    r"192\.168\.\d+\.\d+)|(?:file://|/(?:home|root|data|opt|workspace)/))"
)


class DeploymentReceiptError(ValueError):
    pass


@lru_cache(maxsize=1)
def _load_contract_module():
    spec = importlib.util.spec_from_file_location(
        "vllm_hust_deployment_receipt", _RECEIPT_TOOL
    )
    if spec is None or spec.loader is None:
        raise DeploymentReceiptError("deployment receipt contract is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeploymentReceiptStore:
    """Ingest private receipts and expose only a versioned public allowlist."""

    def __init__(self, settings: AppSettings) -> None:
        self._root = settings.runtime_dir / "data/deployment_receipts"
        self._inbox = self._root / "inbox"
        self._audit = self._root / "audit"
        self._state_path = self._root / "public-state.json"
        self._rejections_path = self._root / "rejections.jsonl"
        self._ttl = timedelta(
            seconds=max(
                60,
                int(
                    os.environ.get(
                        "DIGITAL_TWIN_DEPLOYMENT_RECEIPT_TTL_SECONDS", "604800"
                    )
                ),
            )
        )
        self._lock = RLock()

    def sync_inbox(self) -> dict[str, Any]:
        accepted = 0
        rejected = 0
        with self._lock:
            self._inbox.mkdir(parents=True, exist_ok=True)
            state = self._load_state()
            known = {item.get("receipt_id") for item in state.get("receipts", [])}
            rejected_files = dict(state.get("rejected_files") or {})
            inbox_paths = sorted(self._inbox.glob("*.json"))
            inbox_names = {path.name for path in inbox_paths}
            rejected_files = {
                name: fingerprint
                for name, fingerprint in rejected_files.items()
                if name in inbox_names
            }
            for path in inbox_paths:
                fingerprint = self._file_fingerprint(path)
                if rejected_files.get(path.name) == fingerprint:
                    continue
                try:
                    candidate = json.loads(path.read_text(encoding="utf-8"))
                    receipt_id = (
                        str(candidate.get("receipt_id") or "")
                        if isinstance(candidate, dict)
                        else ""
                    )
                    if receipt_id in known:
                        rejected_files.pop(path.name, None)
                        continue
                    public = self._ingest(candidate)
                    known.add(public["receipt_id"])
                    if path.name in rejected_files:
                        rejected_files.pop(path.name)
                    accepted += 1
                except Exception as exc:
                    self._record_rejection(path.name, str(exc))
                    rejected_files[path.name] = fingerprint
                    rejected += 1
            state = self._load_state()
            state["rejected_files"] = rejected_files
            if rejected:
                state["sync_status"] = "rejected"
                state["last_sync_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            self._write_json(self._state_path, state)
            active = self._active_without_sync()
            stale = (
                self._latest_active_without_freshness() is not None and active is None
            )
            has_rejections = bool(rejected_files)
            return {
                "status": "rejected" if has_rejections else "stale" if stale else "ok",
                "accepted": accepted,
                "rejected": rejected,
                "active_receipt_id": active.get("receipt_id", "") if active else "",
            }

    def ingest(self, receipt: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._ingest(receipt)

    def _ingest(self, candidate: object) -> dict[str, Any]:
        contract = _load_contract_module()
        try:
            receipt = contract.validate_receipt(candidate)
        except Exception as exc:
            raise DeploymentReceiptError(str(exc)) from exc
        generated_at = datetime.fromisoformat(
            receipt["generated_at"].replace("Z", "+00:00")
        )
        if generated_at.tzinfo is None:
            raise DeploymentReceiptError("generated_at must include timezone")
        if generated_at > datetime.now(UTC) + timedelta(minutes=5):
            raise DeploymentReceiptError("receipt timestamp is in the future")
        source_uri = receipt["provenance"]["source_uri"]
        if not source_uri.startswith(_PUBLIC_SOURCE_PREFIX):
            raise DeploymentReceiptError("receipt source_uri is not public-safe")

        public = self._public_record(receipt)
        if _UNSAFE_PUBLIC_VALUE.search(json.dumps(public, ensure_ascii=False)):
            raise DeploymentReceiptError("receipt contains a non-public value")
        state = self._load_state()
        receipts = state.setdefault("receipts", [])
        if public["status"] == "active":
            for item in receipts:
                if item.get("status") == "active":
                    item["status"] = "superseded"
                    item["superseded_at"] = datetime.now(UTC).isoformat(
                        timespec="seconds"
                    )
        receipts.append(public)
        state["last_sync_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        state["sync_status"] = "ok"
        self._write_json(self._state_path, state)
        self._write_json(self._audit / f"{public['receipt_id']}.json", receipt)
        return public

    def active(self) -> dict[str, Any] | None:
        self.sync_inbox()
        with self._lock:
            return self._active_without_sync()

    def _active_without_sync(self) -> dict[str, Any] | None:
        active = self._latest_active_without_freshness()
        if not active:
            return None
        generated_at = datetime.fromisoformat(
            str(active["generated_at"]).replace("Z", "+00:00")
        )
        if datetime.now(UTC) - generated_at > self._ttl:
            return None
        return dict(active)

    def _latest_active_without_freshness(self) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in reversed(self._load_state().get("receipts", []))
                if item.get("status") == "active"
            ),
            None,
        )

    def runtime_mapping(self) -> dict[str, str]:
        active = self.active()
        if not active:
            return {}
        speculative_args = (
            [
                "--data-parallel-size",
                str(active["data_parallel_size"]),
                "--speculative-config",
                json.dumps(
                    {"method": active["speculative_resolved_method"]},
                    separators=(",", ":"),
                ),
            ]
            if active["speculative_active"]
            else ["--data-parallel-size", str(active["data_parallel_size"])]
        )
        return {
            "VLLM_ENGINE_SERVED_MODEL_NAME": active["served_model"],
            "VLLM_ENGINE_MODEL_FAMILY": active["checkpoint_family"],
            "VLLM_ENGINE_ARCHITECTURE": active["architecture"],
            "VLLM_ENGINE_NPU_DEVICES": ",".join(active["physical_device_ids"]),
            "VLLM_ENGINE_TP_SIZE": str(active["tensor_parallel_size"]),
            "VLLM_ENGINE_ENABLE_EXPERT_PARALLEL": "1"
            if active["expert_parallel_enabled"]
            else "0",
            "VLLM_ENGINE_QUANTIZATION": active["quantization"],
            "VLLM_ENGINE_COMPILATION_CONFIG": (
                '{"mode":"graph"}' if active["graph_mode"] == "graph" else ""
            ),
            "VLLM_ENGINE_EXTRA_ARGS_JSON": json.dumps(speculative_args),
            "VLLM_ENGINE_SPECULATIVE_CAPABILITY": active["speculative_resolved_method"],
            "VLLM_ENGINE_SPECULATIVE_REASON": active["speculative_reason"],
            "VLLM_ENGINE_VERSION": active["engine_commit"],
            "VLLM_ENGINE_PLUGIN_VERSION": active["plugin_commit"],
            "VLLM_ENGINE_ACCELERATOR_MODEL": active["accelerator_model"],
        }

    def knowledge_hit(self) -> KnowledgeSearchHit | None:
        active = self.active()
        if not active:
            return None
        detail = (
            f"模型 {active['served_model']}；架构 {active['checkpoint_family']} / "
            f"{active['architecture']}；{active['accelerator_count']}×{active['accelerator_model']}；"
            f"TP={active['tensor_parallel_size']}、DP={active['data_parallel_size']}、"
            f"量化={active['quantization']}、执行={active['graph_mode']}；"
            f"speculative={active['speculative_resolved_method']} "
            f"({'active' if active['speculative_active'] else 'inactive'})；"
            f"采集={active['generated_at']}；receipt={active['receipt_id']}。"
        )
        return KnowledgeSearchHit(
            document_id=f"deployment-receipt:{active['receipt_id']}",
            title="版本化部署回执",
            excerpt=detail,
            score=99.0,
            tags=["runtime", "deployment-receipt", "public"],
            source_name=active["source_uri"],
            metadata={
                "receipt_id": active["receipt_id"],
                "collected_at": active["generated_at"],
                "content_sha256": active["content_sha256"],
            },
        )

    def status(self) -> dict[str, str]:
        with self._lock:
            result = self.sync_inbox()
            active = self._active_without_sync()
        age = "unknown"
        if active:
            generated = datetime.fromisoformat(
                active["generated_at"].replace("Z", "+00:00")
            )
            age = str(max(0, int((datetime.now(UTC) - generated).total_seconds())))
        return {
            "deployment_receipt_sync_status": str(result["status"]),
            "deployment_receipt_active_id": active.get("receipt_id", "")
            if active
            else "",
            "deployment_receipt_schema": active.get("schema_version", "")
            if active
            else "",
            "deployment_receipt_age_seconds": age,
        }

    @staticmethod
    def _public_record(receipt: dict[str, Any]) -> dict[str, Any]:
        model = receipt["model"]
        engine = receipt["engine"]
        hardware = receipt["hardware"]
        parallelism = receipt["parallelism"]
        execution = receipt["execution"]
        speculative = receipt["speculative"]
        return {
            "schema_version": receipt["schema_version"],
            "receipt_id": receipt["receipt_id"],
            "generated_at": receipt["generated_at"],
            "status": receipt["status"],
            "served_model": model["served_name"],
            "checkpoint_family": model["checkpoint_family"],
            "architecture": model["architecture"],
            "engine_name": engine["name"],
            "engine_commit": engine["core_commit"],
            "plugin_name": engine["plugin_name"],
            "plugin_commit": engine["plugin_commit"],
            "accelerator_kind": hardware["accelerator_kind"],
            "accelerator_model": hardware["accelerator_model"],
            "accelerator_count": len(hardware["physical_device_ids"]),
            "physical_device_ids": hardware["physical_device_ids"],
            "tensor_parallel_size": parallelism["tensor_parallel_size"],
            "data_parallel_size": parallelism["data_parallel_size"],
            "expert_parallel_enabled": parallelism["expert_parallel_enabled"],
            "quantization": execution["quantization"],
            "graph_mode": execution["graph_mode"],
            "speculative_requested_method": speculative["requested_method"],
            "speculative_resolved_method": speculative["resolved_method"],
            "speculative_active": speculative["active"],
            "speculative_reason": speculative["reason"],
            "source_uri": receipt["provenance"]["source_uri"],
            "content_sha256": receipt["integrity"]["content_sha256"],
        }

    def _load_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"receipts": []}
        except (OSError, json.JSONDecodeError):
            return {"receipts": [], "sync_status": "empty"}

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _file_fingerprint(path: Path) -> str:
        try:
            stat = path.stat()
            return f"{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            return "unavailable"

    def _record_rejection(self, filename: str, reason: str) -> None:
        self._rejections_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "filename": Path(filename).name,
            "rejected_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "reason": reason[:500],
        }
        with self._rejections_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
