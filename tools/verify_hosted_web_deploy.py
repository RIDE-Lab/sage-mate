#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


SECRET_KEYS = ("TOKEN", "KEY", "SECRET", "PASSWORD")
URL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_env(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env_path = repo_root / ".env"
    if not env_path.exists():
        return env
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env.setdefault(key.strip(), value.strip().strip("\"'"))
    return env


def masked_env_value(key: str, value: str) -> str:
    if any(part in key.upper() for part in SECRET_KEYS):
        return "<set>" if value else "<empty>"
    return value


def request_json(
    url: str,
    *,
    method: str = "GET",
    api_key: str = "",
    timeout: float = 10.0,
) -> tuple[int, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "sage-mate-deploy-verify/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with URL_OPENER.open(req, timeout=timeout) as resp:
            text = resp.read(1024 * 1024).decode("utf-8", errors="replace")
            try:
                return int(resp.status), json.loads(text)
            except json.JSONDecodeError:
                return int(resp.status), text[:500]
    except urllib.error.HTTPError as exc:
        text = exc.read(4096).decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(text)
        except json.JSONDecodeError:
            body = text[:500]
        return int(exc.code), body
    except Exception as exc:
        return 0, f"{exc.__class__.__name__}: {exc}"


def request_status(url: str, *, timeout: float = 10.0) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "User-Agent": "sage-mate-deploy-verify/1.0",
        },
        method="GET",
    )
    try:
        with URL_OPENER.open(req, timeout=timeout) as resp:
            return int(resp.status), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.headers.get("Content-Type", "")
    except Exception as exc:
        return 0, f"{exc.__class__.__name__}: {exc}"


def check(
    condition: bool, label: str, detail: str, failures: list[dict[str, str]]
) -> None:
    status = "OK" if condition else "FAIL"
    print(f"{status} {label}: {detail}", flush=True)
    if not condition:
        failures.append({"label": label, "detail": detail})


def env_bool_false(value: str) -> bool:
    return value.strip().lower() in {"", "0", "false", "no", "off"}


def runtime_repo_identity(url: str) -> str:
    value = url.strip().rstrip("/")
    match = re.search(
        r"github\.com(?::|/)([^/]+/[^/]+?)(?:\.git)?$", value, re.IGNORECASE
    )
    if match:
        return f"github.com/{match.group(1).removesuffix('.git').lower()}"
    return "configured-runtime-repository" if value else "<empty>"


def runtime_repo_remote(runtime_dir: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(runtime_dir), "config", "--get", "remote.origin.url"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def wait_for_models(vllm_url: str, *, timeout: float, api_key: str) -> tuple[int, Any]:
    deadline = time.time() + timeout
    last_status = 0
    last_body: Any = ""
    while time.time() < deadline:
        last_status, last_body = request_json(
            f"{vllm_url.rstrip('/')}/models",
            api_key=api_key,
            timeout=min(10.0, max(1.0, deadline - time.time())),
        )
        if last_status == 200:
            return last_status, last_body
        time.sleep(2)
    return last_status, last_body


def model_ids(models_body: Any) -> tuple[list[str], list[str]]:
    if not isinstance(models_body, dict):
        return [], []
    rows = models_body.get("data")
    if not isinstance(rows, list):
        return [], []
    ids: list[str] = []
    roots: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("id"), str):
            ids.append(row["id"])
        if isinstance(row.get("root"), str):
            roots.append(row["root"])
    return ids, roots


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify hosted/web Faculty Twin deployment safety and LLM wiring."
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--app-url", default="http://127.0.0.1:55601")
    parser.add_argument("--vllm-url", default="")
    parser.add_argument("--public-url", default="")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--allow-model-alias", action="store_true")
    parser.add_argument("--skip-operational-self-knowledge", action="store_true")
    parser.add_argument("--operational-report", type=Path)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    env = load_env(repo_root)
    failures: list[dict[str, str]] = []

    deployment_mode = env.get("DIGITAL_TWIN_DEPLOYMENT_MODE", "")
    app_profile = env.get("DIGITAL_TWIN_APP_PROFILE", "")
    code_enabled = env.get("DIGITAL_TWIN_CODE_WORKBENCH_ENABLED", "")
    workspace_roots = env.get("DIGITAL_TWIN_CODE_WORKSPACE_ROOTS", "")

    check(
        deployment_mode == "hosted",
        "deployment mode",
        f"DIGITAL_TWIN_DEPLOYMENT_MODE={deployment_mode or '<empty>'}",
        failures,
    )
    check(
        app_profile == "faculty_twin",
        "app profile",
        f"DIGITAL_TWIN_APP_PROFILE={app_profile or '<empty>'}",
        failures,
    )
    check(
        env_bool_false(code_enabled),
        "code workbench disabled",
        f"DIGITAL_TWIN_CODE_WORKBENCH_ENABLED={code_enabled or '<empty>'}",
        failures,
    )
    check(
        not workspace_roots.strip(),
        "workspace roots empty",
        "DIGITAL_TWIN_CODE_WORKSPACE_ROOTS is empty",
        failures,
    )

    runtime_repo_url = env.get("FACULTY_TWIN_RUNTIME_REPO_URL", "").strip()
    if runtime_repo_url:
        runtime_dir = Path(env.get("DIGITAL_TWIN_RUNTIME_DIR", "")).expanduser()
        runtime_remote = (
            runtime_repo_remote(runtime_dir) if runtime_dir.is_dir() else ""
        )
        expected_identity = runtime_repo_identity(runtime_repo_url)
        actual_identity = runtime_repo_identity(runtime_remote)
        check(
            bool(runtime_remote),
            "runtime repository checkout",
            f"path={runtime_dir} remote={actual_identity}",
            failures,
        )
        check(
            bool(runtime_remote) and actual_identity == expected_identity,
            "runtime repository source",
            f"expected={expected_identity} actual={actual_identity}",
            failures,
        )

    app_url = args.app_url.rstrip("/")
    status, body = request_json(f"{app_url}/healthz", timeout=10)
    check(
        status == 200 and isinstance(body, dict) and body.get("status") == "ok",
        "app healthz",
        f"status={status}",
        failures,
    )
    status, content_type = request_status(f"{app_url}/", timeout=10)
    check(
        status == 200,
        "app root",
        f"status={status} content_type={content_type}",
        failures,
    )
    status, _ = request_json(f"{app_url}/local-code/config", timeout=10)
    check(status == 403, "local-code config blocked", f"status={status}", failures)
    status, _ = request_json(f"{app_url}/code/workspaces", timeout=10)
    check(status == 403, "code workspaces blocked", f"status={status}", failures)

    if not args.skip_operational_self_knowledge:
        runtime_dir = Path(
            env.get("DIGITAL_TWIN_RUNTIME_DIR")
            or repo_root.parent / "sage-mate-runtime-private"
        )
        report_path = args.operational_report or (
            runtime_dir / "artifacts/operational-self-knowledge-latest.json"
        )
        try:
            operational = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "tools/validate_operational_self_knowledge.py"),
                    "--base-url",
                    app_url,
                    "--output",
                    str(report_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=max(args.timeout, 120.0),
            )
            operational_status = operational.returncode
        except subprocess.TimeoutExpired:
            operational_status = 124
        check(
            operational_status == 0,
            "operational self-knowledge",
            f"status={operational_status} report={report_path}",
            failures,
        )

    if args.public_url:
        public_url = args.public_url.rstrip("/")
        status, body = request_json(f"{public_url}/healthz", timeout=15)
        check(
            status == 200 and isinstance(body, dict) and body.get("status") == "ok",
            "public healthz",
            f"status={status}",
            failures,
        )

    if args.vllm_url.strip():
        vllm_url = args.vllm_url.strip()
    elif env.get("VLLM_NVIDIA_MODEL", "").strip():
        vllm_url = f"http://{env.get('VLLM_NVIDIA_HOST', '127.0.0.1')}:{env.get('VLLM_NVIDIA_PORT', '18000')}/v1"
    else:
        vllm_url = f"http://127.0.0.1:{env.get('VLLM_ENGINE_PORT', '8000')}/v1"
    vllm_api_key = (
        env.get("VLLM_NVIDIA_API_KEY", "")
        or env.get("VLLM_HUST_API_KEY", "")
        or env.get("VLLM_ENGINE_API_KEY", "")
    )
    status, models_body = wait_for_models(
        vllm_url, timeout=args.timeout, api_key=vllm_api_key
    )
    check(status == 200, "vLLM models", f"status={status} url={vllm_url}", failures)
    ids, roots = model_ids(models_body)
    expected_model = (
        env.get("VLLM_NVIDIA_MODEL", "").strip()
        or env.get("VLLM_ENGINE_MODEL_PATH", "").strip()
    )
    actual_model_id = (
        env.get("VLLM_NVIDIA_ACTUAL_MODEL_ID", "").strip()
        or env.get("VLLM_ENGINE_ACTUAL_MODEL_ID", "").strip()
    )
    served_model = (
        env.get("VLLM_NVIDIA_SERVED_MODEL_NAME", "").strip()
        or env.get("VLLM_ENGINE_SERVED_MODEL_NAME", "").strip()
    )
    app_model = env.get("DIGITAL_TWIN_MODEL_NAME", "").strip()
    if served_model == "${DIGITAL_TWIN_MODEL_NAME}":
        served_model = app_model
    actual_model_for_alias_check = actual_model_id or expected_model

    if expected_model:
        expected_exposed = expected_model in roots or expected_model in ids
        if actual_model_id:
            expected_exposed = (
                expected_exposed or actual_model_id in roots or actual_model_id in ids
            )
        check(
            expected_exposed,
            "actual model exposed",
            f"expected={actual_model_id or expected_model}",
            failures,
        )
    if served_model:
        check(
            served_model in ids,
            "served model exposed",
            f"served={served_model}",
            failures,
        )
    if app_model:
        check(
            app_model in ids,
            "app model matches served models",
            f"DIGITAL_TWIN_MODEL_NAME={app_model}",
            failures,
        )
    if actual_model_for_alias_check and served_model and not args.allow_model_alias:
        check(
            served_model == actual_model_for_alias_check,
            "served model is not a misleading alias",
            f"served={served_model} actual={actual_model_for_alias_check}",
            failures,
        )
    if actual_model_for_alias_check and app_model and not args.allow_model_alias:
        check(
            app_model == actual_model_for_alias_check,
            "app model is not a misleading alias",
            f"app={app_model} actual={actual_model_for_alias_check}",
            failures,
        )

    print("Config keys checked:")
    for key in (
        "DIGITAL_TWIN_DEPLOYMENT_MODE",
        "DIGITAL_TWIN_APP_PROFILE",
        "DIGITAL_TWIN_CODE_WORKBENCH_ENABLED",
        "DIGITAL_TWIN_CODE_WORKSPACE_ROOTS",
        "DIGITAL_TWIN_MODEL_NAME",
        "DIGITAL_TWIN_RUNTIME_DIR",
        "FACULTY_TWIN_RUNTIME_REPO_URL",
        "VLLM_NVIDIA_MODEL",
        "VLLM_NVIDIA_ACTUAL_MODEL_ID",
        "VLLM_NVIDIA_SERVED_MODEL_NAME",
        "VLLM_ENGINE_MODEL_PATH",
        "VLLM_ENGINE_ACTUAL_MODEL_ID",
        "VLLM_ENGINE_SERVED_MODEL_NAME",
    ):
        print(f"  {key}={masked_env_value(key, env.get(key, ''))}")

    if failures:
        print(
            f"Hosted/web verification failed with {len(failures)} issue(s).",
            file=sys.stderr,
        )
        return 1
    print("Hosted/web verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
