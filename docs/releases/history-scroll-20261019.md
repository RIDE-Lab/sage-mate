# History scrolling correction

Restoring local saved HTML called `syncConversationMode`, but left the shell's
`chat-empty` class unchanged. Its centered flex layout could place overflow
above the scroll origin. Layout synchronization now updates the shell too,
with safe centering as a defensive CSS fallback and non-shrinking messages.

The custom scrollbar also suppressed the native bar while its own hidden rail
disabled pointer events. It has been removed, including listeners and mutation
observers. A native, edge-aligned scrollbar and focusable conversation region
provide wheel, touch, drag and keyboard access without a second scroll model.

Qualification: 11 Node behavior suites and 39 frontend contracts passed.
An eight-round synthetic browser fixture restored 16 messages, reached scroll
origin 0 and bottom 2708, and retained a reachable first message in dark mode.
These are frontend-only checks, not a new full backend qualification.

Deployment is frontend-only: retain the running backend revision and PID, back
up the three changed web assets, publish the committed versions, and record
the separate frontend revision. No history, model or backend data migration.
