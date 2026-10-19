# Chrome public first paint and acceptance

## Findings and repair (2026-10-19)

There were two distinct observations; they must not be collapsed into a claim
that SAGE or the model server had deadlocked.

1. The HTML hid the entire document with `html { opacity: 0 }`. Visibility was
   restored only by a later script waiting for fonts or a timer. That script was
   itself after stylesheet links. When execution was unavailable, even the
   static interface stayed invisible. The repair removes global hiding entirely;
   the existing early theme bootstrap and nonblocking optional fonts remain.
2. The DOM automation bridge timed out even when Chrome's native accessibility
   tree and screenshot showed a working public page. Some controlled targets
   also returned empty/stale page state or stopped accepting input. Releasing
   only the owned Twin test targets, opening the public page through the native
   foreground address bar, and rebinding after the human activated Chrome made
   direct browser acceptance possible. This is an operational recovery, not a
   claimed repair of the bridge's unknown internal implementation.

Neither a healthy API nor a passing loopback browser test clears a public visual
gate. Equally, an automation timeout alone is not proof of a website deadlock.
The exact internal cause of the bridge timeout was not established. No browser
profile, cookies, history, extensions or security settings were cleared/changed.
Unrelated OpenReview tabs were preserved. The temporary JavaScript-disabled
qualification setting was explicitly restored before normal acceptance.

## Reproducible checks

- The new startup-visibility regression rejects the old HTML and passes the new
  HTML. All eight frontend Node groups pass.
- In Chrome with JavaScript explicitly disabled on the owned qualification
  target, old public HTML produces a white viewport. The new candidate still
  displays its static header, navigation and composer. This checks visibility,
  not functioning chat without JavaScript.
- The exact release `ee9f1c487068cc4b2a882e022c34fcc366321bcd` passed a fresh
  locked full suite: 1,730 passed, 31 existing skips, zero failures/errors,
  502.72 seconds. The baseline identity-evaluation exclusion was unchanged.
- Production uses the same SAGE `dc92f393` dependency and model configuration.
  All three stores were backed up after writers stopped, before app/Wiki release
  pointers were switched. HTTP/background/Slack health are ready, restart count
  zero, Wiki timer waiting. Public HTML SHA-256 equals the committed HTML.
- Native public Chrome acceptance saw the complete ECPA/StateAxis Wiki answer in
  9.3 seconds. After the release switch and refresh, that same answer was restored
  from history without reasking; full source links, table and 15 native execution
  records remained. Clicking the retrieval icon opened its exact attempt details.
  Both themes were actually rendered and captured, not inferred from CSS.
- A new role-specific Wiki starter question was clicked and sent after production
  switching; the complete answer arrived in 10.3 seconds with the full Wiki
  source, table, full links and 12 native nodes. This was not a replay or API-only
  check. The earlier 9.3-second answer was separately restored from history.

The visibility patch alone did not clear every failed controlled target: one
post-switch refresh was still blank and was retained as a failed observation.
Only after releasing the owned test pages, using a fresh native foreground
target, and the human's explicit unlock/Chrome activation were refresh, restored
history, exact node-detail clicking, theme switching and a fresh question accepted.
That sequence must not be rewritten as proof that a font timer alone caused all
earlier white-screen observations. The saved browser group was preserved rather
than accepting Chrome's cross-device group-deletion prompt.

## Browser-control discipline

Use a fresh observation after user activation or a changed-window warning. Rebind
the native app if its binding is stale. Refresh accessibility indices after any
DOM-changing action; do not chain old indices across sidebar toggles. If a DOM
bridge stalls, inspect the native window rather than repeating the same failing
call or restarting the whole browser. Ask the human to unlock/activate when input
cannot be delivered; do not bypass a lock or infer lock status from missing data.
Retain failed observations alongside successful ones and distinguish actual
public evidence from candidate-only evidence.
