#!/usr/bin/env bash
set -euo pipefail

repository="${SAGE_ASCEND_CI_REPOSITORY:-RIDE-Lab/sage-mate}"
organization="${repository%%/*}"
runner_group="${SAGE_ASCEND_CI_RUNNER_GROUP:-sage-mate-ascend}"
workflow="${SAGE_ASCEND_CI_WORKFLOW:-ascend-npu.yml}"
container_image="${SAGE_ASCEND_CI_CONTAINER_IMAGE:-}"
verify_engine="${VERIFY_ENGINE:-false}"
runner_cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/sage-mate-actions-runner"
runner_runtime="$(mktemp -d "${TMPDIR:-/tmp}/sage-mate-actions-runner.XXXXXX")"
runner_name="sage-mate-ascend-$(hostname -s)-$$"
listener_pid=""

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

cleanup() {
    if [[ -n "$listener_pid" ]] && kill -0 "$listener_pid" 2>/dev/null; then
        kill "$listener_pid" 2>/dev/null || true
        wait "$listener_pid" 2>/dev/null || true
    fi
    if [[ -d "$runner_runtime/_diag" ]]; then
        mkdir -p "$runner_cache_root/diag"
        cp -a "$runner_runtime/_diag/." "$runner_cache_root/diag/" 2>/dev/null || true
    fi
    rm -rf -- "$runner_runtime"
}
trap cleanup EXIT INT TERM

command -v gh >/dev/null || fail "gh is required"
command -v curl >/dev/null || fail "curl is required"
command -v jq >/dev/null || fail "jq is required"
command -v sha256sum >/dev/null || fail "sha256sum is required"
[[ -n "$container_image" ]] || fail "SAGE_ASCEND_CI_CONTAINER_IMAGE is required"
[[ "$(uname -m)" == "aarch64" ]] || fail "this runner package requires aarch64"

group_json="$(gh api "orgs/$organization/actions/runner-groups")"
group_id="$(jq -r \
    --arg group "$runner_group" \
    --arg workflow "${repository}/.github/workflows/${workflow}@refs/heads/main" \
    '.runner_groups[] | select(.name == $group) |
     select(.visibility == "selected") |
     select(.allows_public_repositories == true) |
     select(.restricted_to_workflows == true) |
     select(.selected_workflows | index($workflow)) | .id' \
    <<<"$group_json")"
[[ "$group_id" =~ ^[0-9]+$ ]] || fail "runner group is not restricted to the trusted main workflow"
gh api "orgs/$organization/actions/runner-groups/$group_id/repositories" \
    --jq ".repositories[] | select(.full_name == \"$repository\") | .full_name" \
    | grep -qx "$repository" || fail "runner group is not restricted to $repository"

release_json="$(gh api repos/actions/runner/releases/latest)"
runner_tag="$(jq -r '.tag_name' <<<"$release_json")"
runner_version="${runner_tag#v}"
asset_name="actions-runner-linux-arm64-${runner_version}.tar.gz"
asset_url="$(jq -r --arg name "$asset_name" '.assets[] | select(.name == $name) | .url' <<<"$release_json")"
asset_digest="$(jq -r --arg name "$asset_name" '.assets[] | select(.name == $name) | .digest' <<<"$release_json")"
[[ -n "$asset_url" && "$asset_url" != "null" ]] || fail "latest ARM64 runner asset not found"
[[ "$asset_digest" == sha256:* ]] || fail "runner release has no SHA-256 digest"

archive="$runner_runtime/$asset_name"
gh api -H 'Accept: application/octet-stream' "$asset_url" >"$archive"
echo "${asset_digest#sha256:}  $archive" | sha256sum --check --status || fail "runner archive checksum mismatch"
tar -xzf "$archive" -C "$runner_runtime"

registration_token="$(gh api --method POST "orgs/$organization/actions/runners/registration-token" --jq .token)"
(
    cd "$runner_runtime"
    ./config.sh \
        --url "https://github.com/$organization" \
        --token "$registration_token" \
        --runnergroup "$runner_group" \
        --name "$runner_name" \
        --labels 'ascend,sage-mate-ephemeral' \
        --work _work \
        --ephemeral \
        --unattended
)

(
    cd "$runner_runtime"
    SAGE_ASCEND_CI_EPHEMERAL=1 \
    SAGE_ASCEND_CI_CONTAINER_IMAGE="$container_image" \
    ./run.sh
) &
listener_pid=$!

for _ in $(seq 1 30); do
    if gh api "orgs/$organization/actions/runners" \
        --jq ".runners[] | select(.name == \"$runner_name\" and .status == \"online\") | .name" \
        | grep -qx "$runner_name"; then
        break
    fi
    sleep 1
done
gh api "orgs/$organization/actions/runners" \
    --jq ".runners[] | select(.name == \"$runner_name\" and .status == \"online\") | .name" \
    | grep -qx "$runner_name" || fail "ephemeral runner did not become online"

dispatch_started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gh workflow run "$workflow" --repo "$repository" --ref main -f "verify_engine=$verify_engine"
run_id=""
for _ in $(seq 1 30); do
    run_id="$(gh run list --repo "$repository" --workflow "$workflow" --event workflow_dispatch --branch main \
        --limit 10 --json databaseId,createdAt \
        --jq ".[] | select(.createdAt >= \"$dispatch_started\") | .databaseId" | head -n 1)"
    [[ -n "$run_id" ]] && break
    sleep 1
done
[[ -n "$run_id" ]] || fail "dispatched workflow run was not found"

echo "Watching Ascend workflow run $run_id"
gh run watch "$run_id" --repo "$repository" --exit-status --interval 10
wait "$listener_pid"
listener_pid=""

if gh api "orgs/$organization/actions/runners" --jq ".runners[] | select(.name == \"$runner_name\") | .name" | grep -q .; then
    fail "ephemeral runner remained registered after its job"
fi
echo "Ascend one-shot CI completed and runner deregistered: run=$run_id"
