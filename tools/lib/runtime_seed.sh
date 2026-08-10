#!/usr/bin/env bash
# Seed non-secret runtime defaults used by fresh installs and CI.

seed_runtime_data() {
    local repo_root="$1"
    local runtime_dir="$2"
    local seed_root="$repo_root/release/runtime-seed/data"

    [[ -d "$seed_root" ]] || return 0

    mkdir -p \
        "$runtime_dir/data/capability_plugins" \
        "$runtime_dir/data/persona" \
        "$runtime_dir/data/workflow_policies" \
        "$runtime_dir/data/workflow_scenarios"

    local source target
    for source in "$seed_root"/capability_plugins/*.json; do
        [[ -f "$source" ]] || continue
        target="$runtime_dir/data/capability_plugins/$(basename "$source")"
        [[ -f "$target" ]] || cp "$source" "$target"
    done

    for source in \
        "$seed_root/persona/style_profile.md" \
        "$seed_root/workflow_policies/faculty-default-2026-05.json" \
        "$seed_root/workflow_scenarios/v3_preview_scenarios.json"; do
        [[ -f "$source" ]] || continue
        target="$runtime_dir/data/${source#"$seed_root/"}"
        mkdir -p "$(dirname "$target")"
        [[ -f "$target" ]] || cp "$source" "$target"
    done

    # Release notes are code-owned and must follow the deployed application;
    # unlike user-authored runtime data, an older changelog must be replaced.
    if [[ -f "$seed_root/changelog.json" ]]; then
        cp "$seed_root/changelog.json" "$runtime_dir/data/changelog.json"
    fi
}
