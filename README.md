# Sage Mate

Sage Mate 是面向教师数字分身、本地代码助手和自动科研流程的多 profile 应用。Faculty Twin 是数字分身 profile，Code Assistant 是本地代码工作台 profile，Auto Scientist 会结合分身记忆和 CC-hust 代码节点启动科研流程。

Sage Mate 是基于共同旗舰产品 SAGE（Streaming-Augmented Generative Execution）构建的代表性应用，并通过 vLLM-HUST 或用户显式配置的兼容服务执行模型推理。SAGE 核心仓库由 RIDE Lab 负责主要维护，但产品愿景属于整个 IntelliStream 研究生态。

## 支持平台

| 平台 | 形态 | 推理后端 | 安装入口 |
| --- | --- | --- | --- |
| Linux CPU | hosted web | 用户配置的 OpenAI-compatible endpoint，或无本地推理的 CPU 检查路径 | `./quickstart.sh --target hosted-web` |
| Linux Ascend | hosted web | 本机 `vllm-hust` + `vllm-ascend-hust` | `./quickstart.sh --target hosted-web --with-vllm-engine --with-vllm-proxy` |
| macOS Apple Silicon | Sage Mate.app | 本机 `vllm-metal-hust`，或用户手动配置的 endpoint | DMG |

默认不连接远端 vLLM 服务。只有用户显式填写 URL/API key，或通过安装参数预填，才会使用外部 endpoint。

## 一键安装

macOS 本地 Sage Mate:

```bash
./quickstart.sh --start
```

Linux hosted web:

```bash
./quickstart.sh --target hosted-web --start
```

Ascend 本机推理:

```bash
git submodule update --init --recursive
./quickstart.sh --target hosted-web --with-vllm-engine --with-vllm-proxy --start
```

macOS 用户安装:

```text
打开 dist/sage-mate-macos.dmg，双击 Sage Mate.app
```

维护者构建 DMG:

```bash
./quickstart.sh --target mac-dmg
```

## 关键规则

- `hosted-web` 只提供网页 Faculty Twin profile，不启用本地代码能力。
- Sage Mate DMG 随包携带 `claude-code-hust`、Bun runtime、`vllm-metal-hust`，不要求用户临时 clone。
- Auto Scientist 只在本地模式使用 allowlisted workspace；默认生成科研计划和 propose-only 代码建议，不直接修改真实仓库。
- macOS 本地模型使用我们的 `vllm-metal-hust` fork，并基于仓库固定的 `deps/vllm-hust` core。
- Linux Ascend 使用仓库固定的 `deps/vllm-hust-dev-hub`、`deps/vllm-hust`、`deps/vllm-ascend-hust`、`deps/ascend-runtime-manager`。
- Linux Ascend 的正式升级必须把兼容基座、core/plugin 精确源码版本和不可变镜像身份分层记录；详见 [部署指南](docs/deployment.md#ascend-生产运行时身份)。
- token、私有 runtime 数据、Cloudflare 配置不要提交到仓库；运行时数据默认放在 `DIGITAL_TWIN_RUNTIME_DIR`。

## 常用配置

```bash
DIGITAL_TWIN_LLM_BASE_URL=http://127.0.0.1:8000/v1
DIGITAL_TWIN_API_KEY=EMPTY
DIGITAL_TWIN_MODEL_NAME=qwen3-32b
DIGITAL_TWIN_STREAM_CHAT_ANSWER=false
```

macOS Apple GPU:

```bash
DIGITAL_TWIN_LOCAL_MODEL_BACKEND=vllm_metal
VLLM_METAL_MODEL=mlx-community/Qwen2.5-7B-Instruct-4bit
VLLM_METAL_PORT=8000
```

## 服务管理

```bash
./manage.sh status --all
./manage.sh start --all
./manage.sh stop --all
./manage.sh logs app
./manage.sh logs engine
```

## 验证

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:55601/
curl -s http://127.0.0.1:55601/healthz
```

## CI 覆盖

- `ubuntu-latest`: lint、frontend 静态契约、Firefox 多视口布局回归、pytest、Linux CPU 一键安装检查。
- `ubuntu-latest`: Ascend 启动脚本语法和部署契约检查。
- 真实 NPU 主机回归：仅从 `main` 人工触发 `.github/workflows/ascend-npu.yml`，由
  workflow-restricted 的一次性 runner 执行；公共 PR 永远不会直接使用 NPU 宿主机。

## 入口

- 安装：`quickstart.sh`
- 服务管理：`manage.sh`
- macOS 打包：`tools/build_macos_local_code_package.sh`
- 本地代码模式：`docs/local-code-mode.md`
- hosted release：`docs/hosted-web-release.md`
