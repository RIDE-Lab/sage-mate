from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request, Response, status

from .config import AppSettings
from .models import AdminSessionResponse

ADMIN_COOKIE_NAME = "faculty_twin_admin"
USER_COOKIE_NAME = "faculty_twin_user"


def capture_work_session_reference(token: str | None, settings: AppSettings, *, kind: str):
    """Attest an already signed session for internal work, never copy its token.

    The domain-separated proof is not accepted by cookie authentication. It
    preserves original expiry and invalidates on signing-key rotation. Account
    existence/current role must still be checked by the caller at execution.
    This does not add logout revocation to existing stateless sessions.
    """
    secret = _work_session_secret(settings, kind)
    claims = _decode_session_cookie(token, secret)
    if not isinstance(claims, dict):
        return None
    # Retain only the signed identity/expiry fields, never credential bytes.
    fields = ("sub", "iat", "exp", "nonce", "email") if kind == "user" else (
        "sub", "iat", "exp", "nonce", "role")
    claims = {name: claims[name] for name in fields if name in claims}
    reference = {"version": 1, "kind": kind, "claims": claims}
    reference["proof"] = _work_session_proof(reference, secret)
    return reference


def resolve_work_session_reference(reference, settings: AppSettings, *, kind: str):
    """Internal-only fresh verification; never expose as an HTTP credential."""
    secret = _work_session_secret(settings, kind)
    if (not isinstance(reference, dict) or set(reference) != {"version", "kind", "claims", "proof"}
            or reference["version"] != 1 or reference["kind"] != kind):
        return None
    proof, claims = reference["proof"], reference["claims"]
    if not isinstance(proof, str) or not isinstance(claims, dict):
        return None
    try:
        expected = _work_session_proof({k: v for k, v in reference.items() if k != "proof"}, secret)
        if not secrets.compare_digest(proof, expected):
            return None
    except (TypeError, ValueError):
        return None
    expiry = claims.get("exp")
    if type(expiry) is not int or expiry <= time.time() or not isinstance(claims.get("sub"), str):
        return None
    return dict(claims)


def _work_session_secret(settings, kind):
    if kind not in {"user", "admin"}:
        raise ValueError("unsupported_work_session_kind")
    return settings.user_session_secret if kind == "user" else settings.admin_session_secret


def _work_session_proof(reference, secret):
    message = b"sage-mate/internal-work-session/v1\x00" + json.dumps(
        reference, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def decode_admin_session_token(token: str | None, settings: AppSettings) -> dict[str, Any] | None:
    return _decode_session_cookie(token, settings.admin_session_secret)


def build_admin_session_token(
    settings: AppSettings,
    *,
    username: str,
    role: str,
) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + settings.admin_session_ttl_seconds,
        "nonce": secrets.token_hex(8),
    }
    return _encode_session_cookie(payload, settings.admin_session_secret)


def decode_user_session_token(token: str | None, settings: AppSettings) -> dict[str, Any] | None:
    return _decode_session_cookie(token, settings.user_session_secret)


def build_user_session_token(*, user_id: str, email: str, settings: AppSettings) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + settings.user_session_ttl_seconds,
        "nonce": secrets.token_hex(8),
    }
    return _encode_session_cookie(payload, settings.user_session_secret)


def set_admin_session_cookie(response: Response, token: str, settings: AppSettings) -> None:
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.admin_session_ttl_seconds,
        path="/",
    )


def clear_admin_session_cookie(response: Response) -> None:
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")


def set_user_session_cookie(response: Response, token: str, settings: AppSettings) -> None:
    response.set_cookie(
        key=USER_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=settings.user_session_ttl_seconds,
        path="/",
    )


def clear_user_session_cookie(response: Response) -> None:
    response.delete_cookie(USER_COOKIE_NAME, path="/")


def build_admin_session_response(request: Request, settings: AppSettings) -> AdminSessionResponse:
    payload = decode_admin_session_token(request.cookies.get(ADMIN_COOKIE_NAME), settings)
    if payload is None:
        return AdminSessionResponse(is_admin=False, mode="user")
    username, role = resolve_admin_session_identity(payload, settings)
    return AdminSessionResponse(
        is_admin=True,
        mode="admin",
        username=username,
        role=role,
    )


def issue_admin_session(
    response: Response,
    settings: AppSettings,
    *,
    username: str,
    role: str,
) -> AdminSessionResponse:
    token = build_admin_session_token(settings, username=username, role=role)
    set_admin_session_cookie(response, token, settings)
    return AdminSessionResponse(is_admin=True, mode="admin", username=username, role=role)


def clear_admin_session(response: Response) -> AdminSessionResponse:
    clear_admin_session_cookie(response)
    return AdminSessionResponse(is_admin=False, mode="user")


def require_admin(request: Request, settings: AppSettings) -> dict[str, Any]:
    payload = normalize_admin_session_payload(
        decode_admin_session_token(request.cookies.get(ADMIN_COOKIE_NAME), settings),
        settings,
    )
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员身份验证。",
        )
    return payload


def resolve_admin_session_identity(
    payload: dict[str, Any],
    settings: AppSettings,
) -> tuple[str, str]:
    username = str(payload.get("sub") or settings.admin_username)
    role = str(payload.get("role") or "").strip()
    if role in {"super_admin", "manager"}:
        return username, role
    if username == settings.manager_username:
        return username, "manager"
    return username, "super_admin"


def normalize_admin_session_payload(
    payload: dict[str, Any] | None,
    settings: AppSettings,
) -> dict[str, Any] | None:
    if payload is None:
        return None
    username, role = resolve_admin_session_identity(payload, settings)
    normalized = dict(payload)
    normalized["sub"] = username
    normalized["role"] = role
    return normalized


def validate_admin_credentials(
    username: str,
    password: str,
    settings: AppSettings,
) -> tuple[str, str]:
    for account_username, account_password, account_role in _iter_admin_accounts(settings):
        username_ok = secrets.compare_digest(username, account_username)
        password_ok = secrets.compare_digest(password, account_password)
        if username_ok and password_ok:
            return account_username, account_role

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="管理员账号或密码错误。",
    )


def _iter_admin_accounts(settings: AppSettings) -> tuple[tuple[str, str, str], ...]:
    return (
        (settings.admin_username, settings.admin_password, "super_admin"),
        (settings.manager_username, settings.manager_password, "manager"),
    )


def _encode_session_cookie(payload: dict[str, Any], secret: str) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("ascii")
    signature = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def _decode_session_cookie(token: str | None, secret: str) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None

    payload_b64, signature = token.rsplit(".", 1)
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not secrets.compare_digest(signature, expected_signature):
        return None

    try:
        payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        payload = json.loads(payload_json.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None

    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload
