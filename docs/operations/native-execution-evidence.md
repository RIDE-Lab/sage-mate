# Native execution evidence

The resident SAGE chat graph and nested retrieval graphs have one request-scoped
ExecutionObserver. Native events preserve real execution/node/program identities,
parentage, ready-wave scheduling, routed successors and attempt states. Application
step names are labels only; no parallelism is inferred from them.

For HTTP-owned delivery, events are appended to the existing SAGE EventJournal
under the same request stream. Each append participates in the current HTTP lease
guard transaction. Wrong streams, stale leases and closed streams reject writes.
All foreground events drain before canonical answer/task admission. No observer is
bound around a background finalizer that might close the stream before emitting
its own completion. No extra scheduler, store or alternate chat flow is introduced.

The committed response and canonical memory retain execution_events, so request
replay and authorized restored history retain the original evidence rather than
executing the graph again. Legacy records have no invented native evidence.
Event metadata excludes question/answer text, operator payloads and error messages.
Existing per-owner history and per-frame SSE authorization are unchanged.

The web view renders native attempts by execution_id, not operator label. Identical
labels across branches/retries do not overwrite each other. Started, completed,
failed, cancelled and recovered remain distinct. Same-wave scheduling does not
claim temporal overlap. Business annotations remain separate, and their historic
parallel_group fields are ignored; backend generation and the guessed DAG renderer
are removed. Per-node durations are not added into an invented request latency.

## Acceptance boundary

SAGE 78a90e0c initially passed 329 installed-package tests, but full Twin regression
found a missing lifetime boundary: idle-GC inherited the initialization request's
context and retained its observer and application owner. This candidate is not
eligible for release. SAGE 502caf74 isolates service-lifetime tasks with a fresh
Context and passes 332 installed-package tests (22.34 seconds), including weakref
reclamation and preserved request-context propagation.

The same investigation found exception objects retained in metadata-discovery log
arguments, keeping the caller's service construction frames alive in buffered log
handlers. The client now logs detached error type/status only, never the exception
object or endpoint. Client/whole-service reclamation tests pass as part of 82 focused
tests (6.78 seconds). No file-descriptor limit was raised to hide the leak.
Twin application gates: 84 focused HTTP/lease/cancellation/delivery tests pass
(51.15 seconds); 32 source-lane/frontend tests pass (5.66 seconds), including real
nested Wiki/private fanout parentage. Six existing Node regression groups and
three frontend contract groups pass. Full frozen regression and deployment are
separate gates; none of these results claims production cutover.

This foreground evidence is not a durable background task acknowledgement. Native
WorkService claim/lease/retry/recovery/finish events and their atomic publication
still need qualification before the overall workflow visualization is complete.
An answer_done frame is not terminal delivery, and an operator completion is not
proof that an external booking/email effect has been acknowledged or reversed.
