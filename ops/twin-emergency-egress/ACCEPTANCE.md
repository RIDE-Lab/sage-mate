# Acceptance — 2026-10-30

## Passed: transport and scoped recovery

- Before: public Twin returned Cloudflare 1033. A100 public DNS and direct TCP
  failed. A6000 shared similar egress failures. A100 remained accessible from the
  Mac via its existing SSH entry. No shared network configuration was changed.
- After: the independent Twin connector reports four ready connections. Public
  homepage returned HTTP 200 with 101612 bytes; public `/health` returned ready
  both from the Mac and from the independent `180-evaluation` host.
- Native Chrome rendered the public member interface and submitted the original
  two questions. This is actual browser acceptance, not a substituted HTTP check.
  IAB automation timed out twice and was not counted as passing; native Chrome
  was used successfully instead.
- A100's dedicated proxy listener is only `127.0.0.1:17898`. Effective app
  environment contains the intended proxies and internal bypasses. The model
  engine PID stayed `3654893`; the existing backend release stayed pinned.
- Direct Tavily retrieval through the bridge returned three KV-cache sources in
  one 4.83-second sample. This is connectivity evidence, not a speed benchmark.
- Production configuration initially ignored `TAVILY_TOKEN`; a search therefore
  returned unrelated Bing KV/electrical-voltage results. The credential alias
  already present on main was selectively backported. Three isolated precedence
  assertions passed. Using actual production settings/client then returned
  relevant KV-cache sources in a 1.18-second sample.
- A separate SSH LaunchAgent with separate loopback ports was terminated. launchd
  automatically replaced it and the health GET succeeded after 15.19 seconds.
  Production forwarding and GPU services were not interrupted by fault injection.
  This verifies process recovery, not resilience to a Mac power/network outage.
- Four isolated Cloudflare-operation tests passed: preparation does not switch
  DNS; repeated preparation preserves credentials; activation/rollback preserves
  the original DNS fields; independent DNS changes prevent rollback overwrite.
  Production DNS was not intentionally switched back to the known-broken path.

## Not passed: original question quality

1. **KV Cache 迁移和重计算，什么时候各自更划算？**
   Public Chrome run after the credential repair finished in 60.1 seconds and
   showed three web candidate sources with a retrieval timestamp. Two were about
   KV cache; one was unrelated ECC/cryptocurrency material. The final answer only
   reported insufficient evidence. Search transport works; relevance and useful
   answer coverage still fail. This is not accepted as a successful answer.
2. **怎样区分系统优化的真实收益和实验干扰？**
   Public Chrome run ended at `assess-coverage-correction` with an execution error.
   Its details said no public search topic had been confirmed and no web search
   ran. This remaining policy/coverage failure is not a network failure.

Neither evidence validation nor answer generation limits were relaxed to pass.
No unaccepted workflow candidate was deployed. Browser evidence is retained in
this task's `work/twin-alternate-egress-20261030/` directory (not public Git).

## Remaining operational limitation

This emergency route requires the Mac login session, SSH, its existing proxy,
and network to remain up. It must later be replaced by an always-on reachable
relay or retired once the school egress is repaired. The original DNS/config
backups and rollback script are retained. No recurring follow-up was enabled.
