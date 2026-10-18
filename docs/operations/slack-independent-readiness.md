# Slack independent startup readiness

Twin starts its HTTP workflow independently of Slack network availability.
Slack identity verification is a SAGE `DependencyVerifier` task, backed by the
same durable conversation-state runtime, not an application retry thread or a
second execution engine.

Every process startup creates a fresh verification binding. Existing persisted
success cannot unlock current credentials. Only a successful `auth.test` for the
configured workspace unlocks Slack ingress and native task claims. A changed
in-memory token, signing secret or workspace invalidates readiness immediately.
Credentials remain in process memory and are not stored in the verification
journal or health response.

`GET /health` retains independent HTTP `readiness` and adds `slack_readiness`:

| Slack state | Meaning |
| --- | --- |
| `disabled` | No Slack integration configured. |
| `pending` | Identity not yet verified; transient network errors retry in SAGE. |
| `verified` | This process's current configuration passed verification. |
| `rejected` | Wrong workspace, rejected credentials, or invalid probe result. |
| `configuration_changed` | Configuration no longer matches the verified binding. |
| `not_started`, `superseded`, `closed` | Not usable; never treated as verified. |

Unverified integration requests receive HTTP 503. Requests for a different
workspace still receive 403. Recovered Slack tasks remain pending without
spending attempts while verification is unavailable. Signature checking,
identity binding, membership authorization, and delivery-time authorization
remain in force after startup verification; readiness is not authorization.

Invalid/revoked credentials and workspace mismatches do not retry indefinitely.
After correcting configuration, restart the application to establish a new
binding. A partially configured integration (only some of signing secret, team
ID and bot token) remains a static startup configuration error. No permissive
default workspace or bypass is introduced.

Shutdown drains HTTP and Slack workflows, then the dependency verifier, before
closing borrowed state. Existing pending work and obsolete verification audit
records are retained. This startup check is not a continuous Slack uptime or
token-revocation monitor; later API failures still follow normal delivery policy.

Regression covers real HTTP startup while the probe is blocked, DNS retry and
recovery, wrong workspace and revoked credentials, changed configuration, and
restart recovery with no pre-verification task claims. SAGE separately covers
lease fencing, concurrent replacement and abrupt process death after admission.
Production deployment and real transport acceptance remain separate release gates.

## Release acceptance — 2026-10-19

Deployed Twin `477b305` with SAGE `8f7535f7`, both on main. Installed SAGE runtime
qualification passed 231 tests. Frozen Twin regression passed 1649 tests with
31 existing skips and one existing warning in 372.63 seconds; the pre-existing
identity-eval exclusion was unchanged. These are not model-quality benchmark scores.

The isolated loopback service used actual read-only Slack auth.test, became
verified, then restarted with a different binding and verified again in 8.86
seconds. A held Wiki request replayed the identical answer/exchange/source blocks
after restart in 0.14 seconds. Real public production checks passed after cutover:
HTTP ready, Slack verified, zero supervisor restarts; cross-version canonical
replay 1.26 seconds; fresh full-source Wiki answer 10.34 seconds; SSE completion,
cross-guest isolation and replay 0.73 seconds. No new Slack message was sent.

Writers were stopped for two on-host integrity-checked SQLite backups before
switching the app and Wiki task to the frozen release. Existing authorization
configuration was retained, the Wiki timer restored, and inference services left
untouched. Backups and isolated test state remain private and are not serving data.
