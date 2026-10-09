#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
report_path="${REPORT_PATH:-${RUNNER_TEMP:-/tmp}/ascend-host-report.json}"
app_health_url="${SAGE_ASCEND_CI_APP_HEALTH_URL:-http://127.0.0.1:55601/health}"
engine_health_url="${SAGE_ASCEND_CI_ENGINE_HEALTH_URL:-http://127.0.0.1:18001/health}"
verify_engine="${VERIFY_ENGINE:-false}"
container_image="${SAGE_ASCEND_CI_CONTAINER_IMAGE:-}"

mkdir -p "$(dirname "$report_path")"
printf '{"schema_version":1,"result":"started"}\n' >"$report_path"

finalize_failure_report() {
    local status=$?
    if (( status != 0 )); then
        printf '{"schema_version":1,"result":"failed"}\n' >"$report_path"
    fi
    trap - EXIT
    exit "$status"
}
trap finalize_failure_report EXIT

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

[[ "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]] || fail "hardware regression requires workflow_dispatch"
[[ "${GITHUB_REF:-}" == "refs/heads/main" ]] || fail "hardware regression requires refs/heads/main"
[[ "${SAGE_ASCEND_CI_EPHEMERAL:-}" == "1" ]] || fail "runner is not marked ephemeral"
[[ -n "$container_image" ]] || fail "SAGE_ASCEND_CI_CONTAINER_IMAGE is required"
[[ "$(uname -m)" == "aarch64" ]] || fail "Ascend runner must be aarch64"

command -v npu-smi >/dev/null || fail "npu-smi is unavailable"
command -v curl >/dev/null || fail "curl is unavailable"
command -v python3 >/dev/null || fail "python3 is unavailable"

mapfile -t npu_devices < <(find /dev -maxdepth 1 -type c -name 'davinci[0-9]*' -printf '%f\n' | sort -V)
(( ${#npu_devices[@]} > 0 )) || fail "no Ascend NPU device nodes found"
for control_device in /dev/davinci_manager /dev/devmm_svm /dev/hisi_hdc; do
    [[ -c "$control_device" ]] || fail "missing control device $control_device"
done
npu-smi info >/dev/null

docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then
    docker_cmd=(sudo -n docker)
    "${docker_cmd[@]}" info >/dev/null 2>&1 || fail "Docker is unavailable without interactive elevation"
fi
"${docker_cmd[@]}" image inspect "$container_image" >/dev/null

probe_device="/dev/${npu_devices[0]}"
"${docker_cmd[@]}" run --rm --network none \
    --entrypoint /bin/sh \
    --device "$probe_device" \
    --device /dev/davinci_manager \
    --device /dev/devmm_svm \
    --device /dev/hisi_hdc \
    "$container_image" \
    -c "test -c '$probe_device' && test -c /dev/davinci_manager && test -c /dev/devmm_svm && test -c /dev/hisi_hdc"

for script in \
    tools/run_vllm_engine.sh \
    tools/lock_sage_mate_engine.sh \
    tools/verify_sage_mate_engine.sh \
    tools/retry_deploy_vllm_ascend_until_success.sh; do
    bash -n "$repo_root/$script"
done
grep -q 'VLLM_ENGINE_ENFORCE_EAGER=0' "$repo_root/tools/lock_sage_mate_engine.sh"
if grep -Eq -- '(^|[[:space:]])--enforce-eager([[:space:]]|$)' \
    "$repo_root/tools/run_vllm_engine.sh" \
    "$repo_root/tools/lock_sage_mate_engine.sh"; then
    fail "formal Ascend launcher enables --enforce-eager"
fi

app_health="$(curl --fail --silent --show-error --max-time 15 "$app_health_url")"
python3 -c 'import json,sys; data=json.load(sys.stdin); assert data.get("status") == "ok"' <<<"$app_health"

engine_status="not_requested"
if [[ "$verify_engine" == "true" ]]; then
    curl --fail --silent --show-error --max-time 15 "$engine_health_url" >/dev/null
    engine_status="healthy"
fi

REPORT_PATH="$report_path" \
APP_HEALTH="$app_health" \
ENGINE_STATUS="$engine_status" \
NPU_COUNT="${#npu_devices[@]}" \
CONTAINER_IMAGE="$container_image" \
python3 - <<'PY'
import json
import os
from pathlib import Path

health = json.loads(os.environ["APP_HEALTH"])
report = {
    "schema_version": 1,
    "result": "passed",
    "architecture": "aarch64",
    "npu_device_count": int(os.environ["NPU_COUNT"]),
    "container_image": os.environ["CONTAINER_IMAGE"],
    "container_device_binding": "passed",
    "graph_mode_contract": "passed",
    "app_health": {
        "status": health.get("status"),
        "app_version": health.get("app_version"),
    },
    "engine_health": os.environ["ENGINE_STATUS"],
}
Path(os.environ["REPORT_PATH"]).write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

trap - EXIT

echo "Ascend host regression passed: ${#npu_devices[@]} device(s), image=$container_image"
echo "Report: $report_path"
