from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "engine_chat_probe", ROOT / "tools/validate_engine_chat.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("content", ["OK", "\nOK", "OK."])
def test_complete_probe(content):
    probe.validate_response(
        {
            "model": "model",
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        },
        "model",
    )


@pytest.mark.parametrize(
    "content,finish,model",
    [
        (None, "stop", "model"),
        ("", "stop", "model"),
        ("  ", "stop", "model"),
        ("OK", "length", "model"),
        ("OK", "stop", "other"),
        ("<think>reasoning", "stop", "model"),
        ("...OK...", "stop", "model"),
    ],
)
def test_invalid_probe(content, finish, model):
    with pytest.raises(ValueError):
        probe.validate_response(
            {
                "model": model,
                "choices": [{"finish_reason": finish, "message": {"content": content}}],
            },
            "model",
        )


def test_verifier_uses_private_auth_and_explicit_non_thinking():
    script = (ROOT / "tools/verify_sage_mate_engine.sh").read_text()
    assert 'curl --header @- "$@"' in script
    assert '--header "Authorization: Bearer $api_key"' not in script
    assert '"enable_thinking": False' in script
    assert "validate_engine_chat.py" in script
    assert "cannot publish a deployment receipt without a real chat probe" in script
    assert '--list-models --model "$expected_model"' in script


def test_model_probe_selects_configured_model_not_first_item():
    assert (
        probe.select_model({"data": [{"id": "other"}, {"id": "model"}]}, "model")
        == "model"
    )


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"data": []}, {"data": [{"id": "other"}]}]
)
def test_wrong_or_missing_model_is_rejected(payload):
    with pytest.raises(ValueError):
        probe.select_model(payload, "model")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"choices": [None], "model": "model"},
        {"choices": [{"message": "OK"}], "model": "model"},
    ],
)
def test_malformed_completion_is_rejected(payload):
    with pytest.raises(ValueError):
        probe.validate_response(payload, "model")
