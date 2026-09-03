"""Reject empty, truncated or wrong-model engine verification responses."""

from __future__ import annotations

import argparse
import json
import sys


def select_model(response: dict, expected: str) -> str:
    """Verify the configured deployment, not merely whichever model answers."""
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise ValueError("model listing has an invalid schema")
    models = [item.get("id") for item in response["data"] if isinstance(item, dict)]
    if not expected or expected not in models:
        raise ValueError("configured model is absent from the engine model listing")
    return expected


def validate_response(response: dict, model: str) -> None:
    if not isinstance(response, dict):
        raise ValueError("chat completion has an invalid schema")
    if response.get("model") != model:
        raise ValueError("chat completion used a different model")
    choices = response.get("choices") or []
    if not isinstance(choices, list) or not choices:
        raise ValueError("chat completion returned no choices")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise ValueError("chat completion choice has an invalid schema")
    if choice.get("finish_reason") != "stop":
        raise ValueError("chat completion did not finish normally")
    content = (choice.get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("chat completion returned no answer content")
    if content.strip() not in {"OK", "OK."}:
        raise ValueError("chat completion failed the instruction probe")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--list-models", action="store_true")
    args = parser.parse_args()
    try:
        response = json.load(sys.stdin)
        if args.list_models:
            print(select_model(response, args.model))
        else:
            validate_response(response, args.model)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
