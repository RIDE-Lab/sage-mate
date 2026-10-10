"""Bounded, conversation-local Slack context for the /twin command."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


_CHANNEL_ID = re.compile(r"^[CDG][A-Z0-9]{8,}$")
_TOKEN = re.compile(r"(?i)\b(?:xox[baprs]-|sk-)[A-Za-z0-9_-]+")
_MAX_MESSAGES = 30
_MAX_CONTEXT_CHARS = 3200
_MAX_MESSAGE_CHARS = 600
_TIME_ZONE = ZoneInfo("Asia/Shanghai")


class SlackHistoryUnavailable(RuntimeError):
    """A safe error code; never includes a token or Slack message body."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SlackChannelContext:
    text: str
    message_count: int


def fetch_slack_channel_context(
    *, bot_token: str, channel_id: str, latest_ts: str
) -> SlackChannelContext:
    """Read only recent visible messages from the invoking conversation."""
    if not bot_token:
        raise SlackHistoryUnavailable("token_missing")
    if not _CHANNEL_ID.fullmatch(channel_id):
        raise SlackHistoryUnavailable("invalid_channel")
    if not latest_ts.isdigit():
        raise SlackHistoryUnavailable("invalid_timestamp")

    query = urllib.parse.urlencode(
        {"channel": channel_id, "latest": latest_ts, "limit": _MAX_MESSAGES}
    )
    request = urllib.request.Request(
        f"https://slack.com/api/conversations.history?{query}",
        headers={"Authorization": f"Bearer {bot_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.load(response)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SlackHistoryUnavailable("transport_error") from exc

    if not isinstance(payload, dict):
        raise SlackHistoryUnavailable("invalid_response")
    if not payload.get("ok"):
        code = str(payload.get("error") or "api_error")
        if code not in {"missing_scope", "not_in_channel", "channel_not_found", "invalid_auth", "ratelimited"}:
            code = "api_error"
        raise SlackHistoryUnavailable(code)

    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise SlackHistoryUnavailable("invalid_response")

    selected: list[str] = []
    length = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("subtype") or message.get("bot_id"):
            continue
        body = str(message.get("text") or "").strip()
        if not body:
            continue
        body = _TOKEN.sub("[REDACTED]", body)
        body = body[:_MAX_MESSAGE_CHARS]
        user = str(message.get("user") or "unknown")
        if not re.fullmatch(r"[UW][A-Z0-9]+", user):
            user = "unknown"
        try:
            when = datetime.fromtimestamp(float(message["ts"]), _TIME_ZONE).strftime("%m-%d %H:%M")
        except (KeyError, TypeError, ValueError, OverflowError):
            when = "time-unknown"
        entry = f"[{when}] {user}: {body}"
        if length + len(entry) + 1 > _MAX_CONTEXT_CHARS:
            break
        selected.append(entry)
        length += len(entry) + 1

    selected.reverse()
    return SlackChannelContext(text="\n".join(selected), message_count=len(selected))


def asks_about_prior_context(question: str) -> bool:
    """Detect questions that would be misleading without actual Slack history."""
    return any(
        marker in question.lower()
        for marker in (
            "上下文", "前面的对话", "前面的消息", "前文", "刚才的讨论",
            "之前的讨论", "这个频道", "频道历史", "聊天记录",
            "earlier messages", "channel history", "previous discussion",
        )
    )
