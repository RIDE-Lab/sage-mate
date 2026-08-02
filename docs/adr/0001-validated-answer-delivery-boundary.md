# ADR 0001: Make answer delivery a validated type boundary

- Status: accepted
- Date: 2026-08-02

## Context

Model generation, deterministic fallbacks, skills, onboarding shortcuts, code
tools, and alternate profiles can all create plain `ChatResponse` objects. The
SSE route previously streamed model tokens before retry and quality checks
completed. Buffering fixed that endpoint, but did not make the rule unavoidable.

## Decision

All public service entry points pass their result through `ChatDeliveryGate`.
The gate validates an `AnswerCandidate` and returns the distinct subtype
`DeliveredChatResponse`. Public answer-event publishing accepts only that type
and rejects an ordinary `ChatResponse` at runtime.

The first gate enforces transport-independent structural invariants. Semantic
grounding rules will move into the same validation module as generation is
extracted. Until then, existing route-specific quality checks remain in place.

## Consequences

- Fast paths can no longer accidentally bypass the common delivery check.
- Candidate text and delivered text are different concepts in code and tests.
- Raw token deltas cannot be represented as a delivered response.
- The public JSON schema is unchanged because the subtype adds no public fields.
- Internal code still contains duplicated quality logic during the migration;
  this is an explicit temporary state, not the target architecture.
