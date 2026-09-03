# 转交给 Workstation 任务的提示词

> 历史交接（Sage Mate d310686）。Workstation 已以 dbb7cbe/b6e56e1 回应并接受
> 协议，请勿按此文重复提取事务库。当前接手结果、单一归属和后端缺口见
> [mod-producer-acceptance.md](mod-producer-acceptance.md)。以下保留原交接要求。

请推进与 Sage Mate 协调的 Mod 改造。用户授权先实现代码，所有生命周期能力默认
关闭；**本轮不得重启/切换 engine、proxy、app、tunnel，不得操作任何 NPU 或
statecentric 作业**。Sage Mate 已完成 consumer 薄绑定，通用 producer 尚缺；这
不是双方已经联调成功。请先读 Sage Mate 同一提交的
`docs/mod-instance-management-proposal.md` 第 7 节和
`tools/sage_mate_instance_control.py` / `tests/test_instance_control.py`。

## 分工

- 请由 Workstation 任务作为 dev-hub 通用实现的单一写入方，把
  `scripts/mod_deployment.py` 里的实例锁、持久操作日志、计划/审批及恢复协调器
  提取到 dev-hub；不要在两个产品各维护一套。
- Workstation 保留 Mod 目录、制品准备、兼容性/worker 证据、审批交互和薄客户端。
- Sage Mate 负责其 consumer 和启动/清理/恢复入口，暂时不要改它的共享工作区；
  如需协议调整请明确列出差异，不能各改各的。底层 systemd/Docker 通用 adapter
  如需改 runtime-manager，先明确文件归属。

## 已实现的对接点（需你确认）

固定 dev-hub gitlink 内的 `scripts/instance_owner_entry.py`，以及
`config/instance-owner-contract.json`，协议 `vllm-hust.instance-owner-entry/v1`。
consumer 使用当前 Python `-I` + exec，将 instance_id/owner_id/profile_id/action/
new_operations_enabled/invocation_id 的 JSON 写 stdin；具体 schema、manifest 和
环境白名单见 proposal 第 7 节。producer 必须支持
`serve/start/stop/restart/reconcile/cleanup/monitor`。不接收任意命令、路径、镜像
或 flags；从服务端固定注册配置解析。consumer ID 和 INVOCATION_ID 不是权限证明。

Sage Mate 的两键开关默认是 `SAGE_MATE_INSTANCE_CONTROL_ENABLED=0`、
`SAGE_MATE_INSTANCE_REGISTRATION=`。未登记保持原行为；登记后关闭开关仍走受管
接口，producer 缺失/版本不匹配/出错均 fail closed，不回到旧脚本。不要现在登记
真实实例或打开能力。只有你的 producer 提交推送并通过契约测试后，再协调父仓 pin。

## 必须补齐的安全语义

1. 原子接管：实例权限、完整 DeploymentSpec、expected generation/spec 的 CAS、
   一次性审批消费、事务日志和 fencing 必须在同一权威控制面协调。两个独立 flock
   不算接管。旧 owner、watchdog、外部重启和开机恢复也要遵守同一 fencing。
2. DeploymentSpec 必须完整可恢复，不止 imageId/configurationHash：固定
   core/plugin/Mod/manager/witness SHA/hash、镜像 digest、模型 revision、TP、
   graph、物理设备、ports/mounts、插件 allowlist、解释器与带版本 secret 引用。
   配置来源解析后冻结再审批，禁止执行时从旧 `.env` 漂移。
3. 授权必须同时满足宿主 gate、实例动作 allowlist、具体计划/action/generation
   的一次性有期限审批。Web 登录/勾选、推理 API key、发现容器都不等于变更权限。
4. apply → 验证 → commit；disable 是批准后部署无 Mod 版本；手动 rollback
   需要新审批。自动回滚仅限原审批恢复范围且仍持有 fencing，失去所有权不碰新
   占用者；失败写 rollback_failed/recovery-required，不能盲目重试。
5. 管理开关关闭只拒绝新变更，不突然停共享服务；恢复通道不能变成绕过审批入口。
   `serve` 保持前台 PID/信号监管，不通过同一入口递归；start/stop 与 systemd
   ExecStart/ExecStopPost 嵌套不能死锁。monitor 不得自行批准新应用。
6. 必须审计 release 安装器在 quickstart 前修改 `.env`/checkout、外部 Docker/
   systemctl 等残余写入者；consumer 不是 OS 安全边界，在宿主侧保护完成前不启用。

## 兼容性与验收

当前只读基线为 Qwen3.8-27B / TP4 / graph，镜像
`sha256:de1742dd6a1bc7ed1cbfff78d508ffa8ac769e58518d4e04d35a5d8203b88252`。
这是证据，不是可硬编码的部署配置；实施时只读重新核对。旧 `5e7f82c7…` 候选需
重新核验。制品 prepared、包导入、配置 enabled 不等于 Mod effective；必须独立
展示目标 worker 实际执行证据。DiffSpec 当前 TP1/eager 限制需真正兼容 TP4/graph，
不能降低线上配置来“通过”。本轮只做 CPU/隔离测试，不进行实卡切换。

补齐默认关闭零副作用、并发审批/CAS、过期重放篡改、外部 generation 漂移、旧
执行器复活、每个持久化阶段崩溃、disable、回滚失败、秘密脱敏等确定性测试。
Sage Mate 的假 producer 测试只证明传输，不是你的事务/NPU 验收。

请先回复分工和协议是否接受，然后在各自仓库按规范提交、测试并推送 main，给出
producer/main SHA、契约、测试和剩余缺口。保持所有实际能力关闭；启用、重启、
应用/停用/回滚需另行批准具体实例与操作窗口。不要清理其他任务的 dirty 文件。
