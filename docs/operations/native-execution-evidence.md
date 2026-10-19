# Native execution evidence

## 2026-10-19 qualification and deployment

Runtime `9bf304448beff7b74518ad8095f659a1433b9e20`, installed SAGE
`4c4abffed927925856daf6ac468789dc391d587f`: fresh locked CPython 3.11
environment, 1,727 passed / 31 existing skips / zero failures or errors.
The baseline identity-evaluation exclusion was unchanged. Seven frontend Node
groups and 39 focused frontend contracts also passed. Production now runs this
release; the Wiki timer uses its matching interpreter and source paths. Three
canonical SQLite stores were backed up using SQLite's backup API with all writers
stopped, and each backup passed integrity checks before the switch. Inference
services and credentials were not changed.

In the isolated real-model preview, full Wiki evidence, native streamed execution,
atomic background acknowledgement and cross-guest isolation passed. Existing
browser history reopened without reasking or changing answer/source text. Both
themes visibly showed the main graph and the retrieval child graph with its
two routed branches. A real browser failure exposed event-group-scoped parent
lookup; the corrected trace-scoped explicit reference is covered by regression.
Production HTML and JavaScript/CSS hashes were verified against main.

The one authorized Slack acceptance question was sent through the actual slash
command. Its native answer/deliver/finalize tasks completed on attempt one, with
one of one delivery receipts accepted. **This is not a client-rendering pass:**
Slack's accessible tree and screenshot disagreed and the computer-control service
subsequently timed out. Actual new-message Markdown display remains an outstanding
acceptance item. Do not resend the question without new authorization or report
the complete migration goal as accepted solely from the server receipt.

The owned loopback preview and its SSH forward were stopped after qualification;
their synthetic data and reports were retained. No second production executor
was introduced by this visualization.

## Workflow view, not a flat node log

The process panel renders an actual native workflow diagram. Node identities
include program URI/revision/node ID; scopes include trace/request and the native
parent execution. Main and nested workflows remain distinct. Declared upstream/
downstream edges are dotted; native `next_nodes` routes are solid. Layer placement
comes from those relationships, never operator names, timestamps or business notes.
Same-layer placement does not assert simultaneous execution. Nested scopes name
their recorded parent; missing parents remain explicitly unknown.

SAGE `request_id` identifies an event group, not the whole nested call tree.
Parent references therefore resolve by explicit execution ID within the same
trace, even when a child has a different event-group ID. Conflicting duplicate
execution identities remain unresolved; unrelated traces are never joined.

Repeated executions of one graph node retain every attempt/status instead of
inventing a latest overall success. Declared targets with no execution record
are visibly unobserved, not skipped or complete. Cycles retain their edges and
are labelled rather than silently flattened. Missing topology fields fall back
only to the separately folded node metadata log, not a guessed workflow.
Background lease acknowledgement remains a separate observed section: neither
graph completion nor an outgoing edge proves answer commit or external receipt.
Restored answers hydrate the same owner-scoped metadata endpoint and rebuild the
workflow view without changing the answer/citations or calling the model again.
This replaces the list-only presentation, not the SAGE workflow or its executor.

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

The dd2f76c full gate completed with 1629 passes, 31 unchanged skips and 36
failures. Thirty-four failures were caused by an unsupported `local` SageVDB
backend in the operator test environment; the next gate uses the real `cpp`
backend, not a new alias or fallback. Two obsolete frontend assertions now verify
the native summary remains collapsed and the pending view is singular, without
invented phase/completion totals. The affected frontend/knowledge/recovery groups
pass 139 tests with five existing skips (48.80 seconds). A fresh full gate remains
required; resource exhaustion did not recur in the completed failing gate.

The corrected, independent frozen 6bb141c / SAGE502caf74 gate completed with
1665 passes, 31 unchanged skips, zero failures/errors and one existing framework
warning in 438.39 seconds. JUnit independently confirms 1696 cases, zero failures,
zero errors and 31 skips. The file-descriptor soft limit remained 1024. The existing
identity-evaluation exclusion was unchanged; no extra tests were excluded.

Actual isolated HTTP preview of the identical runtime code (dd2f76c differs from
6bb141c only in tests/docs) passed full Wiki provenance/links, 30 native frames for
15 attempts before answer admission, terminal SSE, cross-guest denial and identical
replay (12.26 seconds first answer; 0.13 seconds replay). Booking state survived a
controlled preview restart and an email-only continuation preserved original topic
and time (1.39 seconds; replay 0.12 seconds). Cancellation sealed both destination
databases, returned 499 without a canonical answer and replayed correctly (0.37
seconds). SMTP/Slack were disabled in preview; no production business effects or
model/GPU service changes were made. The preview unit was stopped after acceptance;
qualification data and failed artifacts remain available for audit.

These results qualify the foreground source/runtime stage, not production or
browser acceptance. Production remains d758087 / SAGEb7355905. Native background
task acknowledgement events, legacy cached visualization claims and final visual
acceptance must be completed before claiming the overall workflow goal achieved.

This foreground evidence is not a durable background task acknowledgement. Native
WorkService claim/lease/retry/recovery/finish events and their atomic publication
still need qualification before the overall workflow visualization is complete.
An answer_done frame is not terminal delivery, and an operator completion is not
proof that an external booking/email effect has been acknowledged or reversed.
# Background acknowledgement and restored history

The post-answer service now binds SAGE `WorkObserver` to the existing answer
EventJournal, on the same canonical state store. `started` is published before
handler entry; completion, its native event, post-answer business notes and the
stream terminal are one fenced native acknowledgement transaction. Retry/failure
events follow the native journal decision, not answer text or HTTP status. This
is not proof that external mail or another system received a side effect.

`GET /chat/execution?exchange_id=...` resolves the current verified owner, requires
that owner's canonical exchange, and projects fresh events from the captured
delivery stream. It returns metadata only, never questions or answers. The
opaque ID, supplied email or profile is not an access grant. Missing/foreign
exchanges return 404; responses are `no-store`. HTTP replay and conversation
history project the same current native evidence without changing the committed
answer or initiating another model call.

The browser refreshes restored messages through this endpoint and the existing
authorized SSE stream. It updates only the collapsed process details; original
answers, full citations and expansion state remain intact. Scope changes close
old streams and invalidate in-flight metadata reads. Cached pre-native phase
rails/grouping are removed, preserving business notes explicitly as historical
annotations without native execution evidence. An absent event is unknown,
never an inferred success. This integration is a release candidate until the
matching installed, preview and production gates have passed.

### Qualified candidate, not production

Candidate `e6aa708` / SAGE `b8fbae1d` passed the frozen installed full regression:
1,669 passed, 31 unchanged dependency skips, zero failures (446.82 seconds).
The existing identity-evaluation baseline exclusion and descriptor limit were
unchanged. Selected installed work/event/cancellation/observation tests also
passed (180 tests), as did all six browser logic groups. Earlier four failures
remain recorded: three history-store doubles lacked the native state port, and
the process-death assertion predated durable start publication. Corrected tests
use a real empty legacy journal and require the killed first attempt to retain
only `started`, followed by second-attempt start/completion, with no regenerated
canonical answer. The real process test retains the default 60-second lease.

The identical application runtime (`aedde6e`, with only subsequent test changes)
passed isolated real Wiki delivery: full source blocks, 30 native frames for
15 foreground attempts before answer admission, and native background start/
completion before stream closure. Owner-scoped metadata, cross-guest denial,
canonical replay and an actual preview restart preserved the original answer
and native events. Cross-store cancellation still produced HTTP 499, both
destination seals, no canonical answer and stable replay. Test SMTP/Slack were
disabled; the owned preview was stopped after acceptance. Production was not
changed by these checks.

This is not a whole-workflow completion or live visual acceptance claim. Final
release gates still include continuous worker-supervisor health reporting,
authoritative current task/stream state, avoiding closed-history stream replay,
removal of the unreachable old phase renderer, Slack presentation and browser
visual acceptance. Missing native attempt evidence must remain unknown, never
inferred completion.

## Native continuous lifecycle and current task snapshots

The next candidate pins SAGE `34f65c41`. Native `WorkService.status` and
`AsyncWorkService.status` expose lifecycle/admission and owner-loop status without
business payloads or exception text. `WorkJournal.status` reads actual task state,
attempt, due/lease state and dependencies; expired running work remains running
until reclaimed. Claimability uses the native claim predicate, not a second Twin
scheduler. A historical failure code does not override a later successful state.

Twin composes these public ports for post-answer, feedback, Wiki/private indexing,
booking notification, HTTP ingress/cancellation and Slack execution/verification.
Known application or HTTP/cancellation worker failure makes `/health` return 503
with degraded readiness. Optional Slack worker failure has its own readiness and
admission gate; it does not label a healthy web ingress unavailable. Unstarted
application workers are explicitly `not_started`, not certified ready. Lifecycle
readiness is not a handler-progress heartbeat or an external-delivery receipt.
Detailed native lifecycle/error type is in authorized diagnostics, not public
health responses. Health reads do not dispatch work.

Owner-authorized exchange metadata now carries native current task status and
actual stream closure. The browser shows snapshot state separately from attempt
history and opens no SSE replay for an already-closed stream. New attempt events
trigger metadata reads, never another model call or poller. Request/view/identity
guards discard late reads. The unused phase rail, phase classifier and associated
step-name-derived renderer have been deleted; legacy cache migration still removes
their previously saved markup without changing answers or citations.

Failure injection also exposed a foundation retirement boundary: a failed but
explicitly drained supervisor must be able to release resources once while its
failure remains observable. SAGE now separates `drained` from failure phase;
undrained work still prevents retirement. Twin does not catch and conceal that
failure to bypass native drain.

This section describes the candidate contract; matching full regression, preview,
production and visual gates must be recorded before promotion is claimed.

### Continuous-status qualification

`f6cda6a` / SAGE `34f65c41` passed the frozen installed full regression:
1,677 passed, 31 unchanged skips, zero failures (451.39 seconds). The baseline
identity-evaluation exclusion and descriptor limit were unchanged. SAGE status,
work, event, cancellation and dependency families passed both source (202 tests)
and independent installed-package qualification (202 tests). Twin's focused
permission/startup/runtime/status suites passed 179 tests; six browser logic
groups passed. No test downgrade, source overlay on the installed SAGE package
or application runtime patch was used. Model fixtures in unit/integration tests
remain distinct from the real preview; runtime model/provider settings were not
changed.

Real isolated Wiki delivery took 9.0 seconds for the specific acceptance question;
canonical replay took 0.12 seconds. Full source blocks, 30 native foreground
frames, durable background acknowledgement, owner isolation, actual task completion
and stream closure were verified. A second question followed by an actual owned
preview restart preserved the same canonical answer, sources and native state
(6.87 seconds initial, 0.17 seconds replay). Cross-store cancellation retained
HTTP 499, both destination seals, no canonical answer and stable replay (0.39
seconds). These timings are functional acceptance observations, not matched
performance benchmarks.

The served JavaScript hash matched the immutable release and both assets carried
`native-status-20261019`. Preview health reported independent HTTP/background
readiness; Slack/SMTP were disabled. The owned preview was stopped after tests,
with its data retained. Production remained unchanged. Browser visual acceptance
still requires the user's unlocked desktop session; source, numeric color and
Node checks do not substitute for that evidence. Slack presentation, final
incremental-knowledge/recovery audit and production promotion remain open.
