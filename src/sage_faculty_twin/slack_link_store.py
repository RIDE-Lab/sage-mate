from __future__ import annotations

import hashlib
import json
import re
import secrets
import string
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime


_CODE_ALPHABET = string.ascii_uppercase + string.digits


@dataclass(slots=True)
class SlackUserLinkRecord:
    slack_user_id: str
    user_id: str
    email: str
    visitor_profile: str
    linked_at: datetime
    team_id: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> SlackUserLinkRecord:
        return cls(
            slack_user_id=str(payload["slack_user_id"]),
            user_id=str(payload["user_id"]),
            email=str(payload["email"]),
            visitor_profile=str(payload["visitor_profile"]),
            linked_at=datetime.fromisoformat(str(payload["linked_at"])),
            team_id=str(payload.get("team_id", "")),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "slack_user_id": self.slack_user_id,
            "user_id": self.user_id,
            "email": self.email,
            "visitor_profile": self.visitor_profile,
            "linked_at": self.linked_at.isoformat(),
            "team_id": self.team_id,
        }


@dataclass(slots=True)
class SlackLinkCodeRecord:
    code: str
    user_id: str
    email: str
    visitor_profile: str
    expires_at: datetime
    created_at: datetime

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> SlackLinkCodeRecord:
        return cls(
            code=str(payload["code"]),
            user_id=str(payload["user_id"]),
            email=str(payload["email"]),
            visitor_profile=str(payload["visitor_profile"]),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "user_id": self.user_id,
            "email": self.email,
            "visitor_profile": self.visitor_profile,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


class SlackUserLinkStore:
    def __init__(self, root_dir: Path) -> None:
        self._root_dir = root_dir
        root_dir.mkdir(parents=True, exist_ok=True)
        self._state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(root_dir / "bindings.sqlite3"))
        self._finalizer = weakref.finalize(self, self._state.close)
        self._slot = ("twin-slack-bindings-v1", "bindings", "state")
        self._import_legacy()

    @staticmethod
    def _empty():
        return {"version": 2, "links": {}, "codes": {}, "unscoped_links": {}}

    def _snapshot(self):
        return self._validated(self._state.peek(self._slot))

    def _validated(self, value):
        if value is None:
            return self._empty()
        if value.get("version") != 2:
            raise ValueError("unsupported_slack_binding_version")
        return value

    @staticmethod
    def _code_key(code):
        return hashlib.sha256(code.strip().upper().encode()).hexdigest()

    @staticmethod
    def _identity(team_id: str, slack_user_id: str) -> str:
        if not all(re.fullmatch(r"[A-Za-z0-9]+", part) for part in (team_id, slack_user_id)):
            raise ValueError("valid_slack_team_and_user_required")
        return f"{team_id}:{slack_user_id}"

    def get_link_for_slack_user(self, slack_user_id: str, *, team_id: str) -> SlackUserLinkRecord | None:
        value = self._snapshot()["links"].get(self._identity(team_id, slack_user_id))
        return SlackUserLinkRecord.from_dict(value) if value else None

    def get_link_for_user(self, user_id: str) -> SlackUserLinkRecord | None:
        value = next((link for link in self._snapshot()["links"].values() if link["user_id"] == user_id.strip()), None)
        return SlackUserLinkRecord.from_dict(value) if value else None

    def create_code(
        self,
        *,
        user_id: str,
        email: str,
        visitor_profile: str,
        ttl_seconds: int = 600,
    ) -> SlackLinkCodeRecord:
        if ttl_seconds <= 0:
            raise ValueError("invalid_slack_code_lifetime")
        now = datetime.now(UTC)
        for _ in range(20):
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
            record = SlackLinkCodeRecord(code=code, user_id=user_id, email=email, visitor_profile=visitor_profile,
                expires_at=now + timedelta(seconds=ttl_seconds), created_at=now)
            key = self._code_key(code)
            payload = record.to_dict()
            del payload["code"]  # Only the hash identifies persisted credentials.
            collision = []
            def create(old):
                value = self._validated(old)
                if key in value["codes"]:
                    collision.append(True)
                    return value
                value["codes"] = {old_key: old_code for old_key, old_code in value["codes"].items()
                                  if old_code["user_id"] != user_id
                                  and datetime.fromisoformat(old_code["expires_at"]) > now}
                value["codes"][key] = payload
                return value
            self._state.update(self._slot, create)
            if not collision:
                return record
        raise RuntimeError("Unable to generate a unique Slack link code.")

    def consume_code(self, code: str, *, slack_user_id: str, team_id: str) -> SlackUserLinkRecord | None:
        identity_key = self._identity(team_id, slack_user_id)
        now, key, consumed = datetime.now(UTC), self._code_key(code), []
        def consume(old):
            value = self._validated(old)
            record = value["codes"].get(key)
            if record is None:
                return value
            del value["codes"][key]
            if datetime.fromisoformat(record["expires_at"]) <= now:
                return value
            link = SlackUserLinkRecord(slack_user_id=slack_user_id.strip(), user_id=record["user_id"],
                email=record["email"], visitor_profile=record["visitor_profile"], linked_at=now, team_id=team_id)
            value["links"] = {identity: existing for identity, existing in value["links"].items()
                              if existing["user_id"] != link.user_id and identity != identity_key}
            value["links"][identity_key] = link.to_dict()
            consumed.append(link)
            return value
        self._state.update(self._slot, consume)
        return consumed[0] if consumed else None

    def prune_expired_codes(self) -> None:
        now = datetime.now(UTC)
        def prune(old):
            value = self._validated(old)
            value["codes"] = {key: record for key, record in value["codes"].items()
                              if datetime.fromisoformat(record["expires_at"]) > now}
            return value
        self._state.update(self._slot, prune)

    def _import_legacy(self):
        existing = self._state.peek(self._slot)
        if existing is not None:
            if existing.get("version") == 1:
                def upgrade(old):
                    if old.get("version") != 1:
                        return self._validated(old)
                    # V1 bindings contain no trustworthy workspace identity.
                    # Preserve them for review, but never use them for access.
                    return {**old, "version": 2, "links": {}, "unscoped_links": old["links"]}
                existing = self._state.update(self._slot, upgrade)
            self._validated(existing)
            self._archive_legacy(existing.get("legacy_sources", []))
            return
        snapshot, sources, owners = self._empty(), [], set()
        for path in sorted((self._root_dir / "links").glob("*.json")):
            raw = path.read_bytes()
            link = SlackUserLinkRecord.from_dict(json.loads(raw))
            if link.user_id in owners or link.slack_user_id in snapshot["unscoped_links"]:
                raise ValueError("legacy_slack_binding_conflict")
            owners.add(link.user_id)
            snapshot["unscoped_links"][link.slack_user_id] = link.to_dict()
            sources.append((path, raw))
        for path in sorted((self._root_dir / "codes").glob("*.json")):
            raw = path.read_bytes()
            record = SlackLinkCodeRecord.from_dict(json.loads(raw))
            value = record.to_dict()
            del value["code"]
            key = self._code_key(record.code)
            if key in snapshot["codes"] and snapshot["codes"][key] != value:
                raise ValueError("legacy_slack_code_conflict")
            snapshot["codes"][key] = value
            sources.append((path, raw))
        # The committed snapshot is authoritative. Originals remain recoverable;
        # subsequent opens never import archived/stale files over current state.
        snapshot["legacy_sources"] = [{"path": str(path.relative_to(self._root_dir)),
                                       "sha256": hashlib.sha256(raw).hexdigest()} for path, raw in sources]
        committed = self._state.update(self._slot, lambda old: old if old is not None else snapshot)
        self._archive_legacy(committed.get("legacy_sources", []))

    def _archive_legacy(self, receipts):
        for receipt in receipts:
            relative = Path(receipt["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("invalid_slack_import_receipt")
            path = self._root_dir / relative
            target = self._root_dir / "legacy-imported" / relative
            if not path.exists():
                if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != receipt["sha256"]:
                    raise ValueError("legacy_slack_archive_missing")
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
                raise ValueError("legacy_slack_source_changed")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != raw:
                    raise ValueError("legacy_slack_archive_conflict")
                path.unlink()
            else:
                path.replace(target)

    def close(self):
        self._finalizer()
