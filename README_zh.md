# Goal Plus(GP)

[English](README.md) | 简体中文

`goal-plus` 是 `/goal-plus` 背后的宿主中立运行时。MCP 是它的共享控制平面；
Goal Plus 则是产品本身和面向用户的工作流。

`/goal-plus` 是默认的用户入口。对于常规的编码、文档、审查和调查任务，
它的使用方式与普通 goal 相同。当任务属于可度量的优化问题时，它可以升级到
Search Mode：冻结验证器和指标、创建隔离的候选工作区、启动宿主原生 worker、
通过运行时管理的检查为候选方案评分、选择最佳结果、生成报告，并导出用于
提升正式代码的补丁。

当前仓库内置了面向以下宿主的资产：

- Pi
- Codex
- Claude Code
- OpenCode

新部署优先推荐 Pi，其次是 Codex。它们是当前开发和端到端验证的主要宿主路径；
Claude Code 和 OpenCode 继续作为兼容路径受到支持。

MCP 运行时保持宿主中立。各宿主的特定行为位于仓库内对应的配置、技能、
钩子和 worker agent 提示词中。

## 安装

安装 Python 包，使 `goal-plus` 命令可以从 `PATH` 中找到。

从 Git 安装：

```bash
python -m pip install --user "git+https://github.com/ck0123/goal-plus.git"
goal-plus --help
```

从已有的本地检出安装：

```bash
cd goal-plus
python -m pip install -e ".[dev]"
goal-plus --help
```

如果按用户级别安装后仍找不到命令，请将 Python 的用户脚本目录加入 `PATH`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

macOS 框架版 Python 可能需要使用：

```bash
export PATH="$HOME/Library/Python/3.10/bin:$PATH"
```

本包尚未发布到 PyPI，请通过 Git 或可编辑安装方式使用。

## 配置宿主

运行时是一个 stdio MCP 服务器。所有宿主都应启动同一条命令：

```text
goal-plus --root .gp
```

本仓库已经为所有受支持的宿主提供了项目级配置。

| 宿主 | 项目配置 | 用户入口 | 说明 |
|---|---|---|---|
| Pi | `.pi/prompts/`、`.pi/skills/goal-plus/`、`.pi/extensions/goal-plus.ts` | 在交互式 Pi 中使用 `/goal-plus`，或运行 `pi -p "/goal-plus ..."` | 扩展会在模型运行前预先创建 Goal Plus：在 TUI/RPC 中使用原生命令，在 print/JSON 模式中使用输入转换。Pi RPC worker 通过 `goal-plus-pi-worker` 以无状态方式运行；统计数据属于 Pi 自定义条目，而不是 LLM 消息。 |
| Codex | `.codex/config.example.toml`、`.codex/hooks.json`、`.codex/skills/` | 将示例复制到已忽略的本地文件 `.codex/config.toml`，然后使用 `goal-plus` 技能或 `/goal-plus` 提示 | Codex 0.144.1+ 提供 `UserPromptSubmit`、`SessionStart`、`PreToolUse`、`PostToolUse`、`Stop` 和 `SubagentStop` Goal Plus 钩子。Codex 提示时，请审查并信任项目钩子。 |
| Claude Code | `.mcp.json`、`.claude/settings.json`、`.claude/skills/`、`.claude/agents/` | 在 Claude Code 中使用 `goal-plus` 技能或 `/goal-plus` 提示 | 提供 `PostToolUse(goal_plus_create)` 会话绑定和基于会话范围的 `Stop` 钩子。 |
| OpenCode | `opencode.json`、`.opencode/command/goal-plus.md`、`.opencode/skills/`、`.opencode/agents/` | 在 TUI 中使用 `/goal-plus`，或运行 `opencode run --command goal-plus "<prompt>"` | OpenCode 是旧版 Search Mode 策略的兼容基线；由于仓库未提供 OpenCode 钩子，Goal Plus 阶段门禁由指令驱动。 |

各宿主的设置和调试细节请参阅：

- [Pi 参考文档](docs/pi.md)
- [Codex 参考文档](docs/codex.md)
- [Claude Code 参考文档](docs/claude-code.md)
- [OpenCode 参考文档](docs/opencode.md)
- [宿主适配器能力矩阵](docs/agent-host-adapters.md)
- [运行时与宿主日志调试](docs/debugging-runtime.md)

## 运行 `/goal-plus`

普通目标和优化型目标都使用 `/goal-plus`。工作流从 `goal_plus_create` 开始，
记录任务分流结果；只有在目标具备由验证器支撑的规格后，才会进入 Search Mode。

Codex 和 Pi 还支持以下生命周期命令：

```text
/goal-plus edit <full revised goal>
/goal-plus resume
/goal-plus-with-final-check <goal>
```

编辑操作会保留相同的 `goal_plus_id`，追加新的 `goal_revision`，重置修订后目标的
分流状态，并将之前的 Search 任务保留为历史证据。带检查器的运行要求当前修订
必须经过一个独立的宿主原生审查 agent；在该审查通过前，运行时会拒绝普通完成。
被中断的轮次会保留活动记录。检查器中断会被记录为一次独立尝试，父 agent 随后会
准备一次全新的审查尝试。

`/goal-plus` 与普通 `/goal` 具有相同的交互约定：用户提交一次请求即可启动自主运行。
agent 会根据仓库证据判断 Goal Mode 是否足够、是否必须找出由验证器支撑的规格，
以及并行 Search 是否有价值。用户提示可以改善这一决策，但进入 Search 从不要求
额外确认。宿主钩子和运行时门禁用于保证各阶段完整，而不会创建审批检查点。

一条 Goal Plus 记录对应一项完整的用户任务。如果最终审计发现还需要另一项由验证器
支撑的搜索，它可以包含多条 `search task` 记录。每项搜索任务是在一份冻结规格上
运行的一个 `run_id`；每项任务又可以包含多轮搜索。运行时会分别报告已规划轮数和
已启动轮数。

示例：

```text
使用 /goal-plus。修复这个 bug 并验证测试套件。
```

```text
使用 /goal-plus。优化此模型服务路径以降低 p95 延迟。首先确定基准测试、
正确性门禁、可编辑文件和提升规则。如果验证器已冻结且可用于搜索，
则使用 Pi RPC worker 运行 Search Mode。
```

OpenCode 还保留 `/goal-any-optimize` 作为旧版别名，但 `/goal-plus` 是规范入口。

## Search Mode 流程

当 `/goal-plus` 将任务升级到 Search Mode 后，主 agent 会驱动以下通用 MCP 流程：

1. `search_freeze_spec`
2. `search_create`
3. `search_plan_next`
4. `search_start_batch`
5. `search_start_agent_session`
6. 在 Pi RPC、Codex、Claude Code 或 OpenCode 中启动返回的前台 worker
7. 使用 `search_bind_agent_handle` 或 `search_bind_opencode_session` 绑定宿主句柄
8. worker 调用 `search_get_agent_context`
9. worker 使用 `search_run_verifier(..., agent_session_id=...)` 自行评分
10. 主 agent 确认最终分数、选择结果、生成报告，并按需执行提升

如果原始目标审计要求进行另一项冻结搜索，请从 `search_freeze_spec`/`search_create`
开始重复上述流程，并将新的 `run_id` 关联到同一条 Goal Plus 记录。
`linked_search` 仍然是当前任务的兼容视图；`search_tasks` 则是完整的、仅追加的任务历史。

运行时负责 `.gp/` 状态、候选工作区、验证器评分、历史记录、报告和提升产物。
宿主负责 worker 启动与中断、step/turn/time 限制、前台返回值和原生执行记录。
MCP 不提供 wait、abort、submit、observe 或 host-sync 工具。

## 任务延续与恢复

这里有两种不同的延续概念：

| 概念 | 是否可移植 | 作用 |
|---|---|---|
| 使用 `search_redispatch_candidate` 进行状态级恢复 | 是，所有宿主 | 为同一个候选工作区创建新的 `agent_session_id`。新 worker 会读取 `search_get_agent_context`，其中包含明确的先前会话交接信息、当前 Git 状态、运行时历史和之前的迭代。该次调度还可以覆盖 `worker_agent_type` 或 `worker_budget`。 |
| 使用 `search_continue_agent_session` 进行同一 worker 延续 | 取决于宿主 | 当宿主提供可靠句柄时，复用之前的宿主 worker/会话。OpenCode 通过 `Task(task_id=...)` 支持此能力；Claude Code 可视情况通过 `SendMessage` 支持；Codex 和 Pi RPC 适配器明确不支持。 |

当 worker 达到 step/turn/time 上限、返回时没有有效的验证器证据，或需要更高等级的
worker 时，默认使用状态级恢复。同一 worker 延续只是一项优化，并非可移植的恢复模型。

Search 历史由运行时保存在 `.gp/runs/...` 下，而不是存储在 `plan.md` 文件中。
详细的恢复和延续矩阵请参阅 [agent-host-adapters.md](docs/agent-host-adapters.md)。

## 策略

Pi RPC、Codex 和 Claude Code 的可移植策略子集包括：

- `agent_guided`
- `agent`
- `default`
- `random`
- `random_mode`

对于现有经过 OpenCode 测试的策略，例如 `independent_branches`、`evolve`、
`openevolve`、`mcts`、Python 策略插件和 trace 导出，OpenCode 仍是兼容性基线。
请参阅 [examples/README.md](examples/README.md)、
[docs/strategy-openevolve.md](docs/strategy-openevolve.md) 和
[docs/strategy-adaptevolve.md](docs/strategy-adaptevolve.md)。

## 仓库结构

```text
.pi/                                  # Pi 提示词、技能和扩展
.codex/config.example.toml            # 纳入版本控制的 Codex MCP 配置模板
.codex/config.toml                    # 已忽略的本地 Codex MCP 配置
.codex/hooks.json                     # Codex Goal Plus 宿主钩子
.codex/skills/                        # Codex 技能
.codex/agents/                        # Codex worker agent 配置
.mcp.json                             # 项目级 Claude Code MCP 配置
.claude/                              # Claude Code 设置、技能和 worker agent
opencode.json                         # 项目级 OpenCode MCP 配置
.opencode/                            # OpenCode 命令、技能和 worker agent
scripts/hooks/goal_plus_stop.py       # 用于本地钩子测试的旧版包装器
docs/                                 # 设计、宿主、调试和策略文档
examples/                             # 内置 SearchSpec 示例
src/goal_plus/                        # 运行时、模型、工具和服务器
tests/                                # 单元、集成、资产和可选 ST 测试
```

## 开发检查

```bash
python -m pytest -q
git diff --check
```

运行时状态写入 `.gp/`，该目录已被 Git 忽略。
