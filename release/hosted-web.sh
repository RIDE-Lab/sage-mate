#!/usr/bin/env bash
# One-shot hosted/web installer for Sage Mate.
#
# This file is intentionally standalone enough to publish as a GitHub Release
# asset. It clones/updates the repo, configures hosted/web safety defaults, then
# delegates installation and runtime management to quickstart.sh/manage.sh.

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_url="${FACULTY_TWIN_REPO_URL:-git@github.com:SAGE-Research/sage-mate.git}"
https_repo_url="${FACULTY_TWIN_HTTPS_REPO_URL:-https://github.com/SAGE-Research/sage-mate.git}"
branch="${FACULTY_TWIN_BRANCH:-main}"
parent_dir="${FACULTY_TWIN_PARENT_DIR:-$HOME}"
repo_dir="${FACULTY_TWIN_DIR:-$parent_dir/sage-mate}"
model_preset="${FACULTY_TWIN_MODEL_PRESET:-auto}"
model_override="${FACULTY_TWIN_MODEL:-}"
served_model_override="${FACULTY_TWIN_SERVED_MODEL_NAME:-}"
accelerator="${FACULTY_TWIN_ACCELERATOR:-auto}"
tp_override="${FACULTY_TWIN_TENSOR_PARALLEL_SIZE:-${VLLM_NVIDIA_TENSOR_PARALLEL_SIZE:-${VLLM_ENGINE_TP_SIZE:-}}}"
npu_devices="${FACULTY_TWIN_NPU_DEVICES:-${VLLM_ENGINE_NPU_DEVICES:-${ASCEND_RT_VISIBLE_DEVICES:-}}}"
public_hostname="${FACULTY_TWIN_PUBLIC_HOSTNAME:-}"
tunnel_name="${FACULTY_TWIN_TUNNEL_NAME:-}"
runtime_repo_url="${FACULTY_TWIN_RUNTIME_REPO_URL:-https://github.com/Qixin-Gaoke/sage-mate-runtime-private.git}"
runtime_repo_branch="${FACULTY_TWIN_RUNTIME_REPO_BRANCH:-main}"
encrypted_secrets_file="${FACULTY_TWIN_ENCRYPTED_SECRETS_FILE:-}"
secrets_key_file="${FACULTY_TWIN_SECRETS_KEY_FILE:-}"
use_encrypted_secrets=true
start_services=true
with_tunnel=true
yes=false
hf_endpoint_explicit=false

usage() {
    cat <<'EOF'
Usage: hosted-web.sh [options]

Options:
  --dir PATH                  Install/update repo at PATH.
  --repo-url URL              Primary git URL. Defaults to SSH intellistream repo.
  --branch NAME               Git branch/ref. Defaults to main.
  --accelerator KIND          auto|nvidia|ascend|none. Defaults to auto.
  --model-preset PRESET       auto|qwen3-32b-awq|qwen3-14b-awq|qwen3-32b|qwen3-next-80b-awq|qwen2.5-14b-awq.
  --model MODEL_OR_PATH       Explicit HF model id or local model path.
  --served-model-name NAME    Served OpenAI model name. Defaults to model value.
  --tensor-parallel-size N    Override tensor parallel size.
  --npu-devices CSV           Ascend device IDs. Defaults to auto-selecting verified idle devices.
  --public-hostname HOSTNAME  Public Cloudflare hostname; required with --with-tunnel.
  --tunnel-name NAME          Cloudflare tunnel name to create/reuse.
  --runtime-repo-url URL      Private Faculty Twin runtime-data repository.
  --runtime-repo-branch NAME  Runtime-data branch. Defaults to main.
  --encrypted-secrets PATH    OpenSSL-encrypted env bundle. Default: release/secrets.env.enc.
  --secrets-key-file PATH     File containing decrypt passphrase/key material.
  --no-secrets                Skip encrypted release secrets even if present.
  --with-tunnel               Install/start Cloudflare tunnel service. Default.
  --no-tunnel                 Do not configure/start Cloudflare tunnel.
  --no-start                  Install/configure only; do not start services.
  --yes                       Non-interactive mode.
  -h, --help                  Show this help.

Environment:
  FACULTY_TWIN_PARENT_DIR, FACULTY_TWIN_DIR, FACULTY_TWIN_ACCELERATOR,
  FACULTY_TWIN_MODEL_PRESET,
  FACULTY_TWIN_MODEL, FACULTY_TWIN_SERVED_MODEL_NAME,
  FACULTY_TWIN_NPU_DEVICES,
  FACULTY_TWIN_PUBLIC_HOSTNAME, FACULTY_TWIN_TUNNEL_NAME, HF_ENDPOINT,
  FACULTY_TWIN_PREDOWNLOAD_SOURCE=auto|huggingface|hf-direct|modelscope,
  FACULTY_TWIN_HF_DIRECT_USE_TOKEN=1 for private direct downloads,
  FACULTY_TWIN_ENCRYPTED_SECRETS_FILE, FACULTY_TWIN_SECRETS_KEY_FILE,
  FACULTY_TWIN_SECRETS_PASSPHRASE, HF_TOKEN/HUGGING_FACE_HUB_TOKEN are honored when present.
EOF
}

log() { printf '[hosted-web] %s\n' "$*"; }
warn() { printf '[hosted-web] %s\n' "$*" >&2; }
fail() { printf '[hosted-web] ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)
            [[ $# -ge 2 ]] || fail "--dir requires a value"
            repo_dir="$2"
            shift 2
            ;;
        --repo-url)
            [[ $# -ge 2 ]] || fail "--repo-url requires a value"
            repo_url="$2"
            shift 2
            ;;
        --branch)
            [[ $# -ge 2 ]] || fail "--branch requires a value"
            branch="$2"
            shift 2
            ;;
        --accelerator)
            [[ $# -ge 2 ]] || fail "--accelerator requires a value"
            accelerator="$2"
            shift 2
            ;;
        --model-preset)
            [[ $# -ge 2 ]] || fail "--model-preset requires a value"
            model_preset="$2"
            shift 2
            ;;
        --model)
            [[ $# -ge 2 ]] || fail "--model requires a value"
            model_override="$2"
            shift 2
            ;;
        --served-model-name)
            [[ $# -ge 2 ]] || fail "--served-model-name requires a value"
            served_model_override="$2"
            shift 2
            ;;
        --tensor-parallel-size)
            [[ $# -ge 2 ]] || fail "--tensor-parallel-size requires a value"
            tp_override="$2"
            shift 2
            ;;
        --npu-devices)
            [[ $# -ge 2 ]] || fail "--npu-devices requires a value"
            npu_devices="$2"
            shift 2
            ;;
        --public-hostname)
            [[ $# -ge 2 ]] || fail "--public-hostname requires a value"
            public_hostname="$2"
            shift 2
            ;;
        --tunnel-name)
            [[ $# -ge 2 ]] || fail "--tunnel-name requires a value"
            tunnel_name="$2"
            shift 2
            ;;
        --runtime-repo-url)
            [[ $# -ge 2 ]] || fail "--runtime-repo-url requires a value"
            runtime_repo_url="$2"
            shift 2
            ;;
        --runtime-repo-branch)
            [[ $# -ge 2 ]] || fail "--runtime-repo-branch requires a value"
            runtime_repo_branch="$2"
            shift 2
            ;;
        --encrypted-secrets)
            [[ $# -ge 2 ]] || fail "--encrypted-secrets requires a value"
            encrypted_secrets_file="$2"
            shift 2
            ;;
        --secrets-key-file)
            [[ $# -ge 2 ]] || fail "--secrets-key-file requires a value"
            secrets_key_file="$2"
            shift 2
            ;;
        --no-secrets)
            use_encrypted_secrets=false
            shift
            ;;
        --with-tunnel)
            with_tunnel=true
            shift
            ;;
        --no-tunnel)
            with_tunnel=false
            shift
            ;;
        --no-start)
            start_services=false
            shift
            ;;
        --yes|-y)
            yes=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown option: $1"
            ;;
    esac
done

python_bin="${PYTHON_BIN:-}"

detect_python() {
    if [[ -n "$python_bin" && -x "$python_bin" ]]; then
        printf '%s\n' "$python_bin"
        return 0
    fi
    local candidate
    for candidate in \
        "$HOME/miniforge3/envs/vllm-hust-dev/bin/python" \
        "$HOME/miniconda3/envs/vllm-hust-dev/bin/python" \
        "$HOME/anaconda3/envs/vllm-hust-dev/bin/python" \
        "$(command -v python3 2>/dev/null || true)" \
        "$(command -v python 2>/dev/null || true)"; do
        if [[ -n "$candidate" && -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

gpu_count() {
    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi -L 2>/dev/null | wc -l | awk '{print $1+0}'
    else
        echo 0
    fi
}

min_gpu_mem_mib() {
    if command -v nvidia-smi >/dev/null 2>&1; then
        nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | awk '
            NR == 1 { min = $1 }
            $1 < min { min = $1 }
            END { print min+0 }
        '
    else
        echo 0
    fi
}

default_nvidia_tensor_parallel_size() {
    local gpus="${1:-$(gpu_count)}" min_mem="${2:-$(min_gpu_mem_mib)}"
    if [[ "$gpus" -ge 2 && "$min_mem" -ge 70000 ]]; then
        echo 2
    else
        echo 1
    fi
}

npu_count() {
    if command -v npu-smi >/dev/null 2>&1 && npu-smi info >/dev/null 2>&1; then
        npu-smi info 2>/dev/null | awk '/^[[:space:]]*[0-9]+[[:space:]]+/ { count++ } END { print count+0 }'
    else
        echo 0
    fi
}

detect_accelerator() {
    case "$accelerator" in
        auto|nvidia|ascend|none) ;;
        *) fail "--accelerator must be one of: auto, nvidia, ascend, none" ;;
    esac
    if [[ "$accelerator" != "auto" ]]; then
        return 0
    fi
    if [[ "$(gpu_count)" -gt 0 ]]; then
        accelerator="nvidia"
    elif [[ "$(npu_count)" -gt 0 ]]; then
        accelerator="ascend"
    else
        accelerator="none"
    fi
}

set_env_kv() {
    local env_file="$1" key="$2" value="$3"
    "$python_bin" - "$env_file" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
prefix = f"{key}="
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out = []
updated = False
for line in lines:
    if line.strip().startswith(prefix):
        out.append(f"{key}={value}")
        updated = True
    else:
        out.append(line)
if not updated:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

merge_env_file() {
    local source_env="$1" target_env="$2"
    local line key value
    [[ -f "$source_env" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == *"="* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        key="${key// /}"
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        set_env_kv "$target_env" "$key" "$value"
    done < "$source_env"
}

apply_encrypted_release_secrets() {
    local env_file="$1"
    local default_file="$repo_dir/release/secrets.env.enc"
    local enc_file="${encrypted_secrets_file:-$default_file}"
    local tmp_file pass_args=()

    $use_encrypted_secrets || return 0
    [[ -f "$enc_file" ]] || return 0

    command -v openssl >/dev/null 2>&1 || fail "openssl is required to decrypt $enc_file"
    if [[ -n "$secrets_key_file" ]]; then
        [[ -f "$secrets_key_file" ]] || fail "secrets key file not found: $secrets_key_file"
        pass_args=(-pass "file:$secrets_key_file")
    elif [[ -n "${FACULTY_TWIN_SECRETS_PASSPHRASE:-}" ]]; then
        pass_args=(-pass "env:FACULTY_TWIN_SECRETS_PASSPHRASE")
    else
        warn "encrypted secrets file exists but no key was provided; continuing without decrypting it"
        warn "  file: $enc_file"
        warn "  set FACULTY_TWIN_SECRETS_KEY_FILE or pass --secrets-key-file"
        return 0
    fi

    tmp_file=$(mktemp)
    chmod 0600 "$tmp_file"
    if ! openssl enc -d -aes-256-cbc -pbkdf2 -in "$enc_file" -out "$tmp_file" "${pass_args[@]}" 2>/tmp/faculty_twin_secrets_decrypt.err; then
        rm -f "$tmp_file"
        fail "could not decrypt encrypted release secrets"
    fi
    merge_env_file "$tmp_file" "$env_file"
    rm -f "$tmp_file"
    log "applied encrypted release secrets from $(basename "$enc_file")"
}

apply_initial_release_secrets() {
    local default_file="$script_dir/secrets.env.enc"
    local enc_file="${encrypted_secrets_file:-$default_file}"
    local tmp_file pass_args=()

    $use_encrypted_secrets || return 0
    [[ -f "$enc_file" ]] || return 0
    command -v openssl >/dev/null 2>&1 || return 0

    if [[ -n "$secrets_key_file" ]]; then
        [[ -f "$secrets_key_file" ]] || return 0
        pass_args=(-pass "file:$secrets_key_file")
    elif [[ -n "${FACULTY_TWIN_SECRETS_PASSPHRASE:-}" ]]; then
        pass_args=(-pass "env:FACULTY_TWIN_SECRETS_PASSPHRASE")
    else
        return 0
    fi

    tmp_file=$(mktemp)
    chmod 0600 "$tmp_file"
    if ! openssl enc -d -aes-256-cbc -pbkdf2 -in "$enc_file" -out "$tmp_file" "${pass_args[@]}" 2>/tmp/faculty_twin_initial_secrets_decrypt.err; then
        rm -f "$tmp_file"
        return 0
    fi
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == *"="* ]] || continue
        local key="${line%%=*}" value="${line#*=}"
        key="${key// /}"
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        case "$key" in
            GITHUB_TOKEN|GH_TOKEN|HF_TOKEN|HUGGING_FACE_HUB_TOKEN|CLOUDFLARE_API_TOKEN|CLOUDFLARE_GLOBAL_API_KEY)
                if [[ -z "${!key:-}" ]]; then
                    export "$key=$value"
                fi
                ;;
        esac
    done < "$tmp_file"
    rm -f "$tmp_file"
    log "loaded encrypted release secrets needed before clone"
}

prepare_qwen3_next_template() {
    local runtime_dir="$1"
    local template_path="$runtime_dir/qwen3-next-chat-template.jinja"
    mkdir -p "$runtime_dir"
    if [[ -s "$template_path" ]]; then
        printf '%s\n' "$template_path"
        return 0
    fi
    "$python_bin" - "$template_path" <<'PY'
from pathlib import Path
from urllib.request import urlopen
import json
import os
import sys

target = Path(sys.argv[1])
endpoint = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
url = f"{endpoint}/Qwen/Qwen3-Next-80B-A3B-Instruct/resolve/main/tokenizer_config.json"
with urlopen(url, timeout=60) as response:
    payload = json.loads(response.read().decode("utf-8"))
template = payload.get("chat_template")
if not template:
    raise SystemExit("official Qwen3-Next chat_template missing")
target.write_text(template, encoding="utf-8")
PY
    chmod 0600 "$template_path"
    printf '%s\n' "$template_path"
}

cloudflared_bin() {
    local candidate
    for candidate in \
        "${CLOUDFLARED_BIN:-}" \
        "$HOME/.local/bin/cloudflared" \
        "/usr/local/bin/cloudflared" \
        "/usr/bin/cloudflared" \
        "$(command -v cloudflared 2>/dev/null || true)"; do
        if [[ -n "$candidate" && -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

tunnel_id_for_name() {
    local cf_bin="$1" name="$2"
    "$cf_bin" tunnel list 2>/dev/null | awk -v name="$name" '$2 == name { print $1; exit }'
}

configure_cloudflare_tunnel() {
    local runtime_dir="$1"
    [[ -n "$public_hostname" ]] || fail "--with-tunnel requires --public-hostname or FACULTY_TWIN_PUBLIC_HOSTNAME"

    local cf_bin tunnel_id config_path credentials_path
    local tunnel_origin_url="${TUNNEL_ORIGIN_URL:-http://${APP_HEALTH_HOST:-${APP_HOST:-127.0.0.1}}:${APP_PORT:-55601}}"
    cf_bin=$(cloudflared_bin) || fail "cloudflared is required for --with-tunnel"
    [[ -f "${TUNNEL_ORIGIN_CERT:-$HOME/.cloudflared/cert.pem}" ]] || {
        fail "Cloudflare origin cert missing. Run cloudflared tunnel login, or use --no-tunnel."
    }

    tunnel_id=$(tunnel_id_for_name "$cf_bin" "$tunnel_name")
    if [[ -z "$tunnel_id" ]]; then
        log "creating Cloudflare tunnel: $tunnel_name"
        "$cf_bin" tunnel create "$tunnel_name" >/tmp/faculty_twin_tunnel_create.log
        tunnel_id=$(tunnel_id_for_name "$cf_bin" "$tunnel_name")
    else
        log "reusing Cloudflare tunnel: $tunnel_name"
    fi
    [[ -n "$tunnel_id" ]] || fail "could not resolve Cloudflare tunnel id for $tunnel_name"

    credentials_path="$HOME/.cloudflared/$tunnel_id.json"
    [[ -f "$credentials_path" ]] || fail "Cloudflare tunnel credentials file missing for $tunnel_name"

    mkdir -p "$runtime_dir/cloudflared"
    config_path="$runtime_dir/cloudflared/config.yml"
    umask 077
    cat >"$config_path" <<EOF
tunnel: $tunnel_id
credentials-file: $credentials_path

ingress:
  - hostname: $public_hostname
    service: $tunnel_origin_url
  - service: http_status:404
EOF

    "$cf_bin" tunnel route dns --overwrite-dns "$tunnel_id" "$public_hostname" >/tmp/faculty_twin_tunnel_route.log
    set_env_kv "$env_file" TUNNEL_CONFIG_PATH "$config_path"
    set_env_kv "$env_file" DIGITAL_TWIN_HOMEPAGE_PUBLIC_URL "https://$public_hostname/"
    log "Cloudflare tunnel ready for https://$public_hostname/"
}

predownload_model_if_needed() {
    [[ "$accelerator" == "nvidia" ]] || return 0
    case "$model_preset" in
        qwen3-next-80b-awq|qwen3-32b|qwen3-32b-awq|qwen3-14b-awq) ;;
        *) return 0 ;;
    esac
    [[ "${FACULTY_TWIN_PREDOWNLOAD_MODEL:-1}" != "0" ]] || return 0
    [[ "$model" != /* ]] || return 0

    local predownload_source="${FACULTY_TWIN_PREDOWNLOAD_SOURCE:-auto}"
    case "$predownload_source" in
        auto|huggingface|hf-direct|modelscope) ;;
        *) fail "FACULTY_TWIN_PREDOWNLOAD_SOURCE must be auto, huggingface, hf-direct, or modelscope" ;;
    esac

    if [[ "$predownload_source" == "modelscope" ]]; then
        predownload_model_from_modelscope
        return 0
    fi
    if [[ "$predownload_source" == "hf-direct" ]]; then
        predownload_model_from_hf_resolve_aria2
        return 0
    fi

    if [[ "$predownload_source" == "auto" || "$predownload_source" == "huggingface" ]]; then
        export HF_HUB_OFFLINE=0
        set_env_kv "$env_file" HF_HUB_OFFLINE "0"
        if [[ "${FACULTY_TWIN_PREDOWNLOAD_HF_TRANSFER:-1}" != "0" ]]; then
            if "$python_bin" -c "import hf_transfer" >/dev/null 2>&1 || "$python_bin" -m pip install hf_transfer; then
                export HF_HUB_ENABLE_HF_TRANSFER=1
                set_env_kv "$env_file" HF_HUB_ENABLE_HF_TRANSFER "1"
                log "enabled hf_transfer for faster Hugging Face downloads"
            else
                warn "hf_transfer install failed; continuing with standard Hugging Face downloader"
                export HF_HUB_ENABLE_HF_TRANSFER=0
                set_env_kv "$env_file" HF_HUB_ENABLE_HF_TRANSFER "0"
            fi
        fi
        log "pre-downloading model into Hugging Face cache: $model"
        local attempts="${FACULTY_TWIN_PREDOWNLOAD_ATTEMPTS:-}"
        if [[ -z "$attempts" ]]; then
            [[ "$model_preset" == "qwen3-next-80b-awq" ]] && attempts=1 || attempts=4
        fi
        local attempt
        for ((attempt = 1; attempt <= attempts; attempt++)); do
            if [[ "$attempts" -gt 1 ]]; then
                log "pre-download attempt ${attempt}/${attempts}: $model"
            fi
            if "$python_bin" - "$model" <<'PY'
import os
import sys
from huggingface_hub import snapshot_download

model_id = sys.argv[1]
use_token = os.environ.get("FACULTY_TWIN_HF_DIRECT_USE_TOKEN", "0") == "1"
token = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")) if use_token else None
max_workers = int(os.environ.get("FACULTY_TWIN_PREDOWNLOAD_MAX_WORKERS", "4"))
path = snapshot_download(
    repo_id=model_id,
    token=token or None,
    resume_download=True,
    max_workers=max(1, max_workers),
)
print(f"[hosted-web] model cache ready: {path}", flush=True)
PY
            then
                export HF_HUB_OFFLINE=1
                set_env_kv "$env_file" HF_HUB_OFFLINE "1"
                log "model cache ready; enabling HF_HUB_OFFLINE=1 for vLLM startup"
                return 0
            fi
            [[ "$attempt" -lt "$attempts" ]] || break
            warn "pre-download attempt ${attempt}/${attempts} failed for $model; retrying with resume"
            sleep "${FACULTY_TWIN_PREDOWNLOAD_RETRY_DELAY_SECONDS:-15}"
        done
    fi

    if [[ "$predownload_source" == "auto" ]]; then
        warn "Hugging Face pre-download failed for $model; trying aria2 direct mirror download"
        if predownload_model_from_hf_resolve_aria2; then
            return 0
        fi
        warn "Hugging Face pre-download failed for $model; trying ModelScope mirror"
        if predownload_model_from_modelscope; then
            return 0
        fi
        warn "ModelScope pre-download also failed for $model"
    fi

    if [[ "$model_preset" == "qwen3-32b-awq" && "${FACULTY_TWIN_ALLOW_MODEL_FALLBACK:-1}" != "0" ]]; then
        warn "pre-download failed for $model; falling back to Qwen/Qwen3-14B-AWQ for a reliable first install"
        model_preset="qwen3-14b-awq"
        model="Qwen/Qwen3-14B-AWQ"
        served_model="${served_model_override:-$model}"
        tp="${tp_override:-$(default_nvidia_tensor_parallel_size)}"
        max_model_len="${VLLM_NVIDIA_MAX_MODEL_LEN:-32768}"
        max_num_seqs="${VLLM_NVIDIA_MAX_NUM_SEQS:-8}"
        set_env_kv "$env_file" DIGITAL_TWIN_MODEL_NAME "$served_model"
        set_env_kv "$env_file" VLLM_NVIDIA_MODEL "$model"
        if [[ "$model" == /* && "$served_model" != /* ]]; then
            set_env_kv "$env_file" VLLM_NVIDIA_ACTUAL_MODEL_ID "$served_model"
        else
            set_env_kv "$env_file" VLLM_NVIDIA_ACTUAL_MODEL_ID ""
        fi
        set_env_kv "$env_file" VLLM_NVIDIA_SERVED_MODEL_NAME "$served_model"
        set_env_kv "$env_file" VLLM_NVIDIA_TENSOR_PARALLEL_SIZE "$tp"
        set_env_kv "$env_file" VLLM_NVIDIA_MAX_MODEL_LEN "$max_model_len"
        set_env_kv "$env_file" VLLM_NVIDIA_MAX_NUM_SEQS "$max_num_seqs"
        set_env_kv "$env_file" VLLM_NVIDIA_CHAT_TEMPLATE ""
        predownload_model_if_needed
        return 0
    fi

    if [[ "$model_preset" == "qwen3-32b-awq" || "$model_preset" == "qwen3-14b-awq" ]]; then
        fail "pre-download failed for $model; check network/Hugging Face access or choose --model-preset qwen2.5-14b-awq"
    fi

    if [[ "${FACULTY_TWIN_ALLOW_MODEL_FALLBACK:-1}" == "0" ]]; then
        fail "pre-download failed for $model; set FACULTY_TWIN_ALLOW_MODEL_FALLBACK=1 or choose a smaller preset"
    fi

    warn "pre-download failed for $model; falling back to Qwen/Qwen3-32B-AWQ for a reliable first install"
    model_preset="qwen3-32b-awq"
    model="Qwen/Qwen3-32B-AWQ"
    served_model="${served_model_override:-$model}"
    tp="${tp_override:-$(default_nvidia_tensor_parallel_size)}"
    max_model_len="${VLLM_NVIDIA_MAX_MODEL_LEN:-32768}"
    max_num_seqs="${VLLM_NVIDIA_MAX_NUM_SEQS:-8}"
    if ! $hf_endpoint_explicit; then
        export HF_ENDPOINT="${FACULTY_TWIN_FALLBACK_HF_ENDPOINT:-https://huggingface.co}"
        set_env_kv "$env_file" HF_ENDPOINT "$HF_ENDPOINT"
        log "using Hugging Face endpoint for fallback model: $HF_ENDPOINT"
    fi
    set_env_kv "$env_file" DIGITAL_TWIN_MODEL_NAME "$served_model"
    set_env_kv "$env_file" VLLM_NVIDIA_MODEL "$model"
    set_env_kv "$env_file" VLLM_NVIDIA_SERVED_MODEL_NAME "$served_model"
    set_env_kv "$env_file" VLLM_NVIDIA_TENSOR_PARALLEL_SIZE "$tp"
    set_env_kv "$env_file" VLLM_NVIDIA_MAX_MODEL_LEN "$max_model_len"
    set_env_kv "$env_file" VLLM_NVIDIA_MAX_NUM_SEQS "$max_num_seqs"
    set_env_kv "$env_file" VLLM_NVIDIA_CHAT_TEMPLATE ""
    predownload_model_if_needed
}

predownload_model_from_hf_resolve_aria2() {
    command -v aria2c >/dev/null 2>&1 || return 1

    local original_model="$model"
    local endpoint="${HF_ENDPOINT:-https://huggingface.co}"
    local local_dir="${FACULTY_TWIN_HF_DIRECT_MODEL_DIR:-$HOME/models/${original_model//\//--}}"
    local download_state_dir="${runtime_dir:-$repo_dir/../sage-mate-runtime-private}/downloads"
    local input_file

    mkdir -p "$local_dir" "$download_state_dir"
    input_file=$(mktemp "$download_state_dir/aria2-${original_model//\//--}.XXXXXX")
    chmod 600 "$input_file"

    log "pre-downloading model with aria2 direct mirror: $original_model -> $local_dir"
    if ! "$python_bin" - "$original_model" "$endpoint" "$local_dir" "$input_file" <<'PY'
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

model_id, endpoint, local_dir, input_file = sys.argv[1:5]
endpoint = endpoint.rstrip("/")
token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
api_url = f"{endpoint}/api/models/{urllib.parse.quote(model_id, safe='/')}"
request = urllib.request.Request(api_url)
if token:
    request.add_header("Authorization", f"Bearer {token}")

with urllib.request.urlopen(request, timeout=60) as response:
    payload = json.load(response)

siblings = payload.get("siblings") or []
files = [item.get("rfilename") for item in siblings if item.get("rfilename")]
if not files:
    raise SystemExit(f"could not resolve file list for {model_id} from {api_url}")

root = Path(local_dir)
with open(input_file, "w", encoding="utf-8") as handle:
    for name in files:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"{endpoint}/{urllib.parse.quote(model_id, safe='/')}/resolve/main/{urllib.parse.quote(name, safe='/')}"
        handle.write(url + "\n")
        handle.write(f"  dir={target.parent}\n")
        handle.write(f"  out={target.name}\n")
        if token:
            handle.write(f"  header=Authorization: Bearer {token}\n")

print(f"[hosted-web] aria2 input ready: {len(files)} files", flush=True)
PY
    then
        rm -f "$input_file"
        return 1
    fi

    if ! aria2c \
        --input-file="$input_file" \
        --continue=true \
        --max-connection-per-server="${FACULTY_TWIN_ARIA2_CONNECTIONS:-16}" \
        --split="${FACULTY_TWIN_ARIA2_SPLIT:-16}" \
        --min-split-size="${FACULTY_TWIN_ARIA2_MIN_SPLIT_SIZE:-1M}" \
        --max-concurrent-downloads="${FACULTY_TWIN_ARIA2_CONCURRENT_DOWNLOADS:-2}" \
        --retry-wait="${FACULTY_TWIN_ARIA2_RETRY_WAIT_SECONDS:-5}" \
        --max-tries="${FACULTY_TWIN_ARIA2_MAX_TRIES:-0}" \
        --timeout="${FACULTY_TWIN_ARIA2_TIMEOUT_SECONDS:-60}" \
        --connect-timeout="${FACULTY_TWIN_ARIA2_CONNECT_TIMEOUT_SECONDS:-30}" \
        --allow-overwrite=true \
        --auto-file-renaming=false \
        --file-allocation=none \
        --console-log-level=warn \
        --show-console-readout=false \
        --summary-interval="${FACULTY_TWIN_ARIA2_SUMMARY_INTERVAL_SECONDS:-30}" \
        --download-result=hide
    then
        rm -f "$input_file"
        return 1
    fi
    rm -f "$input_file"

    model="$local_dir"
    set_env_kv "$env_file" VLLM_NVIDIA_MODEL "$model"
    set_env_kv "$env_file" VLLM_NVIDIA_ACTUAL_MODEL_ID "$original_model"
    set_env_kv "$env_file" VLLM_NVIDIA_SERVED_MODEL_NAME "$served_model"
    set_env_kv "$env_file" DIGITAL_TWIN_MODEL_NAME "$served_model"
    set_env_kv "$env_file" HF_HUB_OFFLINE "0"
    validate_local_model_if_needed
}

predownload_model_from_modelscope() {
    local original_model="$model"
    local local_dir="${FACULTY_TWIN_MODELSCOPE_MODEL_DIR:-$HOME/models/${original_model//\//--}}"
    log "pre-downloading model from ModelScope: $original_model -> $local_dir"
    if ! "$python_bin" -c "import modelscope" >/dev/null 2>&1; then
        "$python_bin" -m pip install modelscope
    fi
    "$python_bin" - "$original_model" "$local_dir" <<'PY'
import os
import sys
from modelscope.hub.snapshot_download import snapshot_download

model_id = sys.argv[1]
local_dir = sys.argv[2]
max_workers = int(os.environ.get("FACULTY_TWIN_PREDOWNLOAD_MAX_WORKERS", "4"))
path = snapshot_download(
    model_id=model_id,
    local_dir=local_dir,
    max_workers=max(1, max_workers),
)
print(f"[hosted-web] ModelScope model ready: {path}", flush=True)
PY
    model="$local_dir"
    set_env_kv "$env_file" VLLM_NVIDIA_MODEL "$model"
    set_env_kv "$env_file" VLLM_NVIDIA_ACTUAL_MODEL_ID "$original_model"
    set_env_kv "$env_file" VLLM_NVIDIA_SERVED_MODEL_NAME "$served_model"
    set_env_kv "$env_file" DIGITAL_TWIN_MODEL_NAME "$served_model"
    set_env_kv "$env_file" HF_HUB_OFFLINE "0"
    validate_local_model_if_needed
}

validate_local_model_if_needed() {
    [[ "$accelerator" == "nvidia" ]] || return 0
    [[ "$model" == /* ]] || return 0
    [[ -d "$model" ]] || fail "local model path does not exist: $model"
    log "validating local model files: $model"
    "$python_bin" - "$model" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
safetensors_files = sorted(root.rglob("*.safetensors"))
if not safetensors_files:
    raise SystemExit(f"no .safetensors files found under {root}")

try:
    from safetensors import safe_open
except Exception as exc:
    raise SystemExit(f"could not import safetensors to validate {root}: {exc}") from exc

bad: list[str] = []
for path in safetensors_files:
    try:
        with safe_open(path, framework="pt") as handle:
            next(iter(handle.keys()), None)
    except Exception as exc:
        bad.append(f"{path}: {exc}")
        if len(bad) >= 3:
            break

if bad:
    joined = "\n".join(bad)
    raise SystemExit(f"local model safetensors validation failed:\n{joined}")

print(f"[hosted-web] local model validation OK: {root} ({len(safetensors_files)} safetensors)")
PY
}

model_cache_path_hint() {
    local model_id="$1"
    [[ "$model_id" != /* ]] || return 1
    printf '%s/hub/models--%s\n' "${HF_HOME:-$HOME/.cache/huggingface}" "${model_id//\//--}"
}

progress_monitor() {
    local model_id="$1" engine_port="$2" interval="${FACULTY_TWIN_PROGRESS_INTERVAL_SECONDS:-60}"
    local cache_path=""
    cache_path=$(model_cache_path_hint "$model_id" 2>/dev/null || true)
    while true; do
        sleep "$interval" || true
        local listen_state="not-listening"
        if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${engine_port}$"; then
            listen_state="listening"
        fi
        local cache_size="n/a"
        if [[ -n "$cache_path" && -e "$cache_path" ]]; then
            cache_size=$(du -sh "$cache_path" 2>/dev/null | awk '{print $1}')
            [[ -n "$cache_size" ]] || cache_size="n/a"
        fi
        local gpu_summary="n/a"
        if command -v nvidia-smi >/dev/null 2>&1; then
            gpu_summary=$(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null \
                | awk -F, '{gsub(/ /,"",$1); gsub(/ /,"",$2); printf "%s%s/%sMiB", (NR == 1 ? "" : " "), $1, $2}' \
                | sed 's/[[:space:]]*$//')
            [[ -n "$gpu_summary" ]] || gpu_summary="n/a"
        fi
        log "progress: vLLM port ${engine_port}=${listen_state}; model cache=${cache_size}; gpu_mem=${gpu_summary}"
    done
}

select_model() {
    local gpus min_mem
    gpus=$(gpu_count)
    min_mem=$(min_gpu_mem_mib)

    if [[ -n "$model_override" ]]; then
        model="$model_override"
        served_model="${served_model_override:-$model_override}"
        if [[ "$accelerator" == "ascend" ]]; then
            local configured_device_count=1
            if [[ -n "$npu_devices" ]]; then
                configured_device_count="$(awk -F, '{print NF}' <<< "$npu_devices")"
            fi
            tp="${tp_override:-$configured_device_count}"
            max_model_len="${VLLM_ENGINE_MAX_MODEL_LEN:-32768}"
            max_num_seqs="${VLLM_ENGINE_MAX_NUM_SEQS:-16}"
        else
            tp="${tp_override:-$(default_nvidia_tensor_parallel_size "$gpus" "$min_mem")}"
            max_model_len="${VLLM_NVIDIA_MAX_MODEL_LEN:-32768}"
            max_num_seqs="${VLLM_NVIDIA_MAX_NUM_SEQS:-8}"
        fi
        return 0
    fi

    if [[ "$model_preset" == "auto" ]]; then
        if [[ "$accelerator" == "ascend" ]]; then
            fail "Ascend auto-detection cannot infer a safe model path; pass --model and optionally --tensor-parallel-size"
        elif [[ "$min_mem" -ge 70000 ]]; then
            model_preset="qwen3-32b-awq"
        else
            model_preset="qwen2.5-14b-awq"
        fi
    fi

    case "$model_preset" in
        qwen3-next-80b-awq)
            model="cyankiwi/Qwen3-Next-80B-A3B-Instruct-AWQ-4bit"
            served_model="${served_model_override:-$model}"
            tp="${tp_override:-$([[ "$accelerator" == "ascend" ]] && echo 4 || echo 2)}"
            if [[ "$accelerator" == "ascend" ]]; then
                max_model_len="${VLLM_ENGINE_MAX_MODEL_LEN:-32768}"
                max_num_seqs="${VLLM_ENGINE_MAX_NUM_SEQS:-16}"
            else
                max_model_len="${VLLM_NVIDIA_MAX_MODEL_LEN:-32768}"
                max_num_seqs="${VLLM_NVIDIA_MAX_NUM_SEQS:-8}"
            fi
            ;;
        qwen3-32b-awq)
            model="Qwen/Qwen3-32B-AWQ"
            served_model="${served_model_override:-$model}"
            tp="${tp_override:-$(default_nvidia_tensor_parallel_size "$gpus" "$min_mem")}"
            max_model_len="${VLLM_NVIDIA_MAX_MODEL_LEN:-32768}"
            max_num_seqs="${VLLM_NVIDIA_MAX_NUM_SEQS:-8}"
            ;;
        qwen3-14b-awq)
            model="Qwen/Qwen3-14B-AWQ"
            served_model="${served_model_override:-$model}"
            tp="${tp_override:-$(default_nvidia_tensor_parallel_size "$gpus" "$min_mem")}"
            max_model_len="${VLLM_NVIDIA_MAX_MODEL_LEN:-32768}"
            max_num_seqs="${VLLM_NVIDIA_MAX_NUM_SEQS:-8}"
            ;;
        qwen3-32b)
            if [[ "$accelerator" == "ascend" ]]; then
                model="${FACULTY_TWIN_MODEL:-Qwen/Qwen3-32B}"
                served_model="${served_model_override:-$model}"
                tp="${tp_override:-4}"
                max_model_len="${VLLM_ENGINE_MAX_MODEL_LEN:-32768}"
                max_num_seqs="${VLLM_ENGINE_MAX_NUM_SEQS:-16}"
            else
                model="Qwen/Qwen3-32B"
                served_model="${served_model_override:-$model}"
                tp="${tp_override:-$(default_nvidia_tensor_parallel_size "$gpus" "$min_mem")}"
                max_model_len="${VLLM_NVIDIA_MAX_MODEL_LEN:-32768}"
                max_num_seqs="${VLLM_NVIDIA_MAX_NUM_SEQS:-8}"
            fi
            ;;
        qwen2.5-14b-awq)
            model="Qwen/Qwen2.5-14B-Instruct-AWQ"
            served_model="${served_model_override:-$model}"
            tp="${tp_override:-1}"
            max_model_len="${VLLM_NVIDIA_MAX_MODEL_LEN:-16384}"
            max_num_seqs="${VLLM_NVIDIA_MAX_NUM_SEQS:-8}"
            ;;
        *)
            fail "unknown --model-preset: $model_preset"
            ;;
    esac
}

clone_or_update_repo() {
    mkdir -p "$(dirname "$repo_dir")"
    local askpass=""
    git_auth() {
        if [[ -n "$askpass" ]]; then
            env GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 git "$@"
        else
            git "$@"
        fi
    }
    if [[ -n "${GITHUB_TOKEN:-${GH_TOKEN:-}}" ]]; then
        askpass=$(mktemp)
        chmod 0700 "$askpass"
        cat >"$askpass" <<'EOF'
#!/usr/bin/env bash
case "$1" in
    *Username*) printf '%s\n' "${GITHUB_USER:-x-access-token}" ;;
    *Password*) printf '%s\n' "${GITHUB_TOKEN:-${GH_TOKEN:-}}" ;;
    *) printf '\n' ;;
esac
EOF
    fi
    if [[ ! -d "$repo_dir/.git" ]]; then
        log "cloning $repo_url -> $repo_dir"
        git_auth clone "$repo_url" "$repo_dir" || git_auth clone "$https_repo_url" "$repo_dir"
    fi
    cd "$repo_dir"
    if [[ -n "$(git status --short)" ]]; then
        git stash push -u -m "pre-release-install-$(date +%Y%m%d-%H%M%S)"
    fi
    git checkout "$branch"
    git_auth fetch origin
    git_auth pull --ff-only origin "$branch"
    git submodule update --init --recursive
    [[ -z "$askpass" ]] || rm -f "$askpass"
}

main() {
    command -v git >/dev/null 2>&1 || fail "git is required"
    python_bin=$(detect_python) || fail "could not find Python; set PYTHON_BIN"
    export PYTHON_BIN="$python_bin"
    [[ -n "${HF_ENDPOINT:-}" ]] && hf_endpoint_explicit=true
    export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
    export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-0}"

    apply_initial_release_secrets
    detect_accelerator
    if [[ "$accelerator" == "nvidia" ]]; then
        command -v nvidia-smi >/dev/null 2>&1 || fail "nvidia-smi is required for --accelerator nvidia"
    elif [[ "$accelerator" == "ascend" ]]; then
        command -v npu-smi >/dev/null 2>&1 || fail "npu-smi is required for --accelerator ascend"
    fi
    select_model
    clone_or_update_repo

    local env_file="$repo_dir/.env"
    if [[ ! -f "$env_file" && -f "$repo_dir/.env.example" ]]; then
        cp "$repo_dir/.env.example" "$env_file"
    fi
    apply_encrypted_release_secrets "$env_file"

    if [[ -z "$public_hostname" ]]; then
        public_hostname="$(env_file_get "$env_file" FACULTY_TWIN_PUBLIC_HOSTNAME || true)"
    fi
    if [[ -z "$tunnel_name" ]]; then
        tunnel_name="$(env_file_get "$env_file" FACULTY_TWIN_TUNNEL_NAME || true)"
    fi
    tunnel_name="${tunnel_name:-sage-mate-$(hostname -s)-hosted-web}"

    local app_host="${APP_HOST:-$(env_file_get "$env_file" APP_HOST || true)}"
    local app_port="${APP_PORT:-$(env_file_get "$env_file" APP_PORT || true)}"
    local app_health_host="${APP_HEALTH_HOST:-$(env_file_get "$env_file" APP_HEALTH_HOST || true)}"
    local site_health_host="${SITE_HEALTH_HOST:-$(env_file_get "$env_file" SITE_HEALTH_HOST || true)}"
    local site_port="${SITE_PORT:-$(env_file_get "$env_file" SITE_PORT || true)}"
    local proxy_connect_host="${VLLM_PROXY_CONNECT_HOST:-$(env_file_get "$env_file" VLLM_PROXY_CONNECT_HOST || true)}"
    local proxy_port="${VLLM_PROXY_PORT:-$(env_file_get "$env_file" VLLM_PROXY_PORT || true)}"
    app_host="${app_host:-127.0.0.1}"
    app_port="${app_port:-55601}"
    app_health_host="${app_health_host:-127.0.0.1}"
    site_health_host="${site_health_host:-127.0.0.1}"
    site_port="${site_port:-8088}"
    proxy_connect_host="${proxy_connect_host:-127.0.0.1}"
    proxy_port="${proxy_port:-18001}"
    export APP_HOST="$app_host" APP_PORT="$app_port" APP_HEALTH_HOST="$app_health_host"
    export TUNNEL_ORIGIN_HEALTH_URL="${TUNNEL_ORIGIN_HEALTH_URL:-http://${site_health_host}:${site_port}/}"
    set_env_kv "$env_file" APP_HOST "$app_host"
    set_env_kv "$env_file" APP_PORT "$app_port"
    set_env_kv "$env_file" APP_HEALTH_HOST "$app_health_host"
    set_env_kv "$env_file" SITE_HEALTH_HOST "$site_health_host"
    set_env_kv "$env_file" SITE_PORT "$site_port"
    set_env_kv "$env_file" TUNNEL_ORIGIN_HEALTH_URL "$TUNNEL_ORIGIN_HEALTH_URL"
    set_env_kv "$env_file" VLLM_PROXY_CONNECT_HOST "$proxy_connect_host"
    set_env_kv "$env_file" VLLM_PROXY_PORT "$proxy_port"

    if [[ "$accelerator" == "nvidia" && ( "$model_preset" == "qwen3-32b-awq" || "$model_preset" == "qwen3-14b-awq" ) ]] && ! $hf_endpoint_explicit; then
        export HF_ENDPOINT="${FACULTY_TWIN_FALLBACK_HF_ENDPOINT:-https://huggingface.co}"
        log "using Hugging Face endpoint for Qwen3 AWQ preset: $HF_ENDPOINT"
    fi
    if [[ "$accelerator" == "nvidia" && "$HF_ENDPOINT" == "https://huggingface.co" && "${FACULTY_TWIN_PREDOWNLOAD_HF_XET:-1}" != "0" ]]; then
        export HF_HUB_DISABLE_XET=0
        export HF_XET_HIGH_PERFORMANCE=1
        log "enabled Hugging Face Xet high-performance transfer"
    fi
    validate_local_model_if_needed

    local configured_runtime_dir
    configured_runtime_dir="$(env_file_get "$env_file" DIGITAL_TWIN_RUNTIME_DIR || true)"
    local runtime_dir="${DIGITAL_TWIN_RUNTIME_DIR:-${configured_runtime_dir:-$repo_dir/../sage-mate-runtime-private}}"
    set_env_kv "$env_file" DIGITAL_TWIN_DEPLOYMENT_MODE hosted
    set_env_kv "$env_file" DIGITAL_TWIN_APP_PROFILE faculty_twin
    set_env_kv "$env_file" DIGITAL_TWIN_CODE_WORKBENCH_ENABLED false
    set_env_kv "$env_file" DIGITAL_TWIN_CODE_WORKSPACE_ROOTS ""
    set_env_kv "$env_file" DIGITAL_TWIN_LLM_BASE_URL "http://${proxy_connect_host}:${proxy_port}/v1"
    set_env_kv "$env_file" DIGITAL_TWIN_MODEL_NAME "$served_model"
    set_env_kv "$env_file" FACULTY_TWIN_RUNTIME_REPO_URL "$runtime_repo_url"
    set_env_kv "$env_file" FACULTY_TWIN_RUNTIME_REPO_BRANCH "$runtime_repo_branch"
    set_env_kv "$env_file" FACULTY_TWIN_RUNTIME_REPO_REQUIRED true
    if [[ "$accelerator" == "nvidia" ]]; then
        local nvidia_connect_host="${VLLM_NVIDIA_CONNECT_HOST:-127.0.0.1}"
        local nvidia_port="${VLLM_NVIDIA_PORT:-18000}"
        set_env_kv "$env_file" VLLM_PROXY_UPSTREAM_BASE_URL "http://${nvidia_connect_host}:${nvidia_port}/v1"
        set_env_kv "$env_file" VLLM_NVIDIA_MODEL "$model"
        if [[ "$model" == /* && "$served_model" != /* ]]; then
            set_env_kv "$env_file" VLLM_NVIDIA_ACTUAL_MODEL_ID "$served_model"
        else
            set_env_kv "$env_file" VLLM_NVIDIA_ACTUAL_MODEL_ID ""
        fi
        set_env_kv "$env_file" VLLM_NVIDIA_SERVED_MODEL_NAME "$served_model"
        set_env_kv "$env_file" VLLM_NVIDIA_CONNECT_HOST "$nvidia_connect_host"
        set_env_kv "$env_file" VLLM_NVIDIA_PORT "$nvidia_port"
        set_env_kv "$env_file" VLLM_NVIDIA_TENSOR_PARALLEL_SIZE "$tp"
        set_env_kv "$env_file" VLLM_NVIDIA_GPU_MEMORY_UTILIZATION "${VLLM_NVIDIA_GPU_MEMORY_UTILIZATION:-0.88}"
        set_env_kv "$env_file" VLLM_NVIDIA_MAX_MODEL_LEN "$max_model_len"
        set_env_kv "$env_file" VLLM_NVIDIA_MAX_NUM_SEQS "$max_num_seqs"
        set_env_kv "$env_file" VLLM_ENGINE_MODEL_PATH ""
        set_env_kv "$env_file" VLLM_ENGINE_ACTUAL_MODEL_ID ""
        set_env_kv "$env_file" VLLM_ENGINE_SERVED_MODEL_NAME ""
    elif [[ "$accelerator" == "ascend" ]]; then
        if [[ -z "$npu_devices" ]]; then
            npu_devices="$(env_file_get "$env_file" VLLM_ENGINE_NPU_DEVICES || true)"
        fi
        if [[ -z "$npu_devices" ]]; then
            log "auto-selecting $tp idle Ascend device(s)"
            npu_devices="$($python_bin "$repo_dir/tools/select_idle_npus.py" --count "$tp")" \
                || fail "could not select $tp idle Ascend device(s); pass --npu-devices explicitly"
        fi
        local engine_connect_host="${VLLM_ENGINE_CONNECT_HOST:-127.0.0.1}"
        local engine_port="${VLLM_ENGINE_PORT:-8000}"
        set_env_kv "$env_file" VLLM_PROXY_UPSTREAM_BASE_URL "http://${engine_connect_host}:${engine_port}/v1"
        set_env_kv "$env_file" VLLM_ENGINE_MODEL_PATH "$model"
        set_env_kv "$env_file" VLLM_ENGINE_SERVED_MODEL_NAME "$served_model"
        set_env_kv "$env_file" VLLM_ENGINE_CONNECT_HOST "$engine_connect_host"
        set_env_kv "$env_file" VLLM_ENGINE_PORT "$engine_port"
        set_env_kv "$env_file" VLLM_ENGINE_TP_SIZE "$tp"
        set_env_kv "$env_file" VLLM_ENGINE_NPU_DEVICES "$npu_devices"
        set_env_kv "$env_file" ASCEND_RT_VISIBLE_DEVICES "$npu_devices"
        set_env_kv "$env_file" VLLM_ENGINE_MAX_MODEL_LEN "$max_model_len"
        set_env_kv "$env_file" VLLM_ENGINE_MAX_NUM_SEQS "$max_num_seqs"
        set_env_kv "$env_file" VLLM_ENGINE_GPU_MEM_UTIL "${VLLM_ENGINE_GPU_MEM_UTIL:-0.85}"
        set_env_kv "$env_file" VLLM_ENGINE_DTYPE "${VLLM_ENGINE_DTYPE:-bfloat16}"
        set_env_kv "$env_file" VLLM_NVIDIA_MODEL ""
        set_env_kv "$env_file" VLLM_NVIDIA_ACTUAL_MODEL_ID ""
        set_env_kv "$env_file" VLLM_NVIDIA_SERVED_MODEL_NAME ""
    else
        local external_upstream="${VLLM_PROXY_UPSTREAM_BASE_URL:-$(env_file_get "$env_file" VLLM_PROXY_UPSTREAM_BASE_URL || true)}"
        [[ -n "$external_upstream" ]] || fail "--accelerator none requires VLLM_PROXY_UPSTREAM_BASE_URL"
        set_env_kv "$env_file" VLLM_PROXY_UPSTREAM_BASE_URL "$external_upstream"
    fi
    set_env_kv "$env_file" HF_ENDPOINT "$HF_ENDPOINT"
    set_env_kv "$env_file" HF_HUB_DISABLE_XET "$HF_HUB_DISABLE_XET"
    [[ -z "${HF_XET_HIGH_PERFORMANCE:-}" ]] || set_env_kv "$env_file" HF_XET_HIGH_PERFORMANCE "$HF_XET_HIGH_PERFORMANCE"

    if [[ "$accelerator" == "nvidia" && ( "$model_preset" == "qwen3-next-80b-awq" || "$model" == *"Qwen3-Next"* || "$served_model" == *"Qwen3-Next"* ) ]]; then
        set_env_kv "$env_file" VLLM_NVIDIA_CHAT_TEMPLATE "$(prepare_qwen3_next_template "$runtime_dir")"
    elif [[ "$accelerator" == "nvidia" ]]; then
        set_env_kv "$env_file" VLLM_NVIDIA_CHAT_TEMPLATE ""
    fi
    if $with_tunnel; then
        configure_cloudflare_tunnel "$runtime_dir"
    fi
    predownload_model_if_needed

    local quickstart_args=(
        --target hosted-web
        --with-vllm-proxy
        --with-site-proxy
        --skip-vdb-extras
        --pip-timeout
        "${FACULTY_TWIN_PIP_TIMEOUT_SECONDS:-3600}"
    )
    if [[ "$accelerator" == "nvidia" ]]; then
        quickstart_args+=(--with-nvidia-vllm-engine)
    elif [[ "$accelerator" == "ascend" ]]; then
        quickstart_args+=(--with-vllm-engine)
    fi
    $with_tunnel && quickstart_args+=(--with-tunnel)
    $yes && quickstart_args+=(--yes)

    if [[ "$accelerator" == "nvidia" && -z "${VLLM_NVIDIA_INSTALLER:-}" ]]; then
        export VLLM_NVIDIA_INSTALLER=pip
    fi

    log "installing hosted/web $accelerator stack"
    if [[ "$model_preset" == "qwen3-next-80b-awq" ]]; then
        log "large model selected; first start may download model shards and can take 30-60 minutes on a fresh host"
    elif [[ "$model_preset" == "qwen3-32b" || "$model_preset" == "qwen3-32b-awq" || "$model_preset" == "qwen3-14b-awq" ]]; then
        log "Qwen3 model selected; first start may download model shards and can take several minutes on a fresh host"
    fi
    ./quickstart.sh "${quickstart_args[@]}"

    if $start_services; then
        local manage_services=(
            --with-app
            --with-vllm-proxy
            --with-site-proxy
        )
        if [[ "$accelerator" == "nvidia" ]]; then
            manage_services+=(--with-nvidia-vllm-engine)
        elif [[ "$accelerator" == "ascend" ]]; then
            manage_services+=(--with-vllm-engine)
        fi
        $with_tunnel && manage_services+=(--with-tunnel)
        ./manage.sh restart "${manage_services[@]}"
        local verify_timeout="${FACULTY_TWIN_VERIFY_TIMEOUT_SECONDS:-}"
        if [[ -z "$verify_timeout" ]]; then
            if [[ "$model_preset" == "qwen3-next-80b-awq" ]]; then
                verify_timeout=7200
            elif [[ "$model_preset" == "qwen3-32b" || "$model_preset" == "qwen3-32b-awq" || "$model_preset" == "qwen3-14b-awq" ]]; then
                verify_timeout=7200
            else
                verify_timeout=900
            fi
        fi
        log "waiting for hosted/web verification timeout=${verify_timeout}s"
        local verify_args=(--timeout "$verify_timeout")
        if $with_tunnel; then
            verify_args+=(--public-url "https://$public_hostname")
        fi
        local progress_pid=""
        if [[ "$accelerator" == "nvidia" || "$accelerator" == "ascend" ]]; then
            local engine_port="8000"
            [[ "$accelerator" == "nvidia" ]] && engine_port="18000"
            progress_monitor "$model" "$engine_port" &
            progress_pid="$!"
        fi
        cleanup_progress() {
            [[ -z "${progress_pid:-}" ]] || kill "$progress_pid" >/dev/null 2>&1 || true
        }
        trap cleanup_progress RETURN
        local verify_attempts="${FACULTY_TWIN_VERIFY_ATTEMPTS:-3}"
        local verify_ok=false verify_attempt
        for ((verify_attempt = 1; verify_attempt <= verify_attempts; verify_attempt++)); do
            if env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
                NO_PROXY="127.0.0.1,localhost" \
                ./manage.sh verify-hosted-web "${verify_args[@]}"; then
                verify_ok=true
                break
            fi
            [[ "$verify_attempt" -lt "$verify_attempts" ]] || break
            warn "hosted/web verification attempt ${verify_attempt}/${verify_attempts} failed; retrying after services settle"
            sleep "${FACULTY_TWIN_VERIFY_RETRY_DELAY_SECONDS:-20}"
        done
        if ! $verify_ok; then
            cleanup_progress
            trap - RETURN
            fail "hosted/web verification failed after ${verify_attempts} attempt(s)"
        fi
        cleanup_progress
        trap - RETURN
        ./manage.sh status "${manage_services[@]}"
    fi

    log "done"
    if $with_tunnel; then
        log "web: https://$public_hostname/"
    else
        log "web: http://${APP_HEALTH_HOST:-$app_host}:${app_port}/"
    fi
    log "accelerator: $accelerator"
    [[ "$accelerator" != "ascend" ]] || log "npu devices: $npu_devices"
    log "model: $served_model"
}

main "$@"
