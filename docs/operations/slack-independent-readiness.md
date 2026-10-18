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
