# Deployment Guide

This document describes the generic deployment shape for `sage-mate` in the RIDE Lab
organization.

For a fresh-machine bring-up, the fastest path is:

```bash
git clone https://github.com/RIDE-Lab/sage-mate.git
cd sage-mate
./quickstart.sh --target hosted-web
./quickstart.sh --with-vllm      # also pull and editable-install vllm-hust
./quickstart.sh --start          # install + start the three user services
```

`quickstart.sh` is idempotent. It fills in missing `.env` keys and, for the explicit/default
`hosted-web` target, enforces the deployment-mode/code-tool safety keys so a server install cannot
accidentally expose local repository features. See §7 below for the chunked-transfer / streaming
gotcha that the latency rollout uncovered.

## Portable deployment contract

Tracked code and systemd templates do not select a machine path, accelerator ID, model, domain, or
deployment port. Copy `.env.example` to the ignored `.env` and set the topology for that machine.
In particular, an Ascend launch requires `VLLM_ENGINE_MODEL_PATH` and
`VLLM_ENGINE_NPU_DEVICES`; `VLLM_ENGINE_TP_SIZE` must equal the number of selected devices. The
release installer can choose currently idle NPUs, while the lower-level launcher deliberately
fails instead of guessing.

### Trusted one-shot Ascend CI

Pull requests always use GitHub-hosted runners. The public repository must not
attach a persistent NPU host to `pull_request` jobs. Hardware regression is a
separate `workflow_dispatch` workflow on `main`, routed through the
workflow-restricted `sage-mate-ascend` runner group and the `ascend-npu`
environment.

An operator starts one ephemeral runner and dispatches one job with:

```bash
SAGE_ASCEND_CI_CONTAINER_IMAGE=<locally-installed-ascend-image> \
  tools/run_ascend_ci_once.sh
```

The job checks the trusted revision, Ascend device/control nodes, `npu-smi`,
non-interactive Docker access, container device binding, graph-mode launcher
contracts, and application health. It does not start or replace the managed
model service. Set `VERIFY_ENGINE=true` only when an already-managed engine is
expected to be healthy. The runner deregisters after its single job and its
temporary work directory is removed; diagnostics are retained under the
operator's XDG cache directory.

The loopback addresses and ports shown in `.env.example` are editable sample configuration, not
values embedded in generated systemd units. To move the deployment, copy its secret material
separately and generate a new `.env` for the destination host.

## Deployment Targets

`quickstart.sh` is the single installer entry point, but it has separate targets for the two
product shapes:

- `hosted-web` is the default Linux/server browser deployment. It explicitly sets
  `DIGITAL_TWIN_DEPLOYMENT_MODE=hosted`,
  `DIGITAL_TWIN_APP_PROFILE=faculty_twin`, `DIGITAL_TWIN_CODE_WORKBENCH_ENABLED=false`, and clears
  `DIGITAL_TWIN_CODE_WORKSPACE_ROOTS`.
- `local-mac-app` is the default on macOS. It installs a user Sage Mate runtime, delegates to
  `tools/install_local_code_mode.sh`, and does not install systemd units.
- `mac-dmg` builds the distributable `dist/sage-mate-macos.dmg`.

Examples:

```bash
./quickstart.sh --target hosted-web --start
./quickstart.sh --app-profile code_assistant --workspace "$HOME/my-repo" --start
./quickstart.sh --target mac-dmg
```

## 1. Application Server

Start the FastAPI app directly:

```bash
cd /path/to/sage-mate
PYTHONPATH="$PWD/src:$PWD/../SAGE/src:$PWD/../neuromem:$PWD/../sageVDB" \
python -m uvicorn sage_faculty_twin.api:app --host "$APP_HOST" --port "$APP_PORT"
```

Or use the provided wrapper:

```bash
./tools/run_app_server.sh
```

The wrapper script automatically:

- Sets `PYTHONPATH` to include sibling source checkouts (SAGE, neuromem, sageVDB)
- Validates that `sagevdb` and `sage_anns` are importable
- Auto-installs `isage-vdb` and `isage-anns` from PyPI if either is missing
- Loads `.env` into the process environment before starting uvicorn

## 2. Local Reverse Proxy

The repository includes a repo-local Nginx launcher so you can serve the app and keep a local
homepage compatibility route without writing into system-wide `/etc/nginx`.

```bash
./tools/run_local_proxy.sh
```

Relevant variables:

- `APP_HOST` / `APP_PORT`: upstream application listen address
- `SITE_HOST` / `SITE_PORT`: local proxy listen address
- `DIGITAL_TWIN_HOMEPAGE_PUBLIC_URL`: optional canonical public homepage URL
- `TUNNEL_ORIGIN_HEALTH_URL`: local origin checked before cloudflared starts

`/home` and `/home/` are compatibility paths at the site-proxy layer. They now redirect to the
canonical public homepage origin, while local direct app access can still use the built-in FastAPI
homepage endpoint on `APP_PORT`.

## 3. User Services

To install persistent user services with `systemd --user`:

```bash
./manage.sh install --start
```

This renders and installs:

- `sage-mate-app.service`
- `sage-mate-site.service`

Optional services (pass the flag to include):

- `sage-mate-tunnel.service` (`--with-tunnel`)
- `sage-mate-vllm-openai-proxy.service` (`--with-vllm-proxy`)

The management entry points are:

```bash
./manage.sh status
./manage.sh start
./manage.sh stop
./manage.sh restart
```

## 4. Tunnel Configuration

Production tunnel credentials are runtime-private material. Keep them under
`DIGITAL_TWIN_RUNTIME_DIR`, not under the code repository.

The preferred production shape is token-file mode:

```bash
mkdir -p "$DIGITAL_TWIN_RUNTIME_DIR/cloudflared"
install -m 600 /path/to/cloudflare-tunnel-token "$DIGITAL_TWIN_RUNTIME_DIR/cloudflared/token"
./manage.sh restart --with-tunnel
```

`tools/run_named_tunnel.sh` also supports a named tunnel config file at
`$DIGITAL_TWIN_RUNTIME_DIR/cloudflared/config.yml`, or an explicit
`TUNNEL_CONFIG_PATH`. The repository's `.runtime/cloudflared/config.yml`
fallback is only for local scratch deployments.

Do not commit Cloudflare tokens, origin certificates, or tunnel credentials.

## 5. Public Branding

Set the canonical public homepage URL for the web UI top-bar button with:

```bash
export DIGITAL_TWIN_HOMEPAGE_PUBLIC_URL=https://faculty.example.edu/
```

If the variable is empty, the top-bar homepage link falls back to the app-local `/home/` route.
That fallback is useful for local development, but it should not be treated as the public-facing
homepage URL.

## 6. Upstream Proxy: Body Size and Timeouts

When the deployment chain is `Cloudflare → host nginx → uvicorn`, two
proxy-side knobs commonly cause student-visible failures:

- **HTTP 413 “Request Entity Too Large”** when uploading multi-page PDFs to
  `/chat`. The application accepts up to `MAX_CHAT_ATTACHMENT_BYTES = 5 MB`,
  but stock nginx defaults to `client_max_body_size 1m`, so larger uploads
  are rejected at the proxy before reaching FastAPI.
- **HTTP 504 “Gateway time-out”** when the LLM takes longer than the proxy's
  read timeout. Cloudflare's free tier caps end-to-end requests at
  approximately **100 seconds**; uvicorn typically completes within ~60s but
  occasionally exceeds 90s under load.

### nginx (host) settings

Match or exceed the application limits in the production nginx server block,
for example in `/etc/nginx/sites-available/sage-mate`:

```nginx
http {
    # Allow chat attachments up to slightly above the 5MB application limit.
    client_max_body_size 8m;
    client_body_timeout 120s;

    # Long LLM responses can take 30-60 seconds; do not truncate them.
    proxy_read_timeout 180s;
    proxy_send_timeout 180s;
    proxy_connect_timeout 30s;
    proxy_buffering off;
}
```

The repo-local template at `tools/nginx-local.conf` already ships with these
values. After editing the production config:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### Application-side timeout guard

The FastAPI `/chat` route wraps `service.answer(...)` in
`asyncio.wait_for(...)` with a default budget of **95 seconds** so that the
app returns a structured `504` with a Chinese message before Cloudflare's
edge times out. Override the budget by exporting:

```bash
export DIGITAL_TWIN_CHAT_REQUEST_TIMEOUT_SECONDS=95
```

Keep this value comfortably below Cloudflare's 100s cap. If the upstream LLM
is consistently slower than this budget, lower
`DIGITAL_TWIN_LLM_TIMEOUT_SECONDS` (default 90s) first so the model abandons
before the request budget fires.

### Cloudflare

Cloudflare's edge timeout cannot be increased on free / Pro plans. To
support long-running answers either:

1. Reduce the answer budget (smaller LLM, shorter prompts), or
2. Move long-running endpoints behind WebSocket / Server-Sent Events (the
   `/chat/workflow-events` SSE stream is already exempt from the 100s cap as
   long as bytes flow regularly), or
3. Use a Cloudflare plan that supports custom timeouts.

## 7. Knowledge Backend Stack

The app uses **sagevdb** (vector database) + **SageANNS** (approximate nearest
neighbor search) as its knowledge backend. Both are provided as source checkouts
in sibling directories and auto-managed at startup.

| Component | Source | pip package | Version resolution |
|-----------|--------|-------------|--------------------|
| SageVDB | `../sageVDB` | `isage-vdb` | `sagevdb` pyproject.toml |
| SageANNS | (pure Python) | `isage-anns` | pip metadata |

The local Sage Mate code profile also uses a sibling checkout, `../claude-code-hust`, but only for
`local-mac-app` installs. Hosted web deployments keep code tools disabled and do not install or
start that code backend.

### Configuration (.env)

```bash
DIGITAL_TWIN_KNOWLEDGE_BACKEND=sagevdb      # knowledge backend type
DIGITAL_TWIN_SAGEVDB_BACKEND=sage-anns       # ANNS backend (or "native" for flat index)
DIGITAL_TWIN_SAGEVDB_ANNS_ALGORITHM=faiss_hnsw
DIGITAL_TWIN_SAGEVDB_EMBEDDING_BACKEND=hash
```

### Auto-dependency installation

`tools/run_app_server.sh` validates knowledge backend imports before starting
uvicorn. If `sagevdb` or `sage_anns` is not importable, the script
auto-installs the missing packages from PyPI. No manual `pip install` is ever
required.

### Powered By footer

The web UI footer displays version chips for all stack components:
SAGE, NeuroMem, vLLM-HUST, SageVDB, and SageANNS. Versions are resolved at
runtime via `/stack/versions` (pip metadata → module import → pyproject.toml
parse, in that order).

## 8. Validated answers and optional token streaming

Keep `DIGITAL_TWIN_STREAM_CHAT_ANSWER=false` in normal deployments. Workflow
trace updates still use SSE, while the browser renders only the final answer
after grounding, relevance, and retry validation succeeds. This avoids showing
tokens from an attempt that the workflow later rejects.

The legacy `true` setting may still use streaming transport between Sage Mate
and the upstream model, but `/chat` buffers those chunks and emits only the
validated `answer_done` payload to the browser. Raw model tokens never cross
the public SSE boundary. For upstream streaming transport to work end-to-end,
the OpenAI-compatible endpoint MUST emit `Transfer-Encoding: chunked`.

Reproduction recipe to verify any candidate `LLM_BASE_URL`:

```bash
curl -N -i -H "Authorization: Bearer $KEY" \
     -H 'Content-Type: application/json' \
     --data '{"model":"<model>","stream":true,"max_tokens":50,
              "messages":[{"role":"user","content":"hi"}]}' \
     "$LLM_BASE_URL/chat/completions" | head -c 400
```

Look for these headers:

* `HTTP/1.1 200 OK`  — NOT `HTTP/1.0`
* `Content-Type: text/event-stream; charset=utf-8`
* `Transfer-Encoding: chunked`

**Common foot-gun.** A custom OpenAI router built on Python's stdlib
`http.server.BaseHTTPRequestHandler` defaults to `HTTP/1.0`, which has no
chunked transfer encoding. Such routers buffer the entire response and
flush at the end of generation, defeating per-token streaming even though
vllm itself behaves correctly. Either:

* **point `DIGITAL_TWIN_LLM_BASE_URL` directly at vllm-hust** (recommended),
  or
* fix the proxy to set `protocol_version = "HTTP/1.1"` and forward the
  upstream response with explicit `Transfer-Encoding: chunked` framing.

### .env must be in the process environment, not just the file

The streaming flag and the latency knobs (`DIGITAL_TWIN_STREAM_CHAT_ANSWER`,
`DIGITAL_TWIN_CHAT_REQUEST_TIMEOUT_SECONDS`,
`DIGITAL_TWIN_CHAT_SSE_KEEPALIVE_SECONDS`,
`DIGITAL_TWIN_CHAT_PROMPT_SOFT_CAP_CHARS`) are read at module import time
via `os.environ.get(...)`. They are *not* loaded by pydantic-settings.
`tools/run_app_server.sh` therefore exports `.env` into the process
environment immediately before launching uvicorn so these values reach the
app. If you write your own launcher, do the same — a value that lives only
inside `.env` will be invisible to those code paths.
