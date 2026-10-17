"""Trusted HTTP identity binding, distinct from client display/profile hints.

This contract does not confer conversation ownership. In particular, an API
token grants a role, not access to a person's history through a supplied email.
"""

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException

from .models import ChatRequest


@dataclass(frozen=True, slots=True)
class ChatIdentity:
    kind: Literal["account", "api_token", "anonymous"]
    subject_id: str | None
    visitor_profile: str | None
    account_email: str | None = None
    account_name: str | None = None

    def bind(self, payload: ChatRequest) -> ChatRequest:
        email = (self.account_email or "").strip().lower() or None
        if self.kind == "account":
            if not self.subject_id or not email or not self.account_name:
                raise HTTPException(status_code=401, detail="登录账号身份不完整，请重新登录。")
            claimed = (payload.student_email or "").strip().lower()
            if claimed and claimed != email:
                raise HTTPException(status_code=403, detail="不能以其他账号的邮箱提交对话。")
        elif email is not None or self.account_name is not None:
            raise ValueError("non_account_identity_cannot_claim_personal_account")
        return payload.model_copy(update={
            "student_email": email,
            "student_name": self.account_name if self.kind == "account" else payload.student_name,
            "visitor_profile": self.visitor_profile,
            "answer_max_tokens": payload.answer_max_tokens if self.visitor_profile == "lab_member" else None,
        })
