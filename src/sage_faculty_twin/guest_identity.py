"""Opaque guest credentials backed by SAGE state; no raw credential persistence."""

import hashlib
import math
import re
import secrets
import time
from dataclasses import dataclass


GUEST_COOKIE_NAME = "twin_guest"


@dataclass(frozen=True, slots=True)
class GuestSession:
    subject_id: str
    expires_at: float


class GuestIdentityStore:
    def __init__(self, state, *, ttl_seconds, clock=time.time):
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("invalid_guest_lifetime")
        self._state, self._ttl, self._clock = state, ttl_seconds, clock

    @staticmethod
    def _slot(token):
        return ("twin-guest-session-v1", "credential", hashlib.sha256(token.encode()).hexdigest())

    def authenticate(self, token):
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            return None
        value = self._state.peek(self._slot(token))
        if not isinstance(value, dict) or value.get("version") != 1:
            return None
        subject, expiry = value.get("subject_id"), value.get("expires_at")
        if (not isinstance(subject, str) or re.fullmatch(r"[0-9a-f]{32}", subject) is None
                or not isinstance(expiry, (int, float)) or not math.isfinite(expiry)
                or expiry <= self._clock()):
            return None
        return GuestSession(subject, expiry)

    def issue(self):
        token = secrets.token_urlsafe(32)
        session = GuestSession(secrets.token_hex(16), self._clock() + self._ttl)
        def create(existing):
            if existing is not None:
                raise RuntimeError("guest_credential_collision")
            return {"version": 1, "subject_id": session.subject_id, "expires_at": session.expires_at}
        self._state.update(self._slot(token), create)
        return token, session

    def resolve(self, token):
        session = self.authenticate(token)
        if session is not None:
            return None, session
        return self.issue()
