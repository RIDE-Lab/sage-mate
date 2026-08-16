#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def _load_env(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    path = repo_root / ".env"
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env.setdefault(key.strip(), value.strip())
    return env


def _request_json(url: str, *, api_key: str, payload: dict | None, timeout: float) -> tuple[int, str]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read(4096).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(2048).decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, f"{exc.__class__.__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--mode", choices=("models", "completion"), default="completion")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    env = _load_env(args.repo_root)
    base_url = env.get("DIGITAL_TWIN_LLM_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
    api_key = env.get("DIGITAL_TWIN_API_KEY", "EMPTY")
    model = env.get("DIGITAL_TWIN_MODEL_NAME", "qwen3-32b") or "qwen3-32b"
    started = time.time()

    if args.mode == "models":
        status, body = _request_json(f"{base_url}/models", api_key=api_key, payload=None, timeout=args.timeout)
    else:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Reply with OK only."},
                {"role": "user", "content": "health check"},
            ],
            "temperature": 0,
            "max_tokens": 8,
            "stream": False,
        }
        status, body = _request_json(
            f"{base_url}/chat/completions",
            api_key=api_key,
            payload=payload,
            timeout=args.timeout,
        )

    ok = 200 <= status < 300
    if ok and args.mode == "completion":
        try:
            parsed = json.loads(body)
            choices = parsed.get("choices") if isinstance(parsed, dict) else None
            ok = bool(
                choices
                and isinstance(choices, list)
                and isinstance(choices[0], dict)
                and (choices[0].get("message") or {}).get("content")
            )
        except json.JSONDecodeError:
            ok = False
    result = {
        "ok": ok,
        "status": status,
        "mode": args.mode,
        "base_url": base_url,
        "model": model,
        "elapsed_seconds": round(time.time() - started, 3),
        "body_preview": body[:300],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(("OK" if ok else "FAIL") + " " + json.dumps(result, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
