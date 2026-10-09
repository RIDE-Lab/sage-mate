"""Revocable, least-privilege API tokens for lab-member automation."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .config import AppSettings


_TOKEN_PREFIX = "sage_lab"


@dataclass(frozen=True, slots=True)
class LabMemberApiTokenIdentity:
    token_id: str
    label: str
    visitor_profile: str
    created_at: datetime
    expires_at: datetime


class LabMemberApiTokenStore:
    """Persist only SHA-256 token digests; plaintext exists only at issuance."""

    def __init__(self, settings: AppSettings) -> None:
        self._path = settings.user_account_store_dir / "api_tokens"

    def issue(self, *, label: str, ttl_seconds: int = 86_400) -> tuple[str, LabMemberApiTokenIdentity]:
        normalized_label = label.strip()
        if not normalized_label:
            raise ValueError("token label must not be empty")
        if not 300 <= ttl_seconds <= 2_592_000:
            raise ValueError("token ttl must be between 300 and 2592000 seconds")
        token_id = uuid4().hex
        token = f"{_TOKEN_PREFIX}_{token_id}_{secrets.token_urlsafe(32)}"
        created_at = datetime.now(UTC)
        identity = LabMemberApiTokenIdentity(
            token_id=token_id,
            label=normalized_label,
            visitor_profile="lab_member",
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=ttl_seconds),
        )
        self._path.mkdir(parents=True, exist_ok=True)
        record_path = self._path / f"{token_id}.json"
        temporary_path = record_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(
                {
                    "token_id": token_id,
                    "label": identity.label,
                    "visitor_profile": identity.visitor_profile,
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "created_at": created_at.isoformat(),
                    "expires_at": identity.expires_at.isoformat(),
                    "revoked": False,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(record_path)
        return token, identity

    def authenticate(self, token: str) -> LabMemberApiTokenIdentity | None:
        parts = token.split("_", 3)
        if len(parts) != 4 or "_".join(parts[:2]) != _TOKEN_PREFIX:
            return None
        token_id = parts[2]
        if len(token_id) != 32 or any(char not in "0123456789abcdef" for char in token_id):
            return None
        record_path = self._path / f"{token_id}.json"
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return None
        if record.get("revoked") is True or record.get("visitor_profile") != "lab_member":
            return None
        expected = str(record.get("token_sha256") or "")
        actual = hashlib.sha256(token.encode()).hexdigest()
        if not expected or not secrets.compare_digest(expected, actual):
            return None
        try:
            created_at = datetime.fromisoformat(str(record["created_at"]))
            expires_at = datetime.fromisoformat(str(record["expires_at"]))
        except (KeyError, ValueError):
            return None
        if expires_at <= datetime.now(UTC):
            return None
        return LabMemberApiTokenIdentity(
            token_id=token_id,
            label=str(record.get("label") or "lab-member-api"),
            visitor_profile="lab_member",
            created_at=created_at,
            expires_at=expires_at,
        )

    def revoke(self, token_id: str) -> bool:
        record_path = self._path / f"{token_id}.json"
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return False
        record["revoked"] = True
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(record_path, 0o600)
        return True
