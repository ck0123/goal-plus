# Goal Plus (GP)

[English](README.md) | 简体中文

Goal Plus 是面向长时间 agent 任务的宿主中立运行时。`/goal-plus` 可以直接处理
普通目标；遇到可度量的优化任务时，它会升级到 Search Mode：冻结评价合同、在隔离
候选中探索，并提升经过 verifier 验证的最佳结果。

Pi 是首选宿主路径，Codex 是首选原生多 agent 路径。Claude Code 和 OpenCode
继续作为兼容宿主受到支持。

## 快速开始

从 Git 或现有检出安装：

```bash
python -m pip install --user "git+https://github.com/ck0123/goal-plus.git"
# 或
python -m pip install -e ".[dev]"
```

所有宿主都启动同一个 stdio MCP 服务：

```text
goal-plus --root .gp
```

然后在宿主中启动目标：

```text
/goal-plus 修复这个 bug 并验证测试套件。
/goal-plus 在不改变正确性的前提下，用两小时优化 p95 延迟。
/goal-plus mode=probe 先确认向量化是否可行。
/goal-plus mode=autonomous 深度优化这个 kernel。
```

Codex 和 Pi 还提供：

```text
/goal-plus edit <完整的新目标>
/goal-plus resume
/goal-plus-with-final-check <目标>
```

一次请求会启动自主运行。agent 自己判断 Goal Mode 是否足够，或冻结 verifier 后
并行 Search 是否更有价值；进入 Search 不需要额外确认。`mode=autonomous`（默认）
允许给有价值的 candidate worker 较长且可续的探索 lease；`mode=probe` 先做短时可行性
探测。该探索模式只会作为规范化说明写入 `raw_goal` 最后一行，不是新的调度状态。

## 宿主

| 宿主 | 项目资产 | 入口 | Search worker 路径 |
|---|---|---|---|
| Pi | `.pi/` | `/goal-plus` 或 `pi -p "/goal-plus ..."` | 持久化 Pi RPC pool；参阅 [Pi](docs/pi.md) |
| Codex | `.codex/` | `goal-plus` skill 或 `/goal-plus` 提示 | 原生滚动 `spawn_agent` pool；Codex 0.144.1+ hook 覆盖 `UserPromptSubmit`、`PreToolUse` 和 `SubagentStop`；参阅 [Codex](docs/codex.md) |
| Claude Code | `.mcp.json`、`.claude/` | `goal-plus` skill | 前台 Agent 兼容路径；参阅 [Claude Code](docs/claude-code.md) |
| OpenCode | `opencode.json`、`.opencode/` | `/goal-plus` | 旧策略覆盖最完整；参阅 [OpenCode](docs/opencode.md) |

Codex 需要将 `.codex/config.example.toml` 复制为被忽略的本地文件
`.codex/config.toml`。宿主差异和策略覆盖见
[Agent Host Adapters](docs/agent-host-adapters.md)。

## 心智模型

- 一条 **Goal Plus 记录**对应完整的用户任务。
- 一个 **search task** 是在一份 frozen spec 上运行的一个 `run_id`；一个目标可关联
  多个 search task。
- 一个 **round** 是一次持久化规划决策，不是同步屏障。
- 一个 **candidate** 是带 verifier 历史的隔离工作区。
- 一个 **worker session** 是宿主上下文/来源句柄。worker 生命周期属于宿主，
  不属于 Search 运行时。

Search 使用滚动 pool：先填满 `budget.max_parallel`，任意 worker 完成后立即判断是
继续该方向、启动新候选、空置槽位，还是排空后选择结果。慢 worker 不会阻塞已经完成
的工作。完整流程见 [Flow](docs/flow-view.md)。

运行时状态保存在 `.gp/`。`search_tasks` 只追加；`linked_search` 只是当前任务的
兼容视图。

配置 `promotion_verifiers` 后，Promotion 会执行独立检查，而不是复用缓存结果。
Runtime 会检出选中的 verifier-backed revision，以
`GOAL_PLUS_VERIFIER_PHASE=promotion` 重新运行每个 Promotion Gate，将证据绑定到
Selected Git Head 和 Artifact Hash，之后才生成可被 Git 应用的 Patch。Promotion
失败时保持 `ready_to_promote` 以便重试，并且不会生成 Patch。

## 文档

| 需求 | 文档 |
|---|---|
| 端到端职责和滚动 pool 流程 | [Flow](docs/flow-view.md) |
| 架构、状态与不变量 | [Design](docs/design.md) |
| 当前 MCP 与 Pi 本地工具 | [API](docs/api.md) |
| 宿主能力对比 | [Agent Host Adapters](docs/agent-host-adapters.md) |
| 运行时与宿主日志 | [Debugging](docs/debugging-runtime.md) |
| spec 与可运行示例 | [Examples](examples/README.md) |
| 测试与真实宿主证据 | [Tests](tests/README.md) |

## 开发

```bash
python -m pytest -q
git diff --check
```

Pi、Codex 和 Claude Code 的可移植策略集合是 `agent_guided`
（`agent`/`default`）和 `random`（`random_mode`）。现有高接触策略和 trace 导出
仍以 OpenCode 为兼容宿主。
