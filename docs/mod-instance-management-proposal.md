# Mod 实例管理：分工与默认关闭契约（待双向确认）

日期：2026-09-03。状态：**Sage Mate 默认关闭的薄绑定已实现；通用 producer
尚未接入，未启用；Workstation 尚未确认契约**。

用户先要求协调，随后授权先实施 Sage Mate 一侧并提供转交提示词；仍不授权
重启、接管或切换共享服务。
已读取 Workstation 任务及其现有 `docs/mod-runtime-integration.md`、
`scripts/mod_deployment.py`、`src/lib/modRuntimeTypes.ts`。跨任务发送工具
返回旧接口停用，替代 `codex_app` MCP server 不可用，因此消息没有送达；
不能把文档方向一致当作对方接受本提案。

## 1. 建议分工和唯一代码归属

| 所属仓库 | 实现内容 | 建议负责的任务（待对方确认） |
| --- | --- | --- |
| dev-hub | 通用实例注册、不可变部署规格、计划/审批验证、原子接管、操作日志、应用/停用/回滚协调器及通用 owner adapter 协议 | Workstation 任务主导从既有协调器提取；Sage Mate 任务审阅接管边界 |
| Workstation | Mod 目录与候选制品准备、兼容性与工作进程证据、计划展示/审批交互、dev-hub 客户端 | Workstation 任务 |
| Sage Mate | 注册当前受管实例、现有启动器/运维入口与 dev-hub 的薄绑定、展示运行结果 | Sage Mate 任务 |
| runtime-manager | 如确需修改底层 systemd/Docker 资源操作，则实现与产品无关的后端操作 | 先单独约定文件与单一写入方 |

Workstation 不直接改 Sage Mate `.env`、systemd unit 或容器；Sage Mate 不复制
一份 Mod 审批和部署状态机。Sage Mate 的聊天 API 不成为实例管理 API。
dev-hub 的 Python/CLI 契约优先，是否加受认证的本地服务由双方另定；不新增公网
shell/容器管理接口。通过各父仓固定的 dev-hub gitlink 接入，不依赖另一产品的
绝对 checkout 路径。当前先提交默认关闭的 consumer；producer 按单一写入方实现，
待双方确认并跑通契约测试后再集成，避免并行改同一文件。

## 2. 当前已有能力及不能沿用的假设

- Workstation 协调器已有实例目录锁、持久日志、一次性过期审批、身份复核和
  回滚流程；生产 owner adapter 和与启动器共享的 generation fencing 仍缺失。
- 现有实例锁只约束调用该协调器的进程。Sage Mate 的部署锁位于另一处；两套
  独立的 flock **不构成原子接管**。
- `imageId + configurationHash` 不能代替可恢复的完整配置：必须持有对应的
  不可变部署规格和完整回滚引用，而不只是一个无法还原的 hash。
- `ModRuntimePayload.applicationAvailable` 目前为固定 false。文档所述制品
  prepared、包成功导入、UI 保存 enable 均不代表 Mod 已应用或兼容。
- Workstation 历史候选基于旧 Qwen 镜像 `5e7f82c7…`。当前已是 `de1742dd…`；
  旧候选需标为历史/过期并重新核验，不能用旧审批应用到新实例。

## 3. 建议 v1 契约

以下是通用控制面的建议概念，不是已存在的端点或最终字段名。
Sage Mate 已实现的 owner-entry 传输协议见第 7 节，不包含这一控制面。

- `InstanceRegistration`：操作者登记的 instance/host/owner/adapter ID，允许的
  动作和资源边界；发现容器或拥有 OpenAI 推理 API key 不等于管理授权。
- `DeploymentSpec`：固定 core/plugin SHA、平台/镜像 digest、模型 revision、
  Mod/manager/witness 制品 hash、物理设备集合/TP/graph、服务端口、解释器、
  mounts、插件 allowlist、明确的启动配置以及带版本的 secret 引用。
  模型路径和设备由宿主注册配置解析，不能在通用代码里写死；浏览器只能提交
  已登记 ID。不得允许任意 shell、flags、路径或 image URL。
- `ObservedIdentity`：实例、宿主 boot identity、supervisor generation、容器 ID、
  启动时间以及工作进程身份。配置 hash 与实际进程 generation 分开。
- `OperationPlan`：动作、实例、expected identity/generation、基线/候选 spec hash、
  停机影响、验证预算、回滚目标和有效期。环境覆盖在批准前全部解析并纳入 hash。
- `Approval`：可信管理员身份、具体动作/计划 hash/实例/generation、有效期、
  一次性 nonce；执行端独立鉴权并消费，不能相信 Web 传来的 `approved=true`。
- `OperationRecord`：幂等键、owner fencing epoch、审批消费、恢复快照、阶段和
  验证证据；落盘可恢复，不依赖 Web 进程、浏览器或临时轮询状态。

调用面建议为 `inspect/list`、`plan`、`approve`、`apply`、`disable`、`rollback`、
`operation_status`、`recover`。候选制品准备与上述服务生命周期操作分开。
先复用/迁移现有协议和日志，版本不兼容时 fail closed；不静默改写旧任务或审批。

## 4. 原子接管、应用及回滚

1. 只读生成计划和影响说明；未知身份、资源冲突或不支持的配置不能批准执行。
2. 获批后，权威 owner 在同一事务临界区复核 generation/spec，消费一次性审批，
   写入操作与恢复快照，授予绑定 operation 的 fencing token；不能先检查再裸 stop。
3. 运维脚本、受管服务启动/恢复路径和 Mod executor 必须共同遵守同一 owner
   协议。另一个协调器的文件锁、关闭 watchdog 或直接 docker stop 都不能代替它。
4. 使用冻结配置执行 drain → apply → verify → commit。正常停止和启动必须使用
   已注册生命周期后端；失败不能靠 eager、减少 TP 或切换设备隐式兜底。
5. 验证需含目标进程/完整 worker 集的 Mod 实际加载与执行证据、目标模型真实
   推理及新的身份。health、包安装或历史 receipt 单独都不足以显示 effective。
6. 应用/验证失败时，在本次批准明确允许的恢复范围且仍拥有同一操作所有权下，
   自动恢复冻结的前一版本并验证。失去所有权不触碰新占用者；回滚失败明确进入
   `rollback_failed` 并阻止后续应用，不报成功、不盲目重试。
7. 崩溃后恢复从持久日志和实际身份重新判断；不能因 lease 过期或 PID 消失就
   抢占。先证明旧执行器已被 fencing；不能证明时停在 recovery-required。

`disable Mod` 是部署一个明确的无该 Mod 版本，必须审批、切换、验证和可回滚；
它不是取消 UI 勾选。手动 rollback 也需绑定当前身份的新审批。制品仍被活跃、
进行中或保留回滚版本引用时不得删除。

## 5. 默认关闭与权限边界

新 Mod 生命周期能力需要三层同时满足：

1. 宿主侧明确开启通用管理能力（缺失/无效配置一律 false）；
2. 该实例显式允许相应管理动作（默认只读）；
3. 本次 action 的有效审批和资格检查通过。

Web 开关和管理员登录都不能单独开启宿主能力。关闭时不启动管理 worker、
不获取变更租约、不改 .env/服务/路由、不安装或加载 Mod，原有推理路径保持原样。
已存在的制品准备能力保持其当前权限，不由此新增授权或自动构建。

管理功能关闭与停用正在运行的 Mod 是不同操作。关闭管理入口应拒绝新应用，
不能突然停掉共享推理；已批准操作在安全检查点终止或进入批准范围内的回滚。
恢复通道必须独立、受 owner 身份与原审批恢复范围约束，不能成为关闭开关的后门。

## 6. 合并前门槛及本轮边界

先做无设备、无共享服务的单元/故障注入验证：默认关闭零副作用；并发审批只
有一个接管成功；过期/重放/篡改审批失败；外部重启和进程 generation 变化；
旧执行器复活；每个持久化阶段崩溃；disable、回滚失败、配置漂移和秘密脱敏。
模拟 adapter 的通过只证明控制逻辑，不能标为 NPU 实机通过。

代码集成与启用分离：dev-hub 契约/测试 → Workstation 客户端 → Sage Mate 薄绑定，
保持默认关闭。目标模型/TP/图模式及 Mod 兼容性验证、真实应用/停用/回滚，必须
另行取得具体实例和操作窗口批准。本轮不执行这些步骤。

只读基线（非可执行配置）：Sage Mate `c6c9cf7`，dev-hub `bc9ca7a`，
core `762f85b3`，Ascend `4e57439e`，Qwen3.8-27B，NPU0–3/TP4/graph，
镜像 `sha256:de1742dd6a1bc7ed1cbfff78d508ffa8ac769e58518d4e04d35a5d8203b88252`。
NPU4–7 和任何其他项目实例不在本改造授权范围内。

## 7. 已实现的 Sage Mate consumer 契约

实现位于 `tools/sage_mate_instance_control.py` 和 `tools/lib/instance_control.sh`。
无第三方 Python 依赖，不实现事务状态、审批、租约、Docker/systemd 调用。

默认 `SAGE_MATE_INSTANCE_CONTROL_ENABLED=0`、
`SAGE_MATE_INSTANCE_REGISTRATION=`。只读检查：

```bash
.venv/bin/python -I tools/sage_mate_instance_control.py --describe
```

关闭且未登记时返回 `enabled=false, enrolled=false, lifecycleAvailable=false`；
原入口不启动 Python/producer、不获取管理锁，保持已有行为。**不得因本提交就登记
线上实例或打开开关**。所有两个键均为不带引号的字面值，仓库 `.env` 优先于继承环境。

将来登记文件应由受信操作方创建：绝对真实路径、非 symlink、普通文件、0600、
属主当前 euid 或 root、最多 4096 字节，不含密钥或部署参数。严格 schema：

```json
{"schema":"sage-mate.instance-binding/v1","instance_id":"registered-instance","owner_id":"registered-owner","profile_id":"frozen-profile"}
```

三个 ID 必须匹配 `[a-z][a-z0-9-]{0,63}`；重复 JSON 字段或额外字段拒绝。
此文件是 owner 的定位信息，**不构成授权凭证**。删除登记文件或控制宿主/仓库的
管理员仍可绕过客户端；真正的权限边界、持久登记、fencing 必须在 dev-hub/宿主层。

已登记后即使开关变为 0，也不落回 legacy 路径。以下入口在已有变更前转交：

| 入口 | owner action |
| --- | --- |
| `run_vllm_engine.sh` / foreground model | `serve` |
| `manage.sh start/stop/restart --with-vllm-engine`（仅 engine） | `start/stop/restart` |
| `lock_sage_mate_engine.sh` / lock alias / 历史 retry 入口 | `reconcile` |
| `cleanup_vllm_engine.sh` / systemd ExecStopPost | `cleanup` |
| `monitor_twin_inference.sh` | `monitor` |

已登记时，设备 reserve、含其他服务的混合管理操作、非 `--check` 的 quickstart
安装全部拒绝，必须走另行批准的部署计划。`--help` 保留；不增加 apply/disable/
rollback/approve 命令，它们是 dev-hub 控制面而非 caller 可自授权的 action。

producer 必须在父仓 **HEAD gitlink 固定的** `deps/vllm-hust-dev-hub` 中提供：

1. `scripts/instance_owner_entry.py`；
2. `config/instance-owner-contract.json`，内容如下：

```json
{"protocol":"vllm-hust.instance-owner-entry/v1","entrypoint":"scripts/instance_owner_entry.py","actions":["serve","start","stop","restart","reconcile","cleanup","monitor"]}
```

消费者校验 gitlink/checkout HEAD 一致、无 tracked dirty、以上两个文件是固定提交
中可核验的普通文件；不接受 local-only shim、重定向文件或不匹配协议。
当前 pin `bc9ca7a` 不包含 producer；因此打开开关也不能获得生命周期能力。

随后通过相同解释器 `python -I` **exec** producer，从 stdin 传一个 JSON 对象后 EOF：

```json
{"schema":"vllm-hust.instance-owner-entry/v1","consumer":"sage-mate","action":"serve","instance_id":"registered-instance","owner_id":"registered-owner","profile_id":"frozen-profile","new_operations_enabled":false,"invocation_id":null}
```

保留 PID/退出码/信号生命周期；不进行隐藏重试。仅传递 PATH/HOME、locale、TMPDIR、
XDG_RUNTIME_DIR 和 DBUS_SESSION_BUS_ADDRESS；不传 VLLM_*、PYTHONPATH、dotenv、
密钥、shell、设备或镜像参数。INVOCATION_ID 如存在仅传 32 位小写十六进制 ID，
**只是关联信息而非 supervisor 身份证明**。producer 必须从可信注册表和 OS 身份
独立解析部署规格、审批和恢复范围，不得相信 consumer/owner_id/new_operations_enabled
或 inherited PATH/HOME 可证明权限。`serve` 需保持受管前台生命周期，不能脱离
systemd 新建无管理后台进程。不要回调同一个 shell 入口造成递归；start/stop 与
ExecStart/ExecStopPost 嵌套也需专门验证不会死锁。

`--describe` 即使验证 producer 存在，仍返回 `lifecycleAvailable=false`，因为纯
静态文件检查不能证明 owner 授权、实例资格或执行成功。

### 尚未完成的启用门槛

- producer 与 Workstation 双向契约确认；应用/停用/回滚和故障恢复测试。
- 所有宿主写入者的共同 fencing，特别是外部 `systemctl`/Docker、release 安装器
  在进入 quickstart 前的 `.env`/checkout 修改，以及其他 owner/开机启动路径。
  本提交的入口拦截不是 OS 沙箱；**在这些入口完成宿主侧权限约束前不得启用**。
- 真正的 TP4/graph Mod 资格、NPU worker 证据、审批窗口；禁止沿用旧镜像候选的
  effective 状态，禁止通过 TP1/eager 改小配置绕过兼容性。

### 本次离线验证

运行 `tests/test_instance_control.py` 与 systemd scripts、engine chat probe、
deployment receipts、runtime identity 共 **130 项通过**；Ruff 检查通过。
新增测试使用临时 Git fixture 和仅回显请求的假 producer，验证默认关闭、登记后
关闭、协议/版本漂移、非法登记、隐私过滤、退出码、stdin/exec 和实际脚本路由。
所有服务命令在 fixture 中禁用；这不是真实原子接管、Mod 执行或 NPU 验收。

## 待 Workstation 明确回复

- 接受或修改第 1 节单一写入方分工；由谁在 dev-hub 提取通用协调器？
- 确认 owner 协议如何覆盖原启动器/恢复入口，以及原子审批消费和 fencing。
- 确认冻结 DeploymentSpec、审批身份、disable 和恢复关闭语义。
- 确认第 7 节 consumer 协议或提出显式版本化调整；按各仓文件范围完成 producer。

本轮仅改 Sage Mate 薄绑定、入口、示例配置和测试/文档；未改 live `.env`、
submodule/gitlink、Workstation 或任何服务状态。转交提示见
[mod-instance-management-handoff.md](mod-instance-management-handoff.md)。
