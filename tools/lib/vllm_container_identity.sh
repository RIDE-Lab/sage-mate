#!/usr/bin/env bash
# Stable deployment-role identity shared by lock, launcher, and verifier.

default_vllm_engine_container_name() {
    local repo_root="$1"
    local login_name="${USER:-}"
    [[ -n "$login_name" ]] || login_name="$(id -un 2>/dev/null || printf 'user')"
    local suffix="${login_name}-$(basename "$repo_root")"
    suffix="$(printf '%s' "$suffix" | tr -cs '[:alnum:]_.-' '-')"
    printf 'sage-mate-vllm-%s\n' "$suffix"
}

normalize_vllm_engine_container_name() {
    local repo_root="$1"
    if [[ -z "${VLLM_ENGINE_CONTAINER:-}" ]]; then
        VLLM_ENGINE_CONTAINER="$(default_vllm_engine_container_name "$repo_root")"
    fi
    export VLLM_ENGINE_CONTAINER
}
