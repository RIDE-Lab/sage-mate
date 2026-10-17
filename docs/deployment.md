# Deployment Guide

This document describes the generic deployment shape for `sage-mate` in the RIDE Lab
organization.

## Wiki knowledge sync

The optional `sage-mate-wiki-sync.timer` ingests the `main` branch of
`RIDE-Lab/sage-wiki` into the live application's knowledge store. Its oneshot
must run `tools/sync_wiki_kb.sh` from the same release as
`sage-mate-app.service` and use a Python environment with the production KB
backend installed. Set `SAGE_MATE_PYTHON_BIN` accordingly; when the timer is
used, set `SAGE_MATE_RESTART_AFTER_WIKI_SYNC=1` and `SAGE_MATE_HEALTH_URL` to
the app's loopback `/health` URL. The optional
`SAGE_MATE_HEALTH_WAIT_SECONDS` defaults to 420 seconds. Keep the runtime
directory and `.env` consistent with the app service.

The script logs the source revision, verifies a clean `main` checkout, and
retries a failed source pull on the next timer run. It rebuilds the index and
refreshes the application only when Wiki records actually change. A successful
no-change run must report all pages skipped and leave the app PID unchanged.
After a changed run, verify the oneshot result, app health, and a real Wiki
retrieval; a successful script run alone is not sufficient.

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

### Ascend 生产运行时身份

线上页面和运维收据必须分开显示以下三层，不能用一个 `v0.23.0`
概括整个推理栈：

- `VLLM_ENGINE_COMPATIBILITY_BASE`：官方镜像/依赖的稳定兼容基座；
- `VLLM_ENGINE_CORE_SOURCE_VERSION`、`VLLM_ENGINE_CORE_COMMIT`、
  `VLLM_ENGINE_PLUGIN_SOURCE_VERSION`、`VLLM_ENGINE_PLUGIN_COMMIT`：实际挂载并运行的
  core/plugin 源码快照；
- `VLLM_ENGINE_IMAGE`、`VLLM_ENGINE_EXPECTED_IMAGE_ID`、
  `VLLM_ENGINE_IMAGE_BUILD_TIME`：本机实际运行的派生镜像身份。

这些值属于每台机器的部署合同，应写入 ignored `.env`，不得硬编码进应用。
`/stack/versions` 和系统状态抽屉会按上述语义展示它们。版本化 deployment receipt
负责记录模型、源码 commits、拓扑、图模式和推测解码状态；镜像 ID、构建时间和源码
package version 由运行时合同补足。dev-hub 中的
[`docs/sage-mate-production-runtime.md`](../deps/vllm-hust-dev-hub/docs/sage-mate-production-runtime.md)
说明 production lock 字段、OCI labels、构建、验收和回滚流程。

升级时先保留旧 image ID、gitlinks 和 receipt，再构建新候选；只有新候选完成两次
冷启动、health/models、真实 completion、并发、取消和公网问答后才能切换。失败时
恢复记录的旧 image ID 与 gitlinks，并仍通过 `tools/lock_sage_mate_engine.sh` 受管
重启，禁止留下临时后台进程。

当前 production lock 选择的 latest-HUST-main 原子组合为 core
`88e606d0f0cde63c412db456f3e92da2609e0438`、plugin merged-main
`c0d6294bc30f775151dba256d49a37a29ba939d7` 和 dev-hub
`337757b23c3a78580d5c65f41150c3e082cdbe50`。对应不可变镜像标签为
`sage-mate/vllm-ascend-hust:core-88e606d0-plugin-c0d6294b-cann9.1`；实际部署仍必须
同时锁定并核对 image ID。plugin 的 verified-core 声明、dev-hub production lock
和本仓三个 gitlink 必须一起推进，禁止只更新其中一层。官方 `v0.23.0` 仅标识
openEuler/CANN filesystem 基座，不代表当前 core 或 plugin 的源码版本。

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

For the A6000-5 Qwen retrieval pool, use
[`deploy/env/twin-a6000-retrieval.env.example`](../deploy/env/twin-a6000-retrieval.env.example)
and the corresponding
[`systemd` drop-in](../deploy/systemd/user/sage-mate-app-retrieval.conf.example).
Keep the installed environment file outside Git; the two model-service API
keys are independent of `DIGITAL_TWIN_API_KEY` and may be omitted for the
private-LAN endpoints. Do not send Twin's own API key to either model server.

With `sage-anns`, SageVDB serializes the ANN index and metadata beneath
`DIGITAL_TWIN_KNOWLEDGE_SAGEVDB_PERSISTENCE_DIR/sage-anns` (default:
`knowledge_base_dir/.sagevdb-index/sage-anns`). On startup Twin loads a
checksum-verified generation when the embedding backend/model/dimension,
algorithm, document order/content, and metadata still match. A change or
corruption rebuilds and publishes a new generation atomically. The previous
generation is retained for recovery; index files are never stored in Git.

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
`DIGITAL_TWIN_PROMPT_SOFT_CAP`) are read when application settings are created.
`tools/run_app_server.sh` exports `.env` into the process environment before
launching uvicorn, so both settings-backed and module-level latency controls
see one consistent deployment contract. If you write your own launcher, load
the same environment before importing the application.

### Slack workspace authorization migration

Slack bindings use the signature-verified `team_id` and `user_id` together.
`SLACK_TWIN_ALLOWED_USER_IDS` accepts comma-separated workspace-qualified pairs
such as `T123:U456,T789:U012`. A bare user ID grants no access; an empty list
requires account binding. The configuration helper no longer grants a default
user access. Keep signing secrets and bot tokens in the deployment's secret
environment; do not put them in test fixtures or migration reports.

The legacy SAGE-backed `bindings.sqlite3` state upgrades to schema version 2. Old links
without verified workspace identity are retained under `unscoped_links`, but
cannot authorize requests. Users must generate a new code in the authenticated
Twin web UI and bind again in their intended workspace. One website account has
one active Slack binding; rebinding revokes its previous identity atomically.
Current account membership is still checked for every authorized request.

At startup its snapshot migrates once into the conversation service's shared
SAGE state, which also owns the Slack work journal. The original database remains
a rollback source, not a second active binding store. A stored source digest
prevents a later startup from overwriting newer canonical bindings; a changed
legacy source fails closed and requires operator review.

Before release, stop old binding writers and snapshot both the binding directory
and shared conversation state consistently, including SQLite sidecars. Validate
upgrade on a copy first; do not run old and new writers concurrently. Legacy JSON originals are
archived under `legacy-imported`, not deleted. Rollback requires the matching
pre-upgrade code and consistent storage snapshot, not just an older application
binary. No automatic workspace assignment or legacy authorization fallback is
provided. Verify both signed command and event paths, and confirm that the same
user ID in another workspace cannot reuse the binding or allowlist entry.

### Durable Slack workflow and transport

`slack_workflow.SlackWorkflow` admits answer and delivery intents together into
SAGE's journal. Its strict `SlackIngress` accepts verified destination IDs,
stable transport IDs and the question, not a bot token or bearer response URL.
Duplicate transport IDs with changed input fail rather than overwrite work.
The host must supply current authorization, canonical recovery, answer and
delivery ports. `SlackAnswerRecovery` reads the same owner-scoped answer events
committed by the chat service; it does not run a second chat pipeline.

Delivery failures retry separately from an acknowledged answer. Both stages
recheck authorization; only a validated accepted receipt marks delivery done.
The journal declares `deliver` depends on `answer`; SAGE only leases delivery
after answer completion. Dependency waiting is not a failed attempt and does
not incur transport retry backoff. Startup upgrades old independent Slack task
records before starting consumers. Stop old workers first: live leases and
inconsistent completed-child states reject migration, and dependency-unaware
SAGE versions cannot read the new version-2 graph records. Roll back code and
the matching consistent state snapshot together, not just the dependency pin.
External Slack effects are still at-least-once: a lost remote acknowledgement
can cause duplicates. A success response is not proof that an ephemeral message
was seen. The event loop, shared state and async clients must outlive successful
workflow drain.

The signed command/event routes now admit this workflow before acknowledging a
question. Commands require Slack's `trigger_id`; events require `event_id`.
Retries resume the same durable record, including events bearing a retry header.
Startup recovers the journal and shutdown drains it before closing model clients
or state. The old request-local `BackgroundTasks` answer/callback path is removed.

Configure `SLACK_TWIN_TEAM_ID` together with the bot token and signing secret in
the service environment. Startup uses `auth.test` to reject a token from another
workspace before starting the service. Without workspace credentials the Slack
routes return 503 rather than acknowledging work they cannot deliver. The helper
`manage.sh configure-slack-twin` reads the signing secret from its environment,
not command arguments, and requires the workspace ID. It must be run with a
securely populated environment; never paste credentials into command history.

Slash replies use `chat.postEphemeral` with the original user and channel; DM and
mention answers use `chat.postMessage` and preserve thread identity. Binding
notices in public channels are ephemeral, and one-time binding codes are not
stored in the work journal. Consuming a code, replacing its binding and admitting
the success notice now share a single SAGE state transaction. A replay receipt
stores only the code digest and verified identity; retries cannot consume again
or restore an obsolete binding. Failure rolls back both binding and intent.
Lost acknowledgement after commit leaves the notice recoverable at startup.
Slash commands acknowledge the durable binding request; the result is sent by
the same notification worker as events, rather than a separate inline path.

As documented by [Slack](https://docs.slack.dev/reference/methods/chat.postEphemeral/),
ephemeral visibility requires an active channel member and is not guaranteed.
Receipts prove API acceptance, not viewing. Live deployment, real Slack delivery,
capacity/retention and recovery under production traffic still require acceptance;
isolated transport tests do not establish those results.
