# Twin-only emergency egress — 2026-10-30

Temporary recovery for a school egress outage. This is an operational recovery kit,
not a replacement deployment of the application or the answer-quality candidate.

## Scope and dependency

- Public Twin → dedicated Cloudflare tunnel on the Mac → SSH local forward
  `127.0.0.1:35731` → A100 `127.0.0.1:55601`.
- Twin HTTP clients → A100 loopback `127.0.0.1:17898` → SSH remote forward →
  existing Mac HTTP proxy `127.0.0.1:7897`.
- Only `twin.sage.org.ai` CNAME changes. The original shared tunnel and all its
  other hostnames are untouched. No host routes, shared DNS resolvers, firewall,
  gateway, Clash rules, model configuration, or model service restarts.
- Mac login session, Mac network, its existing HTTP proxy and SSH access to A100
  must remain available. Sleep/logout/offline interrupts this temporary route.
  LaunchAgents restart failed connector/SSH processes; they do not make the Mac
  an independent always-on server. This dependency is intentional and temporary.
- Forward listeners bind to loopback. TLS verification remains enabled. Private
  Cloudflare state stays in `~/.local/share/sage-twin-emergency/` (0700 directory,
  0600 credentials). Existing account credentials are read in memory from `~/.env`.
  No secrets belong in Git or command arguments.
- Transport reconnects do not replay POST requests. Do not use this kit to retry
  application requests whose acknowledgement was lost.

## Installed components

Mac LaunchAgents:

- `org.sage.twin.emergency-bridge`: `ssh -NT`, BatchMode,
  ExitOnForwardFailure, ServerAliveInterval=15, ServerAliveCountMax=3;
  local forward 35731→55601 and remote forward 17898→7897.
- `org.sage.twin.emergency-ingress`: dedicated `cloudflared` tunnel, HTTP/2,
  metrics bound to `127.0.0.1:37901`; ingress allows only Twin, otherwise 404.
- Official cloudflared 2026.10.6 Darwin arm64 artifact verified against release
  SHA256 `ea1e5105d35bbe16cee5b2f7b4742059ada341fbd35938ed827c24b85efa51c8`.

A100 service drop-in: `sage-mate-app.service.d/emergency-egress-20261030.conf`.
It sets HTTP(S)/Tavily proxies for this service only, and bypasses localhost and
its observed internal embedding/reranker endpoint. Other processes do not inherit it.

Production backend remains pinned to `e5878bf72ac340b87045e6ab15750800a6a26837`.
An additional nine-line credential alias backport from main lets AppSettings read
`TAVILY_TOKEN` in memory, while preserving an explicit application key. Without
this backport, restored networking still falls back to irrelevant Bing matches.
No evidence validation or answer timeout is relaxed.

## Inspect / rollback

Run from this directory on the Mac:

```sh
python3 cloudflare_bridge.py status
curl -fsS http://127.0.0.1:37901/ready
curl -fsS https://twin.sage.org.ai/health
```

Rollback, after the original school path is usable:

```sh
bash rollback.sh
```

Rollback restores the saved Twin DNS record, restores the original credential
configuration, removes only the dedicated app proxy drop-in, restarts only the
Twin app, and unloads the two Mac agents. It refuses to overwrite independently
changed DNS or app files. Backups and the inactive dedicated tunnel are retained
for recovery. Rolling back while the original egress remains broken restores the
old outage; it is not a second healthy fallback.

The scripts deliberately pin this incident's hostname and backend revision to
prevent an accidental broader cutover. Review pins before reusing elsewhere.

## Qualification boundaries

Cloudflare healthy connections and HTTP 200 are transport evidence only. The
acceptance record separately reports production search results, public browser
question results, and remaining answer-quality failures. No browser failure may
be relabeled as passed because a command-line probe succeeded.
