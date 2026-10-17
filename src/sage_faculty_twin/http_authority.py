"""Internal HTTP work authority references; never accepted as login credentials."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import resolve_work_session_reference
from .chat_identity import ChatIdentity


class HttpAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["account", "api_token", "guest"]
    session: dict | None = None
    digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    token_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    admin_session: dict | None = None

    @model_validator(mode="after")
    def reference_shape(self):
        if self.kind == "account":
            valid = self.session is not None and self.digest is None and self.token_id is None
        elif self.kind == "api_token":
            valid = self.session is None and self.digest is not None and self.token_id is not None
        else:
            valid = self.session is None and self.digest is not None and self.token_id is None
        if not valid:
            raise ValueError("invalid_http_authority_reference")
        return self


class HttpAuthorityResolver:
    """Resolve current permissions from owning stores, not a captured role cache."""

    def __init__(self, *, settings, users, tokens, guests):
        self.settings, self.users, self.tokens, self.guests = settings, users, tokens, guests

    def resolve(self, authority: HttpAuthority, request):
        if authority.kind == "account":
            claims = resolve_work_session_reference(authority.session, self.settings, kind="user")
            account = self.users.get_user_by_id(claims["sub"]) if claims else None
            if account is None or account.email.lower() != str(claims.get("email", "")).lower():
                raise PermissionError("http_account_authority_denied")
            identity = ChatIdentity("account", account.user_id, account.visitor_profile,
                                    account.email, account.name)
        elif authority.kind == "api_token":
            token = self.tokens.resolve_reference(authority.token_id, authority.digest)
            if token is None:
                raise PermissionError("http_token_authority_denied")
            identity = ChatIdentity("api_token", token.token_id, token.visitor_profile)
        else:
            guest = self.guests.resolve_reference(authority.digest)
            if guest is None or request.visitor_profile == "lab_member":
                raise PermissionError("http_guest_authority_denied")
            identity = ChatIdentity("anonymous", guest.subject_id, request.visitor_profile)
        # A changed role must not expose an answer generated under old access.
        if identity.owner_id != request.owner_id or identity.bind(request).model_dump() != request.model_dump():
            raise PermissionError("http_authority_scope_changed")
        admin = None
        if authority.admin_session is not None:
            admin = resolve_work_session_reference(authority.admin_session, self.settings, kind="admin")
            if admin is None or (admin.get("sub"), admin.get("role")) not in {
                (self.settings.admin_username, "super_admin"), (self.settings.manager_username, "manager")
            }:
                raise PermissionError("http_admin_authority_denied")
        return identity, admin
