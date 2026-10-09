"""Model-bound thinking capability; never infer support from a product name."""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ThinkingCapability:
    supported: bool | None
    source: str
    switchable: bool = False


def capability_from_tokenization(
    disabled: dict[str, Any], enabled: dict[str, Any]
) -> ThinkingCapability:
    """Inspect the server's rendered template without running model inference.

    This detector covers the enable_thinking/<think> protocol. Other protocols
    must be declared by the operator, not guessed from a model family name.
    """
    token_lists = [item.get("tokens") for item in (disabled, enabled)]
    pieces = [item.get("token_strs") for item in (disabled, enabled)]
    if not all(isinstance(tokens, list) and tokens for tokens in token_lists):
        return ThinkingCapability(None, "invalid_tokenizer_response")
    if not all(
        isinstance(tokens, list) and tokens and all(isinstance(s, str) for s in tokens)
        for tokens in pieces
    ):
        return ThinkingCapability(None, "missing_token_strings")
    off, on = ("".join(tokens) for tokens in pieces)
    open_on = on.rfind("<think>") > on.rfind("</think>")
    open_off = off.rfind("<think>") > off.rfind("</think>")
    if open_on:
        return ThinkingCapability(True, "served_chat_template", switchable=not open_off)
    if token_lists[0] == token_lists[1] and "<think>" not in on:
        return ThinkingCapability(False, "template_has_no_thinking_channel")
    return ThinkingCapability(None, "unrecognized_thinking_protocol")


def configured_capability(
    mode: Literal["auto", "native", "application"],
) -> ThinkingCapability:
    if mode == "native":
        return ThinkingCapability(True, "operator_verified_native", switchable=True)
    if mode == "application":
        return ThinkingCapability(False, "operator_declared_unsupported")
    return ThinkingCapability(None, "not_probed")


class VisibleAnswerFilter:
    """Suppress inline think spans, including tags split across SSE chunks."""

    def __init__(self) -> None:
        self.pending = ""
        self.in_thinking = False

    def feed(self, text: str) -> str:
        self.pending += text
        visible = []
        while self.pending:
            marker = "</think>" if self.in_thinking else "<think>"
            index = self.pending.find(marker)
            if index >= 0:
                if not self.in_thinking:
                    visible.append(self.pending[:index])
                self.pending = self.pending[index + len(marker) :]
                self.in_thinking = not self.in_thinking
                continue
            keep = 0
            for length in range(1, len(marker)):
                if self.pending.endswith(marker[:length]):
                    keep = length
            safe = self.pending[:-keep] if keep else self.pending
            if not self.in_thinking:
                visible.append(safe)
            self.pending = self.pending[-keep:] if keep else ""
            break
        return "".join(visible)

    def finish(self) -> str:
        # A trailing partial tag is not safe to publish; plain '<' fragments
        # are rare in prose and can be regenerated instead of leaking a span.
        self.pending = ""
        return ""
