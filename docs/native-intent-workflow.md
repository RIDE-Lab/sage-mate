# Intent branches and compact execution view

The chat workflow uses SAGE's native `filter` and `connect`, not frontend-only
node suppression. Complementary guards read the captured interaction intent.
Ordinary questions bypass booking preparation and submission. Booking requests
prepare a captured decision; only a complete request reaches submission. Missing
fields continue to the answer path without submitting. Pending booking followups
still use the existing intent resolver, cancellation and settlement protocol.

This requires SAGE dc92f393, whose ConnectedStreams implementation preserves
multi-upstream transformation and connect composition. A merge is not a barrier
or deduplicator: exactly one result depends on complementary predicates.

The view renders authoritative native node/edge identities as compact icons.
Short Chinese captions are presentation only; full names, attempts, metadata and
unobserved declared nodes remain available. Nested workflows are folded by
default. Clicking a node (or Enter/Space) opens its exact native attempt details.
No relationship or completion state is inferred from captions or timing.

Qualification must cover ordinary questions, incomplete/complete booking,
concurrent mixed intents, cancellation and exactly one render; plus live theme,
narrow-screen, history and node-detail interactions. Unit results alone do not
qualify production rollout.
