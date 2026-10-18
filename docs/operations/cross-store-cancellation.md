# Native cross-store cancellation

Twin's HTTP workflow uses one SAGE `CancellationService`. The former direct
origin-journal cancellation path is removed. Read-only/isolated workflows with no
remote effects use an empty destination declaration on that same coordinator.

The production destination declaration is provided by `DigitalTwinService`:

- Bookings: conversational draft capture/plan, booking effect plus notification
  intent, and draft settlement share the booking store's local effect guard.
- Private knowledge: the canonical SageVDB document, operation receipt and index
  intent share the knowledge catalog's local effect guard.

Scopes come from the server-captured owner-bound `DeliveryIntent.stream_id`, not
client-provided authority. Each invocation/replay checks its guard inside the
destination transaction. Separate administrative actions without that chat scope
remain separate operations; callers cannot supply a cancellable scope while
silently falling back to an unkeyed booking/knowledge write.

Cancellation first durably admits native target-seal tasks. The coordinator waits
for every target, rechecks stable store identities, then cancels the origin task
while respecting canonical-answer protection. All stores remain borrowed; HTTP
shutdown drains its worker and coordinator before closing business stores.
Startup recovers admitted cancellations before producer claims. The native task
selector pauses producer retries and terminal-event claims while cancellation is
pending; a sealed-effect error cannot become an early cancellation success or
trigger repeated model generation.

`POST /chat/cancel` still resolves current transport identity and checks source
ownership. Its response now distinguishes:

| Result | HTTP | Meaning |
| --- | --- | --- |
| `cancelled` | 200 | All declared targets sealed and origin cancellation durable. |
| `cancelling` | 202 | Admitted, but not yet confirmed; `cancelled` is false. |
| `protected`, `terminal` | 200 | A completed/protected result was not cancelled. |
| `missing` | 200 | No owned source request found; no cancellation acknowledged. |

The browser aborts its local receive request but waits separately for the bounded
cancellation acknowledgement. A disconnected page, failed request or 202 response
does not render “confirmed stopped.” Native async ports retain ownership of any
storage/drain call through repeated asyncio observer cancellation.

Cancellation is not rollback: already committed reservations/documents and their
index or mail outbox intents remain. No existing email can be recalled and no
external exactly-once guarantee is introduced. Old state/audit records are not
deleted. Database replacement or restoration still requires stopped writers and
an explicit consistent recovery procedure.

## Acceptance boundary

Tests exercise actual Twin destinations in separate SQLite databases, including
stale synchronous threads after cancellation acknowledgement; capture/book/settle/
knowledge writes; real SAGE booking-stage scope propagation; partial destination
outages with no premature terminal event; HTTP 202/200 replay; native intent
recovery after service reconstruction; and ownership/canonical-answer protection.
Browser tests independently check acknowledgement-vs-local-abort presentation.
Source/installed tests, frozen full regression, isolated live preview and production
acceptance must be recorded separately; the implementation alone is not a release.
