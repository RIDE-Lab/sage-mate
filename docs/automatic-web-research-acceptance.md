# Automatic web research release qualification — 2026-10-21

Runtime release: Twin `1a7d1804771c12957239d1ffac633e51056c86d8`,
SAGE `ce7eab7700d4ac1235c830cb1ba242f2ae89251e`, both committed to main.
This report does not change runtime code.

## Architecture and failures corrected

- SAGE owns semantic web decisions, capability checks, observed execution and
  the request deadline; Twin supplies verified ingress scope, HTTP transport and UI.
- Model intent JSON repair previously used answer-generation thinking defaults
  and exhausted its short repair budget. It now uses the same native constrained
  intent protocol. User answer model/thinking/output limits are unchanged.
- Official lookup was outside the shared deadline, and the search SDK could use
  its own 60-second default. All providers now receive remaining time. SAGE's
  bounded read-only executor also bounds caller waiting during blocked DNS;
  timeout does not falsely acknowledge forced cancellation.
- A real browser test found successful web results missing from SAGE's source
  plan: the adapter supplied only local knowledge. Web candidates now enter the
  same immutable coverage and composition workflow, not another answer pipeline.
- Complete candidate links/time remain inspectable even when not selected.
  Selected evidence is distinct from retrieval candidates and from verified truth.
- Removed 1000-character URL slicing; invalid candidates cannot emit successful
  completion receipts. Updated stale tests rather than restoring the removed
  prompt-driven preference override or the old empty-local-index web fallback.

## Automated qualification

- SAGE: 685 passed, 1 existing CI-only skip; focused policy/deadline tests passed.
- Twin: 1812 passed, 31 skipped, no failures, in an independent A100 environment.
  The full suite used frozen `470ba59f`; runtime Python and JavaScript in the
  published release are byte-identical. Subsequent changes were documentation
  and four compact-control/receipt CSS declarations, checked in browser.
- All 16 JavaScript test files passed, including actual status rendering,
  unsafe links, full URLs, historical unknown states, streaming navigation and
  advanced controls. Changed Python files passed Ruff and diff whitespace checks.
- Identity/source tests cover guest, account, API-token and Slack ingress kinds,
  private/channel/attachment boundaries, explicit mode precedence, transport
  failure versus empty results, timeout cleanup, and shared-worker admission.
  Role tests are protocol/unit tests, not a claim of interactive login testing
  for every role or a formal data-loss-prevention guarantee.

## Real requests and browser

Public endpoint qualification after deployment passed all seven synthetic cases:
auto current release, never current release, Wiki-only, private unpublished topic,
stable KV explanation, always public release, always private topic. Release
success required an answer containing the returned full official release URL,
selected answer basis, retrieval time, no uncovered question parts and normal
completion—not merely HTTP 200 or a nonempty refusal.

A fresh Chrome incognito session at `https://twin.sage.org.ai/` used normal page
controls, no interception, mock responses or manual request changes. Default
auto showed real “正在搜索网页”, then the GitHub latest non-prerelease release,
complete link, selected support and timestamp. The page reported 12.5 seconds for
that single default request; it is not a throughput/latency benchmark. The
candidate receipt was opened and its link/time visually inspected. Advanced
never/always and light/dark readability were also exercised on the isolated
candidate with identical runtime code. Public never was separately checked.

Negative outcomes were retained: an earlier candidate browser result fetched
the source but returned only gaps before adapter repair; one later external
lookup timed out around 8 seconds and honestly failed. A separate matched
public-mode browser request and the production always request succeeded.
External-provider availability is not guaranteed and no timeout was enlarged
to manufacture success. Search snippets are not complete fetched articles.

## Deployment and rollback boundary

Only the Twin app was gracefully restarted after a quiet-listener/sync check.
An independent release environment replaced only the SAGE/Twin wheels; existing
native retrieval dependencies were retained. Private data and credentials were
not copied into QA or source. Existing runtime/data/auth/model settings were
checked in memory across cutover. Existing frontend edits were preserved by
verifying they matched the upstream main base before release.

The next Wiki-sync invocation points to the same release; its timer remains
unchanged. Engine, router, Workstation and advisory process identities were
verified unchanged. Public health reported ready and Slack configuration verified.

The operator's deployment receipt records previous/new release paths and a
rollback command. Rollback disables only this release's app and Wiki-sync
overrides, reloads user systemd and restarts Twin against the retained previous
source/venv. It never rolls back user data or restarts shared inference services.
