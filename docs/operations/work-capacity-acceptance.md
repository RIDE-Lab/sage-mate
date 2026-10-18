# Durable admission integration (not deployed)

Twin pins SAGE `d00ee1e`, which supplies transactional active-request admission.
No capacity policy is automatically configured at startup. Production remains
on the previous source-plan release pending full release qualification and an
offline policy installation with all relevant namespace writers stopped.

HTTP admission rejection returns 429 with `X-Sage-Error-Code:
work_capacity_exhausted`, Retry-After, and an explicit not-enqueued message.
The newly issued guest cookie is preserved. The streaming transport carries the
same code/status in its terminal error frame; HTTP200 stream establishment is
not successful admission. An admitted observer timeout remains the separate
queued response, with its original request identity.

The browser preserves the request ID during bounded retries. After retries are
exhausted, a known capacity rejection does not keep pretending that a durable
answer is pending. Existing queued requests retain their delivery observer.
Slack command/event and binding admission return retryable 503, not a success
acknowledgement or a promise that an answer will arrive. No outbound Slack call
is made by the rejection path.

Isolated A100 source-overlay qualification: 79 HTTP/Slack/atomic post-answer and
binding integration tests passed in19.18s, including REST and SSE rejection,
guest-cookie preservation and four Slack transport/binding combinations. Five
frontend Node groups passed, including both queued and rejected retry exhaustion.
Ruff, diff-check and offline lock-check passed. The existing Starlette/httpx
deprecation warning remains. These tests are not a frozen clean-release result,
production overload experiment or real Slack delivery acceptance.

An additional35 HTTP/Slack workflow and knowledge-index tests passed in30.82s.
The HTTP producer test installs the actual SAGE policy before startup, holds one
active request at capacity, verifies a second admission is absent from the journal,
and retains duplicate/replay and cancelled-observer behavior.

Initial dependency fetch failed with an HTTP2 framing error. Retrying through
the existing local proxy with HTTP1.1 resolved the exact requested SAGE commit;
no dependency/model fallback was used.

Remaining: full frozen release regression; measure/select ingress capacity;
exercise overload/recovery end-to-end in an isolated service; deploy during a
quiescent maintenance boundary. Do not independently quota downstream finalizers
in a way that prevents already-admitted requests from completing. No TTL,
canonical history deletion or quarantine changes are part of this integration.

## Frozen candidate and real-model gate (2026-10-18)

Independent CPython3.11 release f8f96ec installed147 frozen packages with exact
SAGEd00ee1e (no source overlay). Full regression:1562passed,1failed,31existing
skips in336.11s, with the existing identity-evaluation exclusion unchanged.
The failure was an unintended `code: null` field on ordinary SSE errors. The
fix emits the optional code only when present; the old exact assertion is kept.
Five frontend groups passed on that release.

The isolated real-model capacity1 canary did observe a truthful second-request
429 rejection, but the first request returned413, so the complete canary FAILED.
Question: new members' first-month research steps with complete source links.
Diagnostic-only isolated tracing showed102024source-plan characters: the907-char
onboarding guide, a2937-char research-method page, a2665-char public index and
five unrelated17489–17995-char Prompt Engineering chunks. Upstream returned400
classified as prompt_capacity. The diagnostic regex captured no numeric token
count, so no exact required-context number is claimed.

Read-only inspection of the running control-plane release
`native-budget-router-20261015` confirmed admission estimates complete JSON UTF8
bytes/2, not actual tokenizer counts. This may contribute to conservative
rejection; actual tokenizer comparison is still needed. Its deployed directory
has no Git metadata, so a commit identity has not been inferred from its name.

The clean preview and temporary diagnostic services are stopped (MainPID0).
Production app/router/model services were not changed. Do not deploy on this
failed QA gate or solve it by truncating sources, lowering output budgets, or
substituting an easier question. Relevance and tokenizer-aware admission remain
to be resolved before repeating this same end-to-end canary.

## Corrected frozen qualification and isolated end-to-end result

Twin792505a with locked SAGEd00ee1e, independently installed CPython3.11/147packages,
passed1563tests with31existing skips in330.06s. The pre-existing identity-evaluation
exclusion is unchanged; Starlette/httpx deprecation warning remains. All five Node
groups passed. This supersedes the earlier failed SSE-format gate, not the full
production acceptance checklist.

An isolated gateway03a9377 initially still rejected the question: Twin uses
Responses, which that first fix did not support. Control-plane a5928e8 adds the
qualified stateless text Responses counting mapping after inspecting deployed
vLLM's construct_input_messages and chat renderer. No provider/wire-mode switch.
Eight gateway tests pass, including both transports and verified overflow/outage.

With the corrected isolated gateway, the original capacity1 real-model canary
passed on f8f96ec (7.97s/3.8s), then again after the preview switched to frozen
792505a (5.31s/3.81s): while one request is active, another returns429 explicitly
not enqueued; the latter's unchanged request ID succeeds after release; replay
preserves answer and exchange ID; final active count is0. The selected onboarding,
research-method and public-index source bodies were independently compared with
the canonical read-only Wiki catalog; all are complete with full links, and no
Prompt Engineering document was selected for the answer. These warm/cache-mixed
observations are not a throughput or cold-latency benchmark.

Isolated router metadata confirms quality_input_size_tokenizer_verified with48154
input tokens and200 for the large source plan. An initial audit incorrectly
expected two such calls; repeated plans can reuse cache, and the assertion now
requires observed verified calls without claiming invocation per answer.
Candidate retrieval still includes irrelevant documents before source selection;
this is not claimed resolved by admission or final-answer source checks.

Production remains Twin50306a7 and the original router. Admission limits have
only been installed in the isolated store. Production capacity selection, a
quiescent version6 migration and safe rollback/forward-recovery procedure remain
required; old pre-capacity SAGE binaries must not write version6 state.

## Lossless offline rollback qualification

Twin now pins SAGEb46054f, adding `WorkJournal.disable_capacity()` for the explicit
offline migration back to indexed version5. All namespace writers must be stopped.
It removes only the capacity policy and changes version markers in the same
transaction, preserving canonical answers, task payloads/status/results and active
indexes. Live leases and missing policy with version6 records refuse migration.

129work-family tests passed in2.83s, including fault-injected transactional rollback.
An isolated A100 synthetic SQLite store was opened by the actual installed old
SAGE in Twin50306a7: it rejected version6 before migration, recovered a pending
task after migration, preserved completed receipts/full attachments/canonical
answers, and allowed the current version to re-enable capacity. No production
policy was changed. This avoids restoring an old snapshot over new user data;
it is an offline recovery tool, not a second execution path.

## Production cutover (2026-10-18)

Final frozen Twin `d3fa3fb` / SAGE `b46054f` passed 1563 tests, 31 existing skips
in 333.25s. The identity-evaluation exclusion and Starlette warning are unchanged.
Control-plane `a5928e8` was installed in an independent pinned environment; installed
router/admission source hashes matched the release and its standalone regression
passed before switching the current symlink. No GPU engine, model, credentials or
routing configuration changed.

With Wiki synchronization stopped and no pending/running work, the app stopped
gracefully, the state database was backed up, and SAGE configured HTTP and Slack
ingress namespaces with limit 8 each. Nonsecret release pointers were installed,
the app became ready, and the Wiki timer was restored. Read-only policy inspection
found both limits persisted, active lists empty, SQLite integrity OK. Ownership
inspection found 12 records and 22 profiles, none without owner, and no legacy
import directory. Quarantined test/unknown records were not imported or deleted.

Mac-origin public original onboarding QA passed before/after cutover in
6.90s/11.26s. All three selected complete source bodies matched the independent
canonical public catalog, full source links were present, and no Prompt Engineering
source was selected. Overview QA passed in 5.69s with terminal SSE and cross-guest
denial. These are observed warm/cache-mixed requests, not a latency benchmark.
A100-origin public probing failed with TLS ConnectTimeout and a separate curl
DNS-resolution timeout; this is retained as a failed network-path probe, not
counted as a successful API check.

After a drained controlled application restart, the same public request returned
200 in 2.37s with identical canonical answer and exchange ID. Two real research
questions (StateAxis review and ECPA/StateAxis validation boundaries) passed in
10.20s and 8.70s. Both preserved the independently compared complete system-overview
source and labelled suggestions as non-source inference. Manual output review found
no claim that unpublished measurements imply experiments were never performed.
The second answer used English experiment-field labels in otherwise Chinese text;
this remains a presentation-quality limitation, not a factual-boundary failure.
These checks qualify the observed questions, not arbitrary model reasoning.

Rollback must stop all writers and use current SAGE `disable_capacity()` before
starting old pre-v6 binaries; do not overwrite new answers with the backup.
Candidate retrieval precision, real Slack delivery, visual browser acceptance,
and broader storage/retention policy remain distinct open items.
