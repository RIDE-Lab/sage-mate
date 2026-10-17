from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import scrypt
from uuid import uuid4

from fastapi import HTTPException, status

from .config import AppSettings
from .models import UserAccountResponse


@dataclass(slots=True)
class UserAccountRecord:
    user_id: str
    name: str
    email: str
    visitor_profile: str
    password_salt: str
    password_hash: str
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "email": self.email,
            "visitor_profile": self.visitor_profile,
            "password_salt": self.password_salt,
            "password_hash": self.password_hash,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> UserAccountRecord:
        return cls(
            user_id=str(payload["user_id"]),
            name=str(payload["name"]),
            email=str(payload["email"]),
            visitor_profile=str(payload.get("visitor_profile") or "general_visitor"),
            password_salt=str(payload["password_salt"]),
            password_hash=str(payload["password_hash"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        )

    def to_response(self) -> UserAccountResponse:
        return UserAccountResponse(
            user_id=self.user_id,
            name=self.name,
            email=self.email,
            visitor_profile=self.visitor_profile,
            created_at=self.created_at,
        )


class UserAccountStore:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._path = settings.user_account_store_dir
        self._path.mkdir(parents=True, exist_ok=True)
        self._records_by_id: dict[str, UserAccountRecord] = {}
        self._records_by_email: dict[str, UserAccountRecord] = {}
        self._load_from_disk()

    def register_user(
        self, *, name: str, email: str, visitor_profile: str, password: str, invitation_code: str = ""
    ) -> UserAccountResponse:
        normalized_name = name.strip()
        normalized_email = self._normalize_email(email)
        normalized_visitor_profile = visitor_profile.strip()
        if not normalized_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名不能为空。")
        if not self._looks_like_email(normalized_email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="请输入有效的邮箱地址。"
            )
        if normalized_visitor_profile not in _VISITOR_PROFILE_VALUES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="请选择有效的访问身份。"
            )
        if normalized_email in self._records_by_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="该邮箱已注册，请直接登录。"
            )

        # Validate invitation code for lab_member profile
        if (
            normalized_visitor_profile == "lab_member"
            and self._settings.lab_member_invitation_code_enabled
        ):
            expected = self._settings.lab_member_invitation_code
            if not secrets.compare_digest(
                invitation_code.strip(), expected
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="邀请码错误，请联系课题组老师获取。",
                )

        now = datetime.now(UTC)
        salt = secrets.token_hex(16)
        record = UserAccountRecord(
            user_id=str(uuid4()),
            name=normalized_name,
            email=normalized_email,
            visitor_profile=normalized_visitor_profile,
            password_salt=salt,
            password_hash=self._hash_password(password=password, salt=salt),
            created_at=now,
            updated_at=now,
        )
        self._records_by_id[record.user_id] = record
        self._records_by_email[record.email] = record
        self._persist_record(record)
        return record.to_response()

    def authenticate_user(
        self, *, email: str, password: str, invitation_code: str = ""
    ) -> UserAccountResponse:
        normalized_email = self._normalize_email(email)
        cached = self._records_by_email.get(normalized_email)
        record = self._read_current_record(cached.user_id) if cached is not None else None
        if record is None or self._normalize_email(record.email) != normalized_email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="用户邮箱或密码错误。"
            )

        expected_hash = self._hash_password(password=password, salt=record.password_salt)
        if not secrets.compare_digest(record.password_hash, expected_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="用户邮箱或密码错误。"
            )

        # Upgrade to lab_member if invitation code is provided and valid
        if (
            record.visitor_profile != "lab_member"
            and invitation_code.strip()
            and self._settings.lab_member_invitation_code_enabled
        ):
            expected_code = self._settings.lab_member_invitation_code
            if secrets.compare_digest(invitation_code.strip(), expected_code):
                record = UserAccountRecord(
                    user_id=record.user_id,
                    name=record.name,
                    email=record.email,
                    visitor_profile="lab_member",
                    password_salt=record.password_salt,
                    password_hash=record.password_hash,
                    created_at=record.created_at,
                    updated_at=datetime.now(UTC),
                )
                self._records_by_id[record.user_id] = record
                self._records_by_email[record.email] = record
                self._persist_record(record)

        return record.to_response()

    def get_user_by_id(self, user_id: str) -> UserAccountResponse | None:
        record = self._read_current_record(user_id)
        return record.to_response() if record else None

    def _read_current_record(self, user_id: str) -> UserAccountRecord | None:
        # Session/linked-channel authorization must observe revocation by
        # another process, not the account snapshot loaded at startup.
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id):
            return None
        try:
            payload = json.loads((self._path / f"{user_id}.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        record = UserAccountRecord.from_dict(payload)
        if record.user_id != user_id:
            raise ValueError("account_record_identity_conflict")
        return record

    def count_users(self) -> int:
        return len(self._records_by_id)

    def _persist_record(self, record: UserAccountRecord) -> None:
        # Defensive: re-create the directory in case it was wiped at runtime.
        self._path.mkdir(parents=True, exist_ok=True)
        (self._path / f"{record.user_id}.json").write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_from_disk(self) -> None:
        for file_path in sorted(self._path.glob("*.json")):
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            record = UserAccountRecord.from_dict(payload)
            self._records_by_id[record.user_id] = record
            self._records_by_email[self._normalize_email(record.email)] = record

    def _normalize_email(self, email: str) -> str:
        return email.strip().lower()

    def _hash_password(self, *, password: str, salt: str) -> str:
        return scrypt(
            password=password.encode("utf-8"),
            salt=salt.encode("utf-8"),
            n=2**14,
            r=8,
            p=1,
            dklen=64,
        ).hex()

    def _looks_like_email(self, email: str) -> bool:
        return "@" in email and "." in email.rsplit("@", 1)[-1]


_VISITOR_PROFILE_VALUES = {
    "hust_undergraduate",
    "paper_writing_student",
    "lab_member",
    "general_visitor",
}
