# Reader-first answers and public starters

The answer body is the primary reading surface. Source coverage, durable
background work and advanced execution records follow it. Source/background
details start closed; operation state remains visible and must not be described
as external delivery merely because a task persisted. Saved HTML is migrated in
place without regenerating answers or removing complete citations.

Coverage notes, gaps and the complete source list share one native, initially
closed `回答依据` disclosure. No separate source shortcut or second basis
accordion is rendered. Saved standalone source sections are moved into this
disclosure once, preserving their original nodes and complete links. Normal
background status is a quiet text disclosure rather than a bordered card;
explicitly expanding it still exposes all observed task states.

Following a growing answer is explicit: the latest button enables it. A new
question starts at the question/answer beginning. Manual upward navigation
disables following; completion, errors and work-event refreshes preserve the
reader's position. Merely being near the bottom is not permission to follow.

Starter pools retain role-specific wording, but the selected audience is not
proof of membership. Anonymous member selection uses the public visitor pool;
verified users use their verified role. Only topics with actual public Wiki
catalog entries are advertised; an unavailable catalog does not fabricate
fallback topics.

The SAGE `SourceRequest` contract (introduced in SAGE `12b87f33`) separates an
explicit evidence-origin instruction from a topic's inferred confidentiality.
Source restrictions require a verbatim current-question directive. Invalid
model classification is retried once on the original question, then fails
closed. Identity ACLs and explicit request restrictions remain independent;
public fallback must never override an explicit private-only instruction.

Regression entry points: `natural_starters.test.cjs`,
`streaming_scroll.test.cjs`, `response_evidence.test.cjs`,
`test_frontend_contract.py`, `test_llm_client.py`, and SAGE's
`src/tests/test_source_request.py`. Live acceptance must also use the unchanged
guest question, an explicit private-only negative control, a long answer and a
restored conversation. HTTP success or test counts alone are insufficient.
