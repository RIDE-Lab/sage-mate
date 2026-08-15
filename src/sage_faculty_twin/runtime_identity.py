"""Authoritative, public-safe identity for the active inference runtime."""

from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import AppSettings
from .models import KnowledgeSearchHit


_RUNTIME_SUBJECT_MARKERS = (
    "模型", "模形", "摸型", "大模型", "推理引擎", "后端", "部署", "运行", "npu", "ascend",
    "华为卡", "几张卡", "几块卡", "量化", "并行", "tp", "dp", "ep",
    "图模式", "eager", "speculative", "推测解码", "model", "engine",
    "backend", "deployed", "deployment", "hardware", "accelerator", "card",
    "quantization", "parallel", "tensor parallel", "graph mode", "cuda", "gpu",
    "nvidia", "tensorrt", "sage", "vllm-hust", "vllm-ascend",
)
_RUNTIME_CONTEXT_MARKERS = (
    "当前", "现在", "目前", "正在", "实际", "线上", "这里", "这个系统", "你用", "跑的", "运行的",
    "跑在", "部署的", "用的是", "已经启用", "已启用", "有没有开启",
    "启用", "active",
    "你们是怎么", "你是怎么", "怎么把", "如何把",
    "current", "currently", "running", "serving", "served", "live", "this system",
    "deployed", "deployed backend", "how did you deploy", "what are you using",
    "this site", "here", "live rn", "runtime evidence", "model memory",
)
_RUNTIME_QUERY_MARKERS = (
    "是什么", "什么", "哪个", "哪种", "几张", "几块", "多少", "是否", "有没有",
    "用的是", "怎么把", "如何把", "what", "which", "how many", "is ",
    "are ", "does ", "对吗", "分别", "为什么", "关系", "协作", "还是",
    "给出", "告诉我", " or ", "why", "how do", "come from",
)


def is_runtime_identity_query(question: str) -> bool:
    """Classify operational self-knowledge without model- or person-specific rules."""
    segments = (
        " ".join(segment.split())
        for segment in re.split(r"[\n。！？!?]+", question.lower())
    )
    return any(
        segment
        and any(marker in segment for marker in _RUNTIME_SUBJECT_MARKERS)
        and any(marker in segment for marker in _RUNTIME_CONTEXT_MARKERS)
        and any(marker in segment for marker in _RUNTIME_QUERY_MARKERS)
        for segment in segments
    )


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    status: str
    source: str
    collected_at: str
    serving_available: bool = False
    served_model: str = "unknown"
    checkpoint_family: str = "unknown"
    architecture: str = "unknown"
    engine: str = "vLLM-HUST"
    engine_version: str = "unknown"
    plugin_version: str = "unknown"
    accelerator_kind: str = "Ascend NPU"
    accelerator_model: str = "unknown"
    device_ids: tuple[str, ...] = ()
    device_count: int = 0
    tensor_parallel_size: int | None = None
    data_parallel_size: int | None = None
    expert_parallel_enabled: bool | None = None
    quantization: str = "unknown"
    graph_mode: str = "unknown"
    speculative_capability: str = "unknown"
    speculative_method: str = "none"
    speculative_enabled: bool = False
    speculative_reason: str = "unknown"

    def public_dict(self) -> dict[str, Any]:
        """Return an explicit allowlist; paths, hosts and credentials cannot escape."""
        return asdict(self)

    def evidence_excerpt(self) -> str:
        devices = (
            f"{self.device_count} 张 {self.accelerator_model or self.accelerator_kind}"
            if self.device_count
            else "设备数量未知"
        )
        parallel = "/".join(
            part
            for part in (
                f"TP={self.tensor_parallel_size}" if self.tensor_parallel_size else "",
                f"DP={self.data_parallel_size}" if self.data_parallel_size else "",
                "EP=on" if self.expert_parallel_enabled is True else "EP=off"
                if self.expert_parallel_enabled is False
                else "",
            )
            if part
        ) or "并行配置未知"
        speculative = (
            f"已启用（{self.speculative_method}）"
            if self.speculative_enabled
            else f"未启用（能力：{self.speculative_capability}；原因：{self.speculative_reason}）"
        )
        model_role = "served" if self.serving_available else "configured"
        availability = "live" if self.serving_available else "unavailable-or-unverified"
        return (
            f"{model_role} model={self.served_model}；availability={availability}；"
            f"checkpoint={self.checkpoint_family} / "
            f"{self.architecture}；engine={self.engine} {self.engine_version}；"
            f"plugin={self.plugin_version}；accelerator={devices}；{parallel}；"
            f"quantization={self.quantization}；execution={self.graph_mode}；"
            f"speculative={speculative}；采集时间={self.collected_at}；来源={self.source}。"
        )

    def to_knowledge_hit(self) -> KnowledgeSearchHit:
        return KnowledgeSearchHit(
            document_id=f"runtime-identity:{self.collected_at}",
            title="当前推理运行时身份",
            excerpt=self.evidence_excerpt(),
            score=100.0,
            tags=["runtime", "deployment", "public"],
            source_name=f"runtime:{self.source}",
            metadata={
                "runtime_status": self.status,
                "serving_available": str(self.serving_available).lower(),
                "collected_at": self.collected_at,
                "source_kind": self.source,
            },
        )


class RuntimeIdentityProvider:
    """Resolve live state, then a fresh versioned receipt, then the legacy lock."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        model_probe: Callable[[], str | Mapping[str, Any]],
        versions_provider: Callable[[], Mapping[str, str]],
        hardware_provider: Callable[[], Mapping[str, str]],
        versioned_receipt_provider: Callable[[], Mapping[str, str]] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._model_probe = model_probe
        self._versions_provider = versions_provider
        self._hardware_provider = hardware_provider
        self._versioned_receipt_provider = versioned_receipt_provider
        self._environ = environ if environ is not None else os.environ

    def snapshot(self) -> RuntimeIdentity:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        live_model = ""
        live_capability: Mapping[str, Any] = {}
        try:
            probe_result = self._model_probe()
            if isinstance(probe_result, Mapping):
                live_model = str(probe_result.get("id") or "").strip()
                candidate = probe_result.get("speculative_capability")
                live_capability = candidate if isinstance(candidate, Mapping) else {}
            else:
                live_model = str(probe_result).strip()
        except Exception:
            live_model = ""

        legacy_receipt = self._read_receipt()
        versioned_receipt: Mapping[str, str] = {}
        if self._versioned_receipt_provider is not None:
            try:
                versioned_receipt = self._versioned_receipt_provider()
            except Exception:
                versioned_receipt = {}
        merged = dict(legacy_receipt)
        merged.update({key: value for key, value in self._environ.items() if value})
        merged.update({key: value for key, value in versioned_receipt.items() if value})
        versions = dict(self._versions_provider())
        hardware = dict(self._hardware_provider())
        served_model = live_model or self._first(
            merged, "VLLM_ENGINE_SERVED_MODEL_NAME", "DIGITAL_TWIN_MODEL_NAME"
        )
        has_fallback = bool(versioned_receipt or legacy_receipt)
        status = "live" if live_model else "receipt" if has_fallback else "unknown"
        source = (
            "live-serving-endpoint"
            if live_model
            else "deployment-receipt"
            if versioned_receipt
            else "deployment-lock"
            if legacy_receipt
            else "unavailable"
        )

        config = self._checkpoint_config(merged)
        family = self._first(merged, "VLLM_ENGINE_MODEL_FAMILY") or str(
            config.get("model_type") or self._family_from_name(served_model)
        )
        architectures = config.get("architectures") or []
        architecture = self._first(merged, "VLLM_ENGINE_ARCHITECTURE") or (
            str(architectures[0]) if architectures else "unknown"
        )
        extra_args = self._extra_args(merged.get("VLLM_ENGINE_EXTRA_ARGS_JSON", ""))
        device_ids = self._device_ids(merged)
        accelerator_model = self._accelerator_model(hardware)
        graph_mode = self._graph_mode(merged)
        speculative = self._speculative(extra_args)
        capability_status = str(live_capability.get("status") or "")
        capability_detected = str(
            live_capability.get("detected_checkpoint_method") or ""
        )
        capability_resolved = str(live_capability.get("resolved_method") or "")
        capability_reason = str(
            live_capability.get("reason") or live_capability.get("status_reason") or ""
        )
        if live_capability:
            speculative_enabled = capability_status == "enabled"
            speculative_method = capability_resolved or "none"
        else:
            speculative_enabled = speculative[0]
            speculative_method = speculative[1]
        return RuntimeIdentity(
            status=status,
            source=source,
            collected_at=now,
            serving_available=bool(live_model),
            served_model=served_model or "unknown",
            checkpoint_family=family or "unknown",
            architecture=architecture,
            engine="vLLM-HUST",
            engine_version=self._first(merged, "VLLM_ENGINE_VERSION")
            or versions.get("stack_version_vllm_hust", "unknown"),
            plugin_version=self._first(merged, "VLLM_ENGINE_PLUGIN_VERSION")
            or versions.get("stack_version_vllm_ascend", "unknown"),
            accelerator_model=self._first(merged, "VLLM_ENGINE_ACCELERATOR_MODEL")
            or accelerator_model,
            device_ids=device_ids,
            device_count=len(device_ids),
            tensor_parallel_size=self._positive_int(merged.get("VLLM_ENGINE_TP_SIZE")),
            data_parallel_size=self._positive_int(extra_args.get("--data-parallel-size")) or 1,
            expert_parallel_enabled=self._bool(merged.get("VLLM_ENGINE_ENABLE_EXPERT_PARALLEL")),
            quantization=self._first(merged, "VLLM_ENGINE_QUANTIZATION") or "unknown",
            graph_mode=graph_mode,
            speculative_capability=capability_detected or self._first(
                merged, "VLLM_ENGINE_SPECULATIVE_CAPABILITY"
            ) or ("configured" if speculative[0] else "not-configured"),
            speculative_method=speculative_method,
            speculative_enabled=speculative_enabled,
            speculative_reason=capability_reason
            or self._first(merged, "VLLM_ENGINE_SPECULATIVE_REASON")
            or capability_detected
            or "not-configured",
        )

    def _read_receipt(self) -> dict[str, str]:
        path = self._settings.runtime_dir / "engine-deployment.lock.env"
        try:
            if not path.is_file():
                return {}
            values: dict[str, str] = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                if not key.startswith(("VLLM_ENGINE_", "DIGITAL_TWIN_", "ASCEND_")):
                    continue
                parsed = shlex.split(raw)
                values[key] = parsed[0] if parsed else ""
            return values
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _first(values: Mapping[str, str], *keys: str) -> str:
        return next((str(values.get(key) or "").strip() for key in keys if values.get(key)), "")

    @staticmethod
    def _positive_int(value: object) -> int | None:
        try:
            result = int(str(value))
        except (TypeError, ValueError):
            return None
        return result if result > 0 else None

    @staticmethod
    def _bool(value: object) -> bool | None:
        normalized = str(value or "").strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return None

    @staticmethod
    def _extra_args(raw: str) -> dict[str, str]:
        try:
            values = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return {}
        if not isinstance(values, list):
            return {}
        result: dict[str, str] = {}
        index = 0
        while index < len(values):
            item = str(values[index])
            if item.startswith("--"):
                result[item] = (
                    str(values[index + 1])
                    if index + 1 < len(values) and not str(values[index + 1]).startswith("--")
                    else "true"
                )
            index += 1
        return result

    @staticmethod
    def _speculative(args: Mapping[str, str]) -> tuple[bool, str]:
        raw = args.get("--speculative-config", "")
        if not raw:
            return False, "none"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return True, "configured"
        return True, str(payload.get("method") or "configured") if isinstance(payload, dict) else "configured"

    @staticmethod
    def _device_ids(values: Mapping[str, str]) -> tuple[str, ...]:
        raw = RuntimeIdentityProvider._first(
            values, "VLLM_ENGINE_NPU_DEVICES", "ASCEND_VISIBLE_DEVICES", "ASCEND_RT_VISIBLE_DEVICES"
        )
        return tuple(item.strip() for item in raw.split(",") if item.strip())

    @staticmethod
    def _graph_mode(values: Mapping[str, str]) -> str:
        if RuntimeIdentityProvider._bool(values.get("VLLM_ENGINE_ENFORCE_EAGER")) is True:
            return "eager"
        raw = str(values.get("VLLM_ENGINE_COMPILATION_CONFIG") or "")
        return "graph" if raw and raw not in {"{}", "null"} else "unknown"

    @staticmethod
    def _family_from_name(name: str) -> str:
        lowered = name.lower()
        for family in ("deepseek_v4", "deepseek_v3", "deepseek_v2", "glm", "minimax", "qwen"):
            if family.replace("_", "-") in lowered or family.replace("_", "") in lowered:
                return family
        return "unknown"

    @staticmethod
    def _accelerator_model(hardware: Mapping[str, str]) -> str:
        value = str(hardware.get("npu") or hardware.get("npu_host") or "")
        if "910" in value:
            return next((part for part in value.replace("·", " ").split() if "910" in part), "Ascend 910")
        return "Ascend NPU" if value else "unknown"

    @staticmethod
    def _checkpoint_config(values: Mapping[str, str]) -> dict[str, Any]:
        model_path = RuntimeIdentityProvider._first(
            values, "VLLM_ENGINE_MODEL_PATH", "VLLM_HUST_MODEL"
        )
        if not model_path:
            return {}
        try:
            path = Path(model_path) / "config.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


def render_runtime_identity_answer(identity: RuntimeIdentity, *, english: bool = False) -> str:
    """Render only typed public fields; never delegate runtime facts to model memory."""
    if identity.status == "unknown":
        return (
            "The live runtime identity is currently unavailable. I will not guess the model, "
            "hardware, or engine; please retry after the serving endpoint recovers."
            if english
            else "当前无法读取实时运行时身份。我不会猜测模型、硬件或引擎；请在推理服务恢复后重试。"
        )
    if not identity.serving_available:
        configured_devices = (
            f"{identity.device_count}× {identity.accelerator_model}"
            if identity.device_count
            else f"unknown× {identity.accelerator_model}"
        )
        configured_tp = identity.tensor_parallel_size or "unknown"
        configured_dp = identity.data_parallel_size or "unknown"
        configured_ep = (
            "on"
            if identity.expert_parallel_enabled is True
            else "off"
            if identity.expert_parallel_enabled is False
            else "unknown"
        )
        configured_speculative = (
            f"enabled ({identity.speculative_method})"
            if identity.speculative_enabled
            else f"not enabled ({identity.speculative_reason})"
        )
        configured_accelerator_lower = identity.accelerator_model.lower()
        configured_is_ascend = any(
            marker in configured_accelerator_lower
            for marker in ("ascend", "910", "npu")
        )
        if english:
            platform_relation = (
                "the Ascend plugin maps engine execution to the NPU runtime"
                if configured_is_ascend
                else "the selected platform backend maps engine execution to the accelerator runtime"
            )
            return (
                f"The configured deployment target is **{identity.served_model}** with "
                f"{identity.engine}, but the live serving endpoint is currently unavailable "
                f"or has not been verified. The recorded target describes "
                f"{identity.checkpoint_family} / {identity.architecture} on {configured_devices}, "
                f"with TP={configured_tp}, DP={configured_dp}, EP={configured_ep}, "
                f"quantization={identity.quantization}, execution={identity.graph_mode}, and "
                f"speculative decoding {configured_speculative}. In the configured architecture, "
                f"SAGE orchestrates the application workflow, vLLM-HUST is the serving engine, "
                f"and {platform_relation}. These are deployment configuration facts from "
                f"{identity.source}, not evidence that the engine is currently serving. "
                f"Collected at {identity.collected_at}."
            )
        configured_speculative_zh = (
            f"已启用（{identity.speculative_method}）"
            if identity.speculative_enabled
            else f"未启用（{identity.speculative_reason}）"
        )
        configured_platform_relation_zh = (
            "Ascend 插件把引擎执行映射到 NPU 运行时"
            if configured_is_ascend
            else "所选平台后端把引擎执行映射到加速器运行时"
        )
        return (
            f"当前无法从实时 serving endpoint 确认推理引擎在线。部署锁定目标是 "
            f"**{identity.served_model}**，引擎类型为 {identity.engine}；记录的检查点族/架构为 "
            f"{identity.checkpoint_family} / {identity.architecture}，目标硬件配置为 {configured_devices}，"
            f"TP={configured_tp}、DP={configured_dp}、EP={configured_ep}，量化方式为 "
            f"{identity.quantization}，执行模式为 {identity.graph_mode}，记录中的 speculative "
            f"decoding {configured_speculative_zh}。在该配置架构中，SAGE 组织应用工作流，"
            f"vLLM-HUST 是推理服务引擎，{configured_platform_relation_zh}。"
            f"这些信息来自 {identity.source} 的部署配置，"
            f"不代表引擎当前正在提供推理；当前状态是不可用或尚未验证。"
            f"采集时间为 {identity.collected_at}。"
        )
    devices = f"{identity.device_count}× {identity.accelerator_model}" if identity.device_count else "unknown"
    tp = identity.tensor_parallel_size or "unknown"
    dp = identity.data_parallel_size or "unknown"
    ep = "on" if identity.expert_parallel_enabled else "off" if identity.expert_parallel_enabled is False else "unknown"
    speculative = (
        f"enabled ({identity.speculative_method})"
        if identity.speculative_enabled
        else f"not enabled ({identity.speculative_reason})"
    )
    accelerator_lower = identity.accelerator_model.lower()
    is_ascend = any(
        marker in accelerator_lower for marker in ("ascend", "910", "npu")
    )
    if english:
        platform_relation = (
            "its Ascend plugin maps execution to the NPU runtime"
            if is_ascend
            else "the selected platform backend maps execution to the accelerator runtime"
        )
        return (
            f"The current backend serves **{identity.served_model}** with {identity.engine}. "
            f"Checkpoint family/architecture: {identity.checkpoint_family} / {identity.architecture}. "
            f"Hardware: {devices}; TP={tp}, DP={dp}, EP={ep}. Quantization: "
            f"{identity.quantization}; execution: {identity.graph_mode}; speculative decoding: "
            f"{speculative}. SAGE orchestrates the application workflow, vLLM-HUST provides "
            f"the serving engine, and {platform_relation}. "
            f"This was collected from {identity.source} at {identity.collected_at}."
        )
    speculative_zh = (
        f"已启用（{identity.speculative_method}）"
        if identity.speculative_enabled
        else f"未启用（{identity.speculative_reason}）"
    )
    platform_relation_zh = (
        "Ascend 插件将执行映射到 NPU 运行时"
        if is_ascend
        else "所选平台后端将执行映射到加速器运行时"
    )
    return (
        f"当前后端实际提供的是 **{identity.served_model}**，推理引擎为 {identity.engine}。"
        f"检查点族/架构是 {identity.checkpoint_family} / {identity.architecture}；"
        f"硬件为 {devices}，TP={tp}、DP={dp}、EP={ep}。量化方式为 {identity.quantization}，"
        f"执行模式为 {identity.graph_mode}，speculative decoding {speculative_zh}。"
        f"SAGE 负责组织应用工作流，vLLM-HUST 提供推理服务引擎，{platform_relation_zh}。"
        f"这些信息采集自 {identity.source}，时间为 {identity.collected_at}。"
    )
