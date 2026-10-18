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
