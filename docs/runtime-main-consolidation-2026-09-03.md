# Native-thinking / hybrid-cache main consolidation

Evidence class: `existing-server-probe`. Integration checks are not a comparative
Qwen-versus-GLM benchmark and do not certify every answer's correctness.

## Ownership and intended reproducible set

- Sage Mate native-thinking policy: `d8c74e0`, already published on main.
- HUST core snapshot: `762f85b311fbab0bcf8921dd216f5093cd58b9b8` (unchanged).
- Ascend hybrid-cache snapshot: `4e57439e58ed3d78e675f9fd7b4614fb183c5394`,
  tag `v0.25.1rc1+hust.20260903.4`.
- dev-hub owns `config/vllm-ascend-production-lock.json`, immutable artifact
  hashes, image build recipe and hybrid-cache migration documentation.
- Sage Mate owns the outer gitlinks and its health/chat/receipt verifier.

The initial state was inconsistent: the app main contained native thinking,
but Ascend fixes were on a feature branch, the parent gitlink selected the old
plugin, and the new deployment lock was uncommitted. The integration must be
published inside-out; a running container alone is not a reproducible checkout.

The active core is a tested main snapshot, not a claim that it equals today's
moving main tip. Its verified pairing is explicit in the plugin. No legacy
repository, v0.23 core fallback, eager mode, model change or NPU expansion is
part of this consolidation.

## Verification improvements

`tools/verify_sage_mate_engine.sh` now sends credentials through curl stdin,
requires the configured served model in `/v1/models`, and checks a real
completion for the same model, nonempty final content, normal stop and the
requested `OK` response. A reasoning-only, malformed, truncated or wrong-model
response fails. A deployment receipt cannot be published with the chat probe
disabled. The verifier is not a general answer-quality evaluator.

Run from the parent checkout with its ignored machine-local environment:

```bash
tools/verify_sage_mate_engine.sh
.venv/bin/pytest -q tests/test_engine_chat_probe.py tests/test_deployment_receipts.py
```

## Gates and limits

- Final combined application/frontend/runtime regression:357passed,5skipped. The skips require
  the optional uncached `BAAI/bge-small-zh-v1.5`; no network model download was
  hidden in a test.
- Final verifier/systemd/runtime-identity/receipt regression:81passed.
- dev-hub lock and receipt regression:12passed.
- Final Ascend native allocation and Mamba binding:11passed in a networkless
  container without NPU device mounts. Host driver libraries were mounted
  read-only to satisfy import dependencies; all test tensors were CPU tensors.
- All hooks applicable to the seven changed Ascend files pass, including
  repository-configured Ruff, format, spelling and source-policy checks.
  Gitleaks scanned the changed-commit range with no findings.
- All532 tracked Python package files in the final wheel match its selected
  source;1199 native artifact members match the verified previous wheel.

Hook setup initially timed out downloading Go dependencies. The default hook
also downloaded an x86 gitleaks binary unsuitable for ARM; an ARM build from
pinned Go module v8.24.2, with checksum verification enabled, resolved that
environment failure. These failed attempts are not counted as passing gates.

User-owned `api.py`, `tests/test_chat_streaming.py` and unrelated dev-hub runtime
artifacts remain untouched and excluded. Their existence is reported, not
silently cleaned to make `git status` look clean.

## Operational acceptance

Final-image ID:
`sha256:de1742dd6a1bc7ed1cbfff78d508ffa8ac769e58518d4e04d35a5d8203b88252`.
Container:`733db360b8cdbe57a0d92727ae69e091fbfe5e80c8a029560f4266b7de7c008b`.
Managed container restart began07:46:21Z, health passed07:51:46Z; no automatic
restarts. Existing compile caches were retained, so this is not a cache-cleared
compile-time benchmark. The verifier passed and wrote receipt
`deploy-0c30f1e4f2b63578bd43`.

- Physical NPU0–3 worker PIDs:325653/325654/325655/325656. TP4, graph mode,
  Qwen3.8-27B unchanged. No operations targeted4–7 or the proxy; its start time
  remained2026-09-02 20:30:58CST. Other owners independently changed their
  statecentric containers during the audit; this is not hidden as a stability
  claim for those external workloads.
- Direct normal arithmetic1.50s and native-thinking3.83s both returned391 and
  the requested verification sentence. Native response had119reasoning tokens
  separated from final content. Stream/cancel/two-concurrent probes passed;
  running and waiting requests were both0 afterwards.
- Public normal QA:HTTP200, one real model call, two Support sources;
  server20.84s/client26.27s. Not a latency-quality pass.
- Local application deep QA:HTTP200,23.28s, one native model call, zero retries
  and cache hits, three Support sources/five knowledge hits. It provided two
  clearly labelled inferred groupings and distinguished them from the three
  source-defined research areas; no engine or application fallback was used.
- Public deep QA remains **unverified**: stalled connection, response-header
  timeout despite an application200, then Cloudflare502. Local health stayed
  healthy. Public health did return the correct final plugin/image/receipt.
  These failures are preserved, not rewritten as successful public acceptance.

Evidence artifacts are under `output/main-consolidation-*`, including the
engine verification log, engine probe JSON, public-normal JSON and FAILED.txt
records, final Cloudflare502 page, and local-deep JSON. These outputs are not
part of the source commit. The old Qwen image5e7f82c7 and a600-mode environment
backup under the private runtime backups directory remain for managed rollback.

## Remaining product quality limits

The earlier public native-thinking comparison completed in about45s with three
Support cards. Some mechanism claims still needed source review; another answer
omitted a requested verification sentence. Those are separate quality/latency
limits, not evidence that native thinking failed to activate. This integration
does not claim a universal quality pass or an A/B victory over GLM.

The locked stack still emits a PyTorch future-compatibility warning about
`vllm::all_reduce` output/input aliasing during graph tracing, and FlashComm
deprecation notices. These are warnings, not masked worker exceptions; changing
collective semantics or the communication configuration is outside this
hybrid-cache consolidation. Optional vendor profiler dependencies in the base
image also report conflicts; the protected serving dependency closure is checked
separately by the image builder. No claim of a warning-free full vendor image
is made.
