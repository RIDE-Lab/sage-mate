#!/usr/bin/env bash
# Shared normalization for model provenance used by the deployment lock and launcher.

normalize_vllm_model_metadata() {
    local repo_root="$1"
    local python_bin="$2"
    local model_path="$3"
    local served_name="$4"
    local inspector="$repo_root/tools/inspect_vllm_model.py"
    local metadata

    [[ -n "$model_path" && -n "$served_name" ]] || return 0
    [[ -x "$python_bin" && -x "$inspector" ]] || return 0

    if [[ -z "${VLLM_ENGINE_ACTUAL_MODEL_ID:-}" ]] ||
        [[ -z "${VLLM_ENGINE_MODEL_SOURCE:-}" ]] ||
        [[ -z "${VLLM_ENGINE_MODEL_FAMILY:-}" ]] ||
        [[ -z "${VLLM_ENGINE_ARCHITECTURE:-}" ]]; then
        metadata="$("$python_bin" "$inspector" \
            --model-path "$model_path" \
            --served-name "$served_name")"
        eval "$metadata"
    fi

    export VLLM_ENGINE_ACTUAL_MODEL_ID="${VLLM_ENGINE_ACTUAL_MODEL_ID:-$served_name}"
    export VLLM_ENGINE_MODEL_SOURCE="${VLLM_ENGINE_MODEL_SOURCE:-configured}"
    export VLLM_ENGINE_MODEL_FAMILY="${VLLM_ENGINE_MODEL_FAMILY:-unknown}"
    export VLLM_ENGINE_ARCHITECTURE="${VLLM_ENGINE_ARCHITECTURE:-unknown}"
}
