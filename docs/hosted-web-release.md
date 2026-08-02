# Hosted/Web Release Installer

This is the release-facing one-shot path for deploying Sage Mate in hosted/web mode
on fresh Linux servers. It supports NVIDIA/CUDA, Ascend/NPU, and local-only hosted/web installs.
It delegates the actual install/runtime work to the repository-maintained `quickstart.sh`,
`manage.sh`, systemd user units, and pinned submodule checkouts.

## One-Line Install

From a fresh server:

```bash
mkdir -p "$HOME"
curl -fsSL https://raw.githubusercontent.com/SAGE-Research/sage-mate/main/release/hosted-web.sh \
  -o /tmp/hosted-web.sh
FACULTY_TWIN_SECRETS_KEY_FILE="$HOME/.config/sage-mate/release-secrets.key" \
  bash /tmp/hosted-web.sh --no-tunnel --yes
```

The installer clones or fast-forwards `SAGE-Research/sage-mate`, initializes submodules,
configures hosted/web safety defaults, installs pinned runtime dependencies for the selected
accelerator, installs systemd user units, starts the stack, configures the Cloudflare tunnel when
credentials are available, and runs `./manage.sh verify-hosted-web`.

## Accelerator Selection

`--accelerator auto` is the default:

- NVIDIA/CUDA hosts use `--with-nvidia-vllm-engine` and pinned `deps/vllm-hust`.
- Ascend/NPU hosts use `--with-vllm-engine` and pinned `deps/vllm-hust-dev-hub`,
  `deps/vllm-hust`, and `deps/vllm-ascend-hust`.
- Hosts without local inference hardware can use `--accelerator none` and point
  `DIGITAL_TWIN_LLM_BASE_URL` at an external OpenAI-compatible endpoint.

Convenience wrappers are also published:

```bash
bash /tmp/hosted-web.sh --accelerator nvidia --yes
bash /tmp/hosted-web.sh --accelerator ascend --model /path/to/model \
  --tensor-parallel-size 1 --no-tunnel --yes
```

## Model Presets

```bash
# Auto: NVIDIA selects a preset from detected capacity. Ascend requires --model.
bash /tmp/hosted-web.sh --yes

# Explicit large dual-A100 preset.
bash /tmp/hosted-web.sh --accelerator nvidia --model-preset qwen3-next-80b-awq --yes

# More conservative Qwen3 preset.
bash /tmp/hosted-web.sh --model-preset qwen3-32b --yes

# Small, faster smoke-test preset.
bash /tmp/hosted-web.sh --accelerator nvidia --model-preset qwen2.5-14b-awq --yes
```

For custom models, the served model name defaults to the exact model value so the deployment does
not create a misleading alias:

```bash
bash /tmp/hosted-web.sh \
  --accelerator nvidia \
  --model Qwen/Qwen3-32B \
  --served-model-name Qwen/Qwen3-32B \
  --tensor-parallel-size 2 \
  --yes
```

## Safety Guarantees

The installer writes these hosted/web settings before calling `quickstart.sh`:

```bash
DIGITAL_TWIN_DEPLOYMENT_MODE=hosted
DIGITAL_TWIN_APP_PROFILE=faculty_twin
DIGITAL_TWIN_CODE_WORKBENCH_ENABLED=false
DIGITAL_TWIN_CODE_WORKSPACE_ROOTS=
```

It does not enable local Code Assistant, local repo editing, server folder selection, or local
command execution. It chooses exactly one local inference path based on `--accelerator`; NVIDIA uses
`--with-nvidia-vllm-engine`, and Ascend uses `--with-vllm-engine`.

## Network And Secrets

- `HF_ENDPOINT` defaults to `https://hf-mirror.com` for faster model access from China-region
  networks. Override it if needed.
- `HF_HUB_DISABLE_XET` is honored when set, but the release installer does not force it because
  some large community quantized snapshots are Xet-backed.
- `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, GitHub tokens, Cloudflare tunnel tokens, and API keys are
  honored from the environment or `.env` but are never printed by the installer.
- Release secrets can be shipped as encrypted ciphertext. Put a filled plaintext copy at
  `release/secrets.env`, encrypt it, and commit/publish only `release/secrets.env.enc`:

  ```bash
  openssl enc -aes-256-cbc -pbkdf2 -salt \
    -in release/secrets.env \
    -out release/secrets.env.enc \
    -pass file:/secure/faculty-twin-secrets.key
  ```

  Deploy with the decrypt key kept outside the repository:

  ```bash
  FACULTY_TWIN_SECRETS_KEY_FILE=/secure/faculty-twin-secrets.key \
    bash /tmp/hosted-web.sh --yes
  ```

  For example, a per-user key path can be:

  ```bash
  $HOME/.config/sage-mate/release-secrets.key
  ```

  The installer decrypts to a temporary `0600` file, merges values into `.env`, deletes the
  temporary file, and never prints secret values. A public release cannot safely contain both the
  ciphertext and the decrypt key; keep the key in server provisioning or CI secrets.
- Cloudflare tunnel mode requires `--public-hostname` (or the equivalent `.env` setting). The installer creates or reuses a
  CLI-managed named tunnel, writes a private runtime config file, routes DNS with
  `cloudflared tunnel route dns --overwrite-dns`, and verifies the public URL.
- Use `--no-tunnel` for local-only installs, or `--public-hostname HOSTNAME --tunnel-name NAME` for
  another domain/tunnel.

## Network topology

Configure `APP_HOST`/`APP_PORT`, `SITE_HOST`/`SITE_PORT`, `VLLM_PROXY_HOST`/`VLLM_PROXY_PORT`,
and the accelerator engine address in the destination machine's ignored `.env`. The tracked
systemd units do not embed these values.

## Verification

After install:

```bash
cd "$HOME/sage-mate"
./manage.sh status --with-vllm-proxy --with-site-proxy --with-nvidia-vllm-engine
./manage.sh verify-hosted-web \
  --app-url "http://$APP_HEALTH_HOST:$APP_PORT" \
  --public-url "https://$FACULTY_TWIN_PUBLIC_HOSTNAME/"
curl --noproxy '*' -fsS "http://$APP_HEALTH_HOST:$APP_PORT/healthz"
curl --noproxy '*' -fsS "https://$FACULTY_TWIN_PUBLIC_HOSTNAME/healthz"
```

If a shell has `HTTP_PROXY`/`HTTPS_PROXY` set, use `--noproxy '*'` for local `curl` checks.
