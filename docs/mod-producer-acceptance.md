# Mod producer 接手验收与产品 backend 边界

日期：2026-09-04。阶段：**默认关闭的依赖/协议集成；不是生产启用**。

接手来源：Workstation `dbb7cbe` 的 `docs/mod-instance-producer-handoff.md`。
对方已明确接受 Sage Mate `d310686` 的分工与 owner-entry/v1，无 schema 变更。
本次先接受 dev-hub `b6e56e1f7f1ae58ea15aa9994852f48290827a55`，随后复验并
更新至 `bfcc0d5c2c083d6b633d2f0657a09c7f7712c74c`，均来自 canonical
origin/main。第二次更新只新增 Backend protocol、foreground helper、设计文档
和测试，没有修改
推理启动脚本、production lock、模型、镜像或递归 gitlink。

父仓 gitlink 固定整个 dev-hub commit，因而新增四文件也在不可变 source tree
范围内；运行时还拒绝任何 tracked dirty。验收逐文件对比 working-tree blob 与
`<gitlink>:<path>`，并逐个篡改验证 owner entry 在执行 producer 前 fail closed。
无需再复制一份可能漂移的文件哈希清单来削弱 gitlink 的完整树承诺。

## 单一写入归属（协调任务已确认）

| 范围 | 写入方 | 文件/边界 |
| --- | --- | --- |
| 通用 producer、authority、审批/CAS/fencing、backend 协议及监管 | Workstation 任务 | dev-hub `scripts/instance_control/`、两个 entry 脚本、contract JSON、相应 tests/docs |
| Workstation 客户端和 UI、Manager/Provider 对接 | Workstation 任务 | Workstation 仓库；不能代替 Manager 注册插件 |
| Sage Mate 产品 backend adapter、owner-entry 薄绑定、入口审计、gitlink | Sage Mate 任务 | `tools/sage_mate_instance_control.py`、`tools/lib/instance_control.sh`、相关运维入口和 tests；未来产品 adapter 独立文件，通用状态机不复制 |
| runtime-manager 底层变更 | 尚未分配 | 本轮不修改；先由协调任务确认具体文件的唯一写入方 |

通用接口有问题时记录 exact API/测试交回 Workstation，不并行修改其 dev-hub 文件。

## 已复验的安全行为

- 从 Sage 自己的 dev-hub Git 对象克隆精确 SHA 到临时父/子仓；不引用 Workstation
  的绝对运行路径、不复制一个假 producer 充数、不修改线上登记文件。
- `serve/start/stop/restart/reconcile/cleanup/monitor`，以及八条实际脚本入口，在
  consumer preference 为 0/1 时都得到退出码 2、
  `error=production_backend_not_qualified`、`lifecycleAvailable=false`。
- HOME/TMP/runtime/DBus 隔离；systemctl/docker/sudo/npu-smi/curl/ps 替换为拒绝
  fixture。未调用服务命令、未创建 authority.sqlite3、未改测试 `.env`、未泄漏密钥。
- `--describe` 可以证明固定 producer 存在，但仍不声称 lifecycleAvailable。
- `deployment_spec/generation/fence/approved/peer_uid/executor_id/quiescence_receipt`
  不能通过 registration 额外字段
  注入，直接被 consumer 拒绝。合法 owner 请求只传登记 ID 和动作：完整
  DeploymentSpec、generation、审批及 fence 必须由权威端解析，**没有把它们
  在两个产品间自由转发**。
- producer 事务测试独立覆盖冻结快照、CAS/并发审批、重放、身份漂移、逐阶段
  崩溃和恢复 fence。使用真实多进程 + 无设备后端，并非真实 Docker/NPU 原子性证据。

复现：初始化父仓固定的 dev-hub，然后执行：

本次最终结果：Sage 接入/相关部署回归 **173 passed**（其中 owner **92 passed**），
dev-hub foreground/事务/receipt/profile 回归 **52 passed, 37 subtests passed**；
两侧相关 Ruff 检查通过。dev-hub foreground 测试要求 Linux `pidfd_open`：项目
Python 3.11 构建缺少该 OS binding 时有 6 项确定性的解释器能力失败，改用具备
`pidfd_open` 的系统 Python 3.10 后全量通过；没有跳过或修改断言。测试没有使用
共享服务或 NPU。

```bash
.venv/bin/pytest -q tests/test_instance_control.py tests/test_systemd_service_scripts.py tests/test_engine_chat_probe.py tests/test_deployment_receipts.py tests/test_runtime_identity.py
/usr/bin/python3 -m pytest -q deps/vllm-hust-dev-hub/tests/test_instance_foreground.py deps/vllm-hust-dev-hub/tests/test_instance_transactions.py deps/vllm-hust-dev-hub/tests/test_deployment_receipt.py deps/vllm-hust-dev-hub/tests/test_optimization_profile.py
.venv/bin/ruff check tools/sage_mate_instance_control.py tests/test_instance_control.py
.venv/bin/python -I tools/sage_mate_instance_control.py --describe
```

## 线上只读回归（未启用 Mod、未重启服务）

- 普通问答：Qwen3.8-27B，HTTP 200，8.84 秒，5 条知识命中、3 条 Support，
  1 次模型调用、0 重试；其中模型生成约 1.74 秒。
- 深度问答：Qwen3.8-27B，HTTP 200，51.18 秒，2221 completion tokens，回答完整，
  1 次模型调用、0 重试。问题是通用实验设计且本地知识命中为 0，因此没有伪造
  Support。
- SSE/取消：保持 workflow-events 在线时收到 trace-step、keepalive、error；显式
  `/chat/cancel` 返回 `cancelled=true`，请求在 4.01 秒内结束。随后恢复问答 HTTP
  200（8.49 秒），仍使用 Qwen3.8-27B，并返回 5 条知识命中、3 条 Support。
- app/site/tunnel/engine/OpenAI proxy 五个用户服务均为 active。新日志只有上述正常
  200 和由主动取消产生的 504；没有 traceback 或持续请求重试。本轮没有重启、
  切换模型、修改 NPU、登记实例或打开 lifecycle gate。

候选接手前，可仅在测试进程中设置 `SAGE_MATE_TEST_PRODUCER_REVISION=<已 fetch SHA>`；
正常 CI 使用父仓 HEAD 中的 gitlink，不读取远端最新版本。所有真实 producer 测试
是这个阶段的 closed-gate 契约门槛；将来开放后端须显式新增授权 fixture 并更新门槛，
不能直接删除拒绝断言。

## 给 Workstation 的最小 backend 接口反馈

以下沿用 `b6e56e1` 的真实 Controller 调用签名，不是新造的第二套管理 API：

| 方法 | 参数/返回 | Sage 产品 adapter 必须做到 |
| --- | --- | --- |
| `qualify(registration, spec)` | `DeploymentSpec` → 严格 bool | 独立核验固定制品/镜像/模型、Manager/Provider 渲染、完整配置与 OS 写入者排除；任何缺项 false |
| `inspect(registration)` | observation dict | 捕获实际 boot/supervisor/container/process 身份及当前 spec；不得把配置声明当实测 |
| `owns(registration, token, expected_identity, *, restore)` | 严格 bool | 检查 operation/executor/fence 和实际资源；restore 也只能拥有本操作资源 |
| `deploy(registration, spec, token, deadline, *, restore)` | 有界同步效果 | 只消费权威快照，直接非递归原语，不重新读取 `.env`，不调用旧 launcher/reconcile 自己 |
| `verify(registration, spec_hash, token, deadline)` | observation dict | 验证完整 worker 集、Manager/Mod 实际执行、非 fallback 推理、健康和新身份 |
| `quiescent(registration, operation)` | 严格 bool | 证明旧执行器及已排队 daemon 效果均不能再落地；PID 消失/超时本身不够 |

registration 当前字段：instance_id、owner_id、profile_id、backend_id、actions、
owner_uids、fencing_receipt_sha256；由可信宿主管理员登记，而非 Web JSON 自授权。
effect token 是 authority 产生的 id/fence/executor；generation 位于实例和计划，
不能由 owner-entry caller 提供。backend 不得仅比较 receipt 字符串就返回 qualified。

observation 当前字段：instance_id、spec_hash、identity、captured_at、healthy、
components_executed、inference_verified。identity 包含 boot_id、
supervisor_generation、resource_id、started_at、processes；每个进程含 pid、
start_ticks、role、rank。adapter 必须核对实际 TP/PP 完整 rank 集；当前 schema
接受非空 process 列表**不等于已证明完整 TP4 worker**。

### 固定部署快照字段

按 producer `DeploymentSpec` 提供且由宿主独立解析/核验：

- schema；image 的 id/digest/platform；core/ascend/manager 的 source_sha/wheel_sha256；
  witness 同格式或 null；mods 的 id/artifact/original manifest。
- model 的 id/revision/path/files_sha256；resources 的 devices/tp/pp/graph/ports/mounts。
  graph 包含 mode/configuration；ports 包含 address/host/container/protocol；
  mounts 包含 source/target/read_only/content_sha256。
- launch 的 interpreter/argv/environment/working_directory/plugin_allowlist/
  resolved_options：解析后明确 model/TP/PP/enforce_eager/compilation_config 及其他默认值。
- provider 的 id/source_sha/configuration/rendered/rendered_sha256/qualification；
  qualification 为 receipt_sha256/status，但状态不能替代真实资格核验。
- secrets 只存 id/version/target，不含值、不用 latest/main/current。执行时由受信
  secret resolver 解析固定版本；日志、浏览器和进程参数不得暴露明文。

当前 invariant 锁住 model 和 resources；因此不改变现网 Qwen3.8-27B、TP4、graph。
不从产品代码硬编码机器/设备/路径。插件发现/manifest/兼容性/启动渲染继续归
Extension Manager/Host Provider；adapter 消费其结果，不调用 plugin.register。

## 生命周期旁路与宿主权限要求

| 入口 | 当前状态 | 启用前还需解决 |
| --- | --- | --- |
| run/foreground、cleanup/ExecStopPost | 登记后已路由 closed-gate | 前台信号、PID/cgroup、超时取消与精确清理契约；不可沿用按名字/端口扫描杀进程 |
| manage engine start/stop/restart | 登记后已路由；混合服务操作拒绝 | 不持 authority 锁调用等待 ExecStart 再入的阻塞 systemctl |
| lock alias、历史 retry、monitor | 登记后已路由 | monitor 只能报告/请求批准范围内恢复，不能自行创建新审批 |
| reserve、quickstart 安装 | 登记后拒绝；只读/help 保留 | profile 迁移需独立批准，不恢复旧 dotenv 参数 |
| release/hosted-web.sh、hosted-web-installer.sh | quickstart 之前仍有 checkout/env 修改 | 发布与更新入口也必须受 owner 排他保护，不能靠后置 guard 补救 |
| 外部 Docker/systemctl、unit/env/source 写权限、开机自动恢复 | 未建立 OS 排他 | 限制 socket、sudo/polkit、unit/drop-in、registry/spec/secret/config 文件权限；未知写入者拒绝资格 |

所有权限部署都是下一阶段，需要批准后执行，本次不改属主/权限/组/服务 unit。
不能把同一用户运行的产品脚本当成防御该用户任意 Docker/root 操作的安全沙箱。

## 尚缺的通用接口/真实后端门槛

### Workstation 要求的三项明确答复

1. **身份能提供什么**：产品 adapter 将从宿主 OS/supervisor 只读捕获 boot_id、
   unit InvocationID、cgroup、容器 ID/启动时间、进程 PID/start_ticks/role/rank，
   作为观测关联。当前 owner-entry 输入中的 invocation_id、owner_id 及这些观测
   **不能证明调用者获授权或外部写入者被排除**。尚无经过认证的 broker peer、
   authority executor ticket，因此可信执行器身份能力当前为 unsupported。
2. **哪些操作能同步完成**：当前可执行的是纯只读描述、输入/固定源码核验和
   有界的隔离 CLI 拒绝。生产 serve/start/stop/restart/reconcile/cleanup/monitor
   均 fail closed；没有已经合格的同步部署方法。Docker CLI 返回不代表 daemon
   效果已终止；systemctl 等待 ExecStart 再入也不能作为此同步方法的实现。
3. **旧执行器不能再写入的证据**：当前没有。PID 不存在、InvocationID 变化、
   flock 释放、CLI 已退出、NPU 看似空闲都不够。需要独立受信 broker 吊销旧
   operation/executor/fence，确认 cgroup 及子进程退出、daemon 已排队命令完成或
   撤销，并证明所有变更路径只接受新 fence。现有宿主共用 UID/管理权限尚未排除
   外部写入者，不能出具 quiescence/qualified=true。

对应 fixture 验证仅是负向安全边界：额外 peer_uid/executor_id/quiescence_receipt
被 consumer 严格 schema 拒绝；合法形状的所有生命周期动作仍被真实 producer 拒绝。
没有用 fixture 构造“宿主隔离已通过”的回执。正向 trusted identity / synchronous
effects / quiescence fixture 待 Workstation 确认通用协议后由产品 adapter 对接；
权限或证明缺失必须返回 unsupported/fail-closed，通用错误码由 Workstation 定义。

Workstation 负责定义本地认证 transport（实际 OS peer 身份）、持久 owner broker
和前台监管协议；Sage 实现产品映射，不自行建立另一个守护进程。需明确：

1. `serve` 的前台 attach/退出码/信号/cancel/cleanup 请求如何携带**权威端授予**的
   operation/executor/fence，而非 caller 自行拼接；启动与停止是否有独立票据。
2. 非递归、有界 deploy 原语如何等待 readiness，又不与 ExecStart/ExecStopPost
   反向争同一锁；SIGTERM、崩溃、进程组遗留与 daemon 异步排队效果如何回收。
3. 新操作 gate 关闭后，已批准恢复的身份、时限、scope 与重新审批接口。
4. 完整 worker rank/Mod 执行/Provider 渲染证据类型，以及真实既有实例 capture 的
   无漂移策略；不能用三条 HTTP 200 代替后端资格。

这些尚未定义/安装，所以本次不提供假装可用的生产 adapter、不登记实例、不开放
管理接口；gitlink 接入仅代表接受默认关闭的协议阶段。后续代码/无设备 fixture 可
按上述单一归属继续，真实切换需另行批准实例和操作窗口。
