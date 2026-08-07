#!/usr/bin/env python3
"""Resolve a Sage Mate vLLM-HUST model path with optional auto-download.

This helper is designed for multi-machine deployments:
- Resolve against candidate families in order (default: GLM → DeepSeek → MiniMax → Qwen).
- Pick the largest usable model from the selected families, preferring larger footprint first;
  family order is used when sizes are equal.
- If no local model is available, optionally auto-download from Hugging Face.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from huggingface_hub import snapshot_download


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "", separator: str = ",") -> list[str]:
    raw = os.environ.get(name, default) or ""
    return [value.strip() for value in raw.split(separator) if value.strip()]


@dataclass
class Candidate:
    path: Path
    family: str
    family_rank: int
    served_name: str
    size_bytes: int
    source: str
    model_id: str


WEIGHT_EXTS = {".safetensors", ".bin", ".safetensor", ".pt", ".pth"}
WEIGHT_INDEX_EXTS = {".index.json", ".index"}
CONFIG_FILENAME = "config.json"
IGNORE_DIRS = {
    ".cache",
    ".git",
    ".locks",
    ".xet",
    "xet",
    "exec",
    "runtime-runc",
    "libnetwork",
}


def _as_list(value: str | None, fallback: str) -> list[str]:
    if value is None or not value.strip():
        return [item.strip() for item in fallback.split(",") if item.strip()]
    return [item.strip() for item in value.split(",") if item.strip()]


def _shell_quote(item: str) -> str:
    return shlex.quote(item)


def _readable_family_order() -> list[str]:
    order_raw = os.environ.get("VLLM_ENGINE_MODEL_FAMILY_ORDER", "glm,deepseek,minimax,qwen")
    return [item.strip() for item in order_raw.split(",") if item.strip()]


def _family_patterns(family: str) -> list[str]:
    env_key = f"VLLM_ENGINE_FAMILY_PATTERNS_{family.upper()}"
    default_map = {
        "GLM": "glm",
        "DEEPSEEK": "deepseek",
        "MINIMAX": "minimax,minimaxi",
        "QWEN": "qwen",
    }
    default_patterns = default_map.get(family.upper(), family.lower())
    return [item.lower() for item in _env_list(env_key, default_patterns)]


def _family_remote_candidates(family: str) -> list[str]:
    env_key = f"VLLM_ENGINE_FAMILY_REMOTE_{family.upper()}_CANDIDATES"
    defaults = {
        "GLM": "THUDM/glm-4-9b-chat",
        "DEEPSEEK": "deepseek-ai/DeepSeek-V2.5,deepseek-ai/DeepSeek-V2",
        "MINIMAX": "",
        "QWEN": "Qwen/Qwen3___5-35B-A3B,Qwen/Qwen3-32B",
    }
    return _as_list(os.environ.get(env_key), defaults.get(family.upper(), ""))


def _scan_roots() -> list[Path]:
    roots = _env_list(
        "VLLM_ENGINE_MODEL_ROOTS",
        ",".join(
            [
                "/data/shared_models/modelscope_cache",
                "/data/shared_models/huggingface_cache/hub",
                "/data/shared_models/huggingface_cache",
                "/data/shared_models",
            ]
        ),
        ",",
    )
    return [Path(root).expanduser() for root in roots]


def _looks_like_model_directory(path: Path) -> bool:
    if not path.is_dir():
        return False
    if not (path / CONFIG_FILENAME).is_file():
        return False
    return _has_model_weights(path)


def _has_model_weights(path: Path) -> bool:
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        suffix = child.suffix.lower()
        name = child.name.lower()
        if suffix in WEIGHT_EXTS and not name.endswith(".txt"):
            return True
        if any(name.endswith(ext) for ext in WEIGHT_INDEX_EXTS):
            # index-only metadata can appear with shards elsewhere
            # keep scanning siblings and siblings' children below.
            continue
    return False


def _dir_weight_bytes(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        name = child.name.lower()
        if child.suffix.lower() in WEIGHT_EXTS or any(name.endswith(ext) for ext in WEIGHT_INDEX_EXTS):
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def _infer_served_model_name(model_dir: Path) -> str:
    parts = model_dir.parts
    for idx in range(len(parts) - 1, -1, -1):
        part = parts[idx]
        if part.startswith("models--"):
            model_slug = part[8:]
            return model_slug.replace("--", "/")
    if len(parts) >= 2:
        parent = parts[-2]
        child = parts[-1]
        return f"{parent}/{child}" if "snapshots" not in {parent, child} else child
    return model_dir.name


def _collect_candidates_for_family(family: str, family_rank: int, root_paths: list[Path]) -> list[Candidate]:
    patterns = _family_patterns(family)
    candidates: list[Candidate] = []
    for root in root_paths:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Keep scan bounded and deterministic, while avoiding known noisy trees.
            lower_dirpath = dirpath.lower()
            if any(needle in lower_dirpath for needle in IGNORE_DIRS):
                dirnames[:] = []
                continue

            depth = len(Path(dirpath).relative_to(root).parts)
            if depth > int(os.environ.get("VLLM_ENGINE_MODEL_SCAN_MAX_DEPTH", "8")):
                dirnames[:] = []
                continue

            if CONFIG_FILENAME not in filenames:
                continue

            candidate_path = Path(dirpath)
            candidate_name_lower = candidate_path.as_posix().lower()
            if not any(pattern in candidate_name_lower for pattern in patterns):
                continue

            try:
                valid = _looks_like_model_directory(candidate_path)
            except OSError:
                continue
            if not valid:
                continue

            try:
                size_bytes = _dir_weight_bytes(candidate_path)
            except OSError:
                size_bytes = 0
            served_name = _infer_served_model_name(candidate_path)
            candidates.append(
                Candidate(
                    path=candidate_path,
                    family=family,
                    family_rank=family_rank,
                    served_name=served_name,
                    size_bytes=size_bytes,
                    source="local",
                    model_id=served_name,
                )
            )
    return candidates


def _pick_best(candidates: Iterable[Candidate]) -> Candidate | None:
    sorted_candidates = sorted(
        candidates,
        key=lambda item: (-item.size_bytes, item.family_rank, len(item.path.as_posix())),
    )
    return sorted_candidates[0] if sorted_candidates else None


def _download_family_model(family: str, family_rank: int) -> Candidate | None:
    if not _env_flag("VLLM_ENGINE_AUTO_DOWNLOAD", True):
        return None

    remote_candidates = _family_remote_candidates(family)
    if not remote_candidates:
        return None

    download_roots = _scan_roots()
    cache_root: Path | None = None
    for root in download_roots:
        if root.is_dir() and os.access(root, os.W_OK):
            cache_root = root
            break
    if cache_root is None:
        for root in download_roots:
            candidate = Path(root)
            try:
                candidate.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            if os.access(candidate, os.W_OK):
                cache_root = candidate
                break
    if cache_root is None:
        return None

    for model_id in remote_candidates:
        target_dir = cache_root / "models--" / re.sub(r"[/\\\\]", "--", model_id)
        print(f"[resolver] downloading missing model '{model_id}' -> {target_dir}", file=sys.stderr)
        try:
            local_dir = target_dir / "snapshots" / "main"
            local_dir.mkdir(parents=True, exist_ok=True)
            snapshot_download(
                repo_id=model_id,
                local_dir=str(local_dir),
                local_dir_use_symlinks=False,
                ignore_patterns=["*.md", "*.txt"],
            )
        except Exception as exc:
            print(f"[resolver] download failed for {model_id}: {exc}", file=sys.stderr)
            continue
        candidate_path = local_dir
        if not _looks_like_model_directory(candidate_path):
            # Some repos only export a model snapshot under the requested commit dir.
            try:
                snapshot_root = target_dir / "snapshots"
                if snapshot_root.is_dir():
                    for item in snapshot_root.iterdir():
                        if item.is_dir() and (item / CONFIG_FILENAME).is_file():
                            candidate_path = item
                            if _looks_like_model_directory(candidate_path):
                                break
            except OSError:
                pass
        if not _looks_like_model_directory(candidate_path):
            print(f"[resolver] downloaded directory is not ready: {candidate_path}", file=sys.stderr)
            continue
        return Candidate(
            path=candidate_path,
            family=family,
            family_rank=family_rank,
            served_name=model_id.split("/")[-1] if "/" in model_id else model_id,
            size_bytes=_dir_weight_bytes(candidate_path),
            source="download",
            model_id=model_id,
        )
    return None


def _emit_env_lines(candidate: Candidate) -> None:
    print(f"VLLM_ENGINE_MODEL_PATH={_shell_quote(str(candidate.path))}")
    print(f"VLLM_ENGINE_SERVED_MODEL_NAME={_shell_quote(candidate.served_name)}")
    print(f"VLLM_ENGINE_ACTUAL_MODEL_ID={_shell_quote(candidate.model_id)}")
    print(f"VLLM_ENGINE_MODEL_SOURCE={_shell_quote(candidate.source)}")
    print(f"VLLM_ENGINE_MODEL_FAMILY={_shell_quote(candidate.family)}")


def main() -> int:
    families = _readable_family_order()
    family_rank_map = {family: idx for idx, family in enumerate(families)}
    roots = _scan_roots()

    local_candidates: list[Candidate] = []
    for family in families:
        family = family.strip()
        if not family:
            continue
        local_candidates.extend(
            _collect_candidates_for_family(family, family_rank_map[family], roots)
        )

    preferred_families = [family for family in families if family.strip().lower() != "qwen"]
    preferred_set = {family.strip().lower() for family in preferred_families}
    preferred_candidates = [item for item in local_candidates if item.family.strip().lower() in preferred_set]

    selected: Candidate | None = None
    if preferred_candidates:
        selected = _pick_best(preferred_candidates)
    if selected is None:
        selected = _pick_best(local_candidates)

    if selected is None:
        fallback_families = preferred_families if preferred_families else families
        for family in fallback_families:
            family = family.strip()
            if not family:
                continue
            selected = _download_family_model(family, family_rank_map[family])
            if selected:
                break

        if selected is None and "qwen" in {item.strip().lower() for item in families}:
            qwen_family = next(item for item in families if item.strip().lower() == "qwen")
            selected = _download_family_model(qwen_family, family_rank_map[qwen_family])


    if selected is None:
        print("[resolver] no local model found and auto-download did not produce a candidate.", file=sys.stderr)
        return 2

    _emit_env_lines(selected)
    print(f"[resolver] selected {selected.family} model: {selected.model_id}", file=sys.stderr)
    print(
        f"[resolver] source={selected.source} family={selected.family} path={selected.path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
