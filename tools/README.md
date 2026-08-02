# Script Map

This directory intentionally separates public entrypoints from internal helpers.
Prefer the top-level entrypoints unless you are debugging one component.

## Primary Entrypoints

- `../quickstart.sh` - install/bootstrap entrypoint.
  - Hosted web: `./quickstart.sh --target hosted-web --start`
  - A6000/NVIDIA local engine: add `--with-nvidia-vllm-engine --with-vllm-proxy`
  - Ascend engine: use `--with-vllm-engine --with-vllm-proxy`
- `../release/hosted-web.sh` - release-facing fresh-machine hosted/web installer for NVIDIA,
  Ascend, or external-LLM deployments.
  - Clones/updates the repo, configures hosted/web safety defaults, calls `quickstart.sh`, and
    verifies with `manage.sh verify-hosted-web`.
- `../manage.sh` - runtime management entrypoint.
  - Status/restart/logs for systemd user services.
  - Hosted verification: `./manage.sh verify-hosted-web --public-url "https://$FACULTY_TWIN_PUBLIC_HOSTNAME/"`

## Runtime Launchers

These are systemd-facing scripts. They should stay small and source shared helpers from `tools/lib/`.

- `run_app_server.sh` - Faculty Twin FastAPI app on `127.0.0.1:${APP_PORT:-55601}`.
- `run_vllm_engine.sh` - Ascend vLLM-HUST engine path.
- `run_vllm_nvidia_engine.sh` - NVIDIA/CUDA vLLM-HUST engine path. Uses pinned `deps/vllm-hust`.
- `run_vllm_openai_proxy.sh` - OpenAI-compatible auth proxy.
- `run_local_proxy.sh` - local site proxy.
- `run_named_tunnel.sh` - Cloudflare tunnel. Uses token files so secrets do not appear in process argv.

## Verification And Repair

- `verify_hosted_web_deploy.py` - hosted/web acceptance test:
  - hosted mode and `faculty_twin` profile,
  - local Code Assistant/workspace APIs blocked,
  - app/public health checks,
  - vLLM `/v1/models`,
  - app model name, served model name, and actual model ID consistency.
- `check_twin_inference.py` - low-level OpenAI-compatible LLM smoke test.
- `monitor_twin_inference.sh` - recurring inference monitor for systemd timer.
- `repair_sagevdb.sh` / `repair_sagevdb.py` - native extension repair.
- `upgrade_nvidia_driver_for_vllm.sh` - guarded NVIDIA driver upgrade helper.

## Local Code / Desktop Packaging

These are for local Sage Mate installations, not hosted/web deployments.

- `install_local_code_mode.sh` - local desktop-style setup with optional Code Assistant.
- `build_macos_local_code_package.sh` - macOS package/DMG builder.

## Data And Knowledge Maintenance

- `ingest_wiki.py`, `sync_wiki_kb.sh` - wiki ingestion/sync.
- `ingest_private_materials.py` - private material ingestion with redaction checks.
- `ingest_paper_writing_knowledge.py` - paper-writing KB ingestion.
- `ingest_weekly_schedules.py` - schedule ingestion.
- `migrate_runtime_data.sh` - runtime data migration.
- `prune_skill_duplicate_kb.py` - KB cleanup.

## Experiments, Benchmarks, And Demos

These are not part of normal deployment.

- `benchmark_vllm_latency.py`
- `benchmark_twin_continuity.py`
- `benchmark_knowledge_backends.py`
- `segment_reuse_experiment.py`
- `smoke_segment_reuse_perf.py`
- `record_faculty_twin_demo_playwright.py`
- `create_dual_profile_demo_video.py`
- `replay_poor_cases.py`

## Shared Libraries

- `lib/runtime_env.sh` - Python/runtime path setup.
- `lib/deploy_common.sh` - deployment helpers used by install/runtime scripts.

## Rules Of Thumb

- Hosted/web must never enable local Code Assistant, repo editing, folder selection, or command execution.
- NVIDIA/A6000 deployment uses `--with-nvidia-vllm-engine`; Ascend uses `--with-vllm-engine`.
- Do not introduce a served model alias unless `verify_hosted_web_deploy.py --allow-model-alias` is intentionally used.
- Do not pass secrets as command-line arguments. Use env files, token files, or stdin.
