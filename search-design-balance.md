# Search Design Balance

## 核心修正

需要重新考虑当前方案的中心假设。

之前的设计是从各种 search algorithm 出发：OpenEvolve、MCTS、tree search、agent-guided proposal，然后再问这些策略如何分配 candidate、如何派生 subagent。这个视角容易把 agent 降级成 candidate executor：策略在外部，agent 只是按方向写一个候选。

但 code agent 的自然形态不是“执行一个固定候选”，而是“在一个长上下文里连续 ReAct、自行搜索、试错、总结、再试”。现代 LLM 上下文已经足够长，强行把它切成很多短上下文 worker，未必是主路径。

更合理的中心假设应该是：

> Search 的默认主体是一个或多个长链自主 agent。MCP 不是用来规定 agent 每一步怎么做，而是提供共享状态、工作区隔离、可观测性、验证、历史和最终 promotion。

这样可以同时满足两件事：

- 保留单 agent 长链搜索的连续推理能力。
- 支持多 agent 并发自主探索，并且通过 MCP 观察彼此状态、产物和进展。

这不是完全推翻 MCP，而是改变 MCP 的定位：从“策略调度器 + worker executor 协议”，转向“agentic search control plane + shared observable blackboard”。

## 新的设计问题

真正的问题不是“用不用 subagent”，也不是“选 evolve 还是 MCTS”。

真正的问题是：

1. 如何让 agent 保持长链自主搜索能力？
2. 如何并发启动多个自主搜索链，而不是多个僵硬 candidate executor？
3. 如何让这些链互相可见，但不互相污染？
4. 如何让 main agent 能观察、干预、汇总、验证和停止它们？
5. 如何把固定搜索算法作为可插拔 advisor，而不是系统中心？

如果能做到这些，单上下文长链搜索和多并发探索不是冲突关系，而是同一个框架下的两种拓扑。

## 旧设计的问题

旧设计隐含了一个分层：

```text
strategy decides candidate directions
  -> main agent dispatches worker
    -> worker implements one candidate
      -> runtime verifies
```

这个分层适合 OpenEvolve/MCTS 这类算法，但不完全适合 LLM code agent。

问题在于：

- agent 的优势是连续反思和自适应搜索，不是只执行一个短 prompt。
- subagent 被限制成“只尝试当前方案”，会损失自主发现新方向的能力。
- main agent 为了管理 subagent，需要写越来越多上下文规则，复杂度上升。
- worker prompt 过窄时，worker 很容易做出平庸候选；prompt 过宽时，又会失控、跑很久、自己验证、写大量 scratch script。
- 固定策略先验过强时，第一批方向质量决定后续上限；如果一开始 seed 差，后续只是局部优化。

所以旧设计最大的问题不是实现问题，而是抽象层次不对：它把“搜索策略”放在 agent 之前，而不是把 agent 自身看成搜索策略的一部分。

## 新的中心：Autonomous Search Agent

新设计里，每个 search worker 应该被看作一个 autonomous search agent，而不是一个 candidate executor。

它拥有：

- 独立 workspace。
- 独立长上下文。
- 自己的局部计划和探索路径。
- 可持续更新的状态。
- 可提交多个中间观察和一个或多个候选产物。
- 对其他 agent 状态的受控可见性。

它不一定每次只交付一个固定候选。更自然的工作方式是：

```text
read MCP context
  -> form local search plan
    -> explore within budget
      -> publish observations / current status
        -> optionally inspect peer summaries
          -> continue or pivot
            -> submit best candidate + reasoning
```

main agent 不需要提前替它规定所有细节。main agent 更像 search supervisor：

- 定义问题和 verifier。
- 冻结 spec 和 edit surface。
- 启动若干 autonomous search sessions。
- 观察每个 session 当前在干什么。
- 在必要时发送 nudges 或停止低价值方向。
- 汇总 runtime verification 和 agent summaries。
- 决定下一批并发搜索是否继续。

## MCP 的新定位

MCP 不应该主要用来强制 subagent 遵循一套很窄的流程。它应该提供这些能力：

### 1. Durable Search State

记录 run、spec、workspace、budget、verifier、candidate、score、report。

这部分沿用当前设计。

### 2. Agent Session Registry

每个自主 agent 都应该有一个 session record：

```json
{
  "agent_session_id": "agent_001",
  "run_id": "run_xxx",
  "workspace": ".search/runs/run_xxx/workspace/a001",
  "status": "running",
  "phase": "probing | implementing | experimenting | blocked | submitting",
  "current_goal": "try a staggered grid packing with local adjustment",
  "last_update_at": "...",
  "budget": {
    "deadline_at": "...",
    "max_verifier_runs": 0,
    "max_wall_seconds": 600
  }
}
```

这和当前 `WorkerDispatch` 类似，但语义不同：不是一次短 dispatch，而是一个可持续观察的 search session。

### 3. Heartbeat And Progress Events

agent 应该定期写入轻量状态，而不是只在最后提交 artifact：

```json
{
  "agent_session_id": "agent_001",
  "phase": "implementing",
  "current_goal": "replace ring layout with hex-grid initializer",
  "last_action": "edited initial_program.py",
  "confidence": "medium",
  "blockers": [],
  "next_step": "run static check and submit first candidate"
}
```

这解决用户当前关心的问题：main agent 不是只能看到“subagent 还在跑”，而是能看到它到底卡在哪个阶段。

### 4. Shared Observation Blackboard

agent 可以发布中间发现：

```json
{
  "type": "observation",
  "agent_session_id": "agent_001",
  "summary": "ring layout gives stable valid packing but leaves large gaps near corners",
  "evidence": "static inspection / local reasoning / runtime verified score",
  "next_ideas": ["try staggered rows", "seed corner circles explicitly"],
  "visibility": "run"
}
```

其他 agent 可以读取这些 observation，但不必读取完整 transcript。这样能共享有效发现，又不强制上下文融合。

### 5. Verification And Candidate Archive

runtime 仍然拥有 official verification：

- verifier artifact frozen。
- process verifier 由 runtime 执行。
- score 和 metrics 进入 candidate history。
- candidate summary、changed files、lineage、observations 都进入 report。

agent 可以有不同自治级别，但最终选择仍应该基于 runtime verification。

## 需要的新 MCP API

当前 API 已经有一部分基础，但如果采用新 framing，需要增加或重命名一些接口。

### Session Lifecycle

```text
search_start_agent_session(run_id, directive?, workspace_policy?, budget?)
search_get_agent_context(agent_session_id)
search_finish_agent_session(agent_session_id, status, summary)
search_abort_agent_session(agent_session_id, reason)
```

这比 `search_prepare_worker` 更自然。`prepare_worker` 听起来像派一个任务，`start_agent_session` 表达的是启动一条自主搜索链。

### Status And Observability

```text
search_update_agent_status(agent_session_id, phase, current_goal, last_action, next_step, blockers?)
search_list_agent_status(run_id, status?, top_n?)
search_get_agent_status(agent_session_id)
search_list_agent_events(run_id, agent_session_id?, event_type?, top_n?)
```

这部分是关键。没有它，main agent 无法区分：

- agent 在认真探索。
- agent 卡在 API timeout。
- agent 在跑过长脚本。
- agent 已经有 candidate 但还没提交。
- agent 方向明显无效，需要停止。

### Shared Observation

```text
search_publish_observation(agent_session_id, summary, evidence?, next_ideas?, tags?)
search_list_observations(run_id, visibility?, tags?, top_n?)
```

这让其他 agent 能看到“有效中间知识”，而不是被迫读别人的完整上下文。

### Candidate Submission

```text
search_submit_candidate(run_id, candidate_id, artifact)
search_run_verifier(run_id, candidate_id, scope)
search_list_history(run_id, top_n, sort_by)
```

这部分可以沿用当前设计。

## Peer Visibility：既要可见，也要防止坍缩

让 subagent 看到其他 subagent 当前状态是有价值的，但不能无限制共享。

如果所有 agent 实时看到所有细节，可能出现两个问题：

- 过早收敛：大家都复制当前看起来最好的方向，探索多样性下降。
- 噪声污染：一个错误观察被其他 agent 当成事实放大。

所以需要可配置 visibility mode：

```text
none          # 完全独立
status_only   # 只看其他 agent 的 phase/current_goal
observations  # 看别人主动发布的 summary/next_ideas
top_history   # 看 runtime verified top candidates
full          # 可看完整 artifact/history，通常只给 main agent
```

默认建议：

- worker 看 `status_only + observations + verified top_history`。
- main agent 看 full run state。
- strategy advisor 可以看 full state，但输出要记录 trace。

这样能同时保留并发独立性和跨 agent 信息流。

## 固定搜索算法怎么放

OpenEvolve、MCTS、AutoResearch、tree search 不应该消失，但不应该是唯一顶层框架。

更合理的位置是 advisor/planner：

```text
agent-native search runtime
  |
  +-- optional evolve advisor: suggests parent/inspirations
  +-- optional mcts advisor: suggests frontier node
  +-- optional autoresearch advisor: suggests query/decomposition
  +-- optional human/main-agent advisor: suggests next batch
```

这些 advisor 可以：

- 读取 runtime history。
- 选择 parent/frontier/seed。
- 给 autonomous agents 提供 starting hint。
- 调整 budget allocation。

但 agent session 仍然可以自主探索。也就是说，固定算法提供“搜索偏置”，不是替代 agent 的搜索过程。

这解决了你提到的初始方向问题：好 seed 很重要，所以系统应该支持强 seed；但 seed 不应该把 agent 限死成只执行一个短方案。

## 新的运行模式

### Mode A: Single Long-Chain Search

一个主 agent 自行搜索，MCP 负责状态、workspace、verifier、history。

适合：

- 小到中等任务。
- 需要连续推理。
- 并发收益不明显。
- 用户正在交互式编码，突然想探索一个问题。

这是默认基础模式。

### Mode B: Parallel Autonomous Search

启动多个 autonomous agent session。每个 agent 有独立 workspace 和长链上下文，通过 MCP 更新状态和发布 observation。

适合：

- 多个方向都可能有效。
- 单个方向探索时间较长。
- 希望用 wall-clock 并发换探索覆盖。
- 可以接受部分 agent 超时或失败。

这是你现在提出的主路径。

### Mode C: Advisor-Guided Parallel Search

在 Mode B 上加一个 planner/advisor。advisor 可以是 main agent、OpenEvolve、MCTS、custom Python strategy。

适合：

- 已经有 verified history。
- 需要从 best parent 派生。
- 需要控制 exploration/exploitation。
- 需要把固定算法经验接进 agent search。

### Mode D: Fully Autonomous Worker Search

agent 能自己跑 verifier、做局部程序搜索、提交 best-so-far。

这只适合有 sandbox、硬 timeout、资源预算和日志回收的环境。否则容易复现当前看到的问题：worker 自己写一堆脚本、跑很久、main agent 只知道它没回来。

## 对当前实现的重新判断

当前实现不是完全错，但抽象需要上移。

保留：

- frozen spec。
- candidate workspace。
- runtime-owned verifier。
- `search_list_history`。
- report/promote。
- dispatch/context hash。

需要弱化：

- worker 只是 candidate executor 的假设。
- strategy 先于 agent 的假设。
- main directive 必须详细规定候选方向的假设。

需要增强：

- agent session 状态模型。
- heartbeat/progress event。
- observation blackboard。
- peer visibility policy。
- main agent 对 running workers 的监控和干预能力。
- stale/timeout/blocked worker 的统一处理。

## 最小改造路径

不需要一次推翻现有 API，可以在当前基础上增量改。

### Step 1: 把 WorkerDispatch 语义扩展成 AgentSession

当前 `search_prepare_worker` 可以继续存在，但文档和模型上把它理解为创建 agent session。

新增字段：

- `agent_session_id`
- `phase`
- `current_goal`
- `last_action`
- `next_step`
- `blockers`
- `last_heartbeat_at`
- `visibility_mode`

### Step 2: 增加状态更新 API

新增：

```text
search_update_agent_status
search_list_agent_status
search_publish_observation
search_list_observations
```

这是最关键的能力。没有它，多 agent 并发只是“黑盒等待”。

### Step 3: 修改 AnySearchAgent prompt

不要再强调“你只执行这个固定方案”。改成：

- 你是一条 autonomous search session。
- 你有一个 starting direction，但可以在 budget 内自主调整。
- 你必须定期 update status。
- 你可以读取 peer status/observations。
- 你必须在 deadline 前提交 best-so-far。
- official verification 由 runtime/main agent 负责，除非 policy 允许本地验证。

### Step 4: Main Agent 改成 Supervisor

main agent 不应该只 dispatch 后等待。它应该循环：

```text
list agent status
list observations
identify stale/blocked agents
verify submitted candidates
publish verified history
decide whether to nudge, stop, or start more agents
```

### Step 5: Strategy 变成 Advisor

现有 `search_plan_next` 保留，但它输出的不是“worker 必须执行的候选命令”，而是：

- recommended seeds
- suggested directions
- parent/inspiration hints
- budget allocation
- visibility policy

agent 可以从这些 hints 开始，但不是机械执行。

## 借鉴 OpenCode 的 subagent 限制方式

OpenCode 的 subagent 没有真正的通用 wall-clock timeout。它强制的是 agentic iteration budget，也就是 `agent.steps`。

这里的 step 不是 token、stream chunk，也不是 subagent 自己计划里的“第几步”。它更接近一次 ReAct continuation：

```text
one step =
  send one model request
  -> model may emit text/tool calls
  -> runtime settles those tool calls
  -> if tool calls happened and session needs continuation, enter next step
```

所以 `steps=5` 的含义大致是“这个 agent 最多经历 5 轮模型请求/工具结算循环”。如果某一轮模型直接给最终文本并停止，后续 step 不会发生；如果每轮都继续调用工具，runner 会递增 step。

核心机制是：

1. `Task` 工具创建一个 child session，并把它注册成 background job。
2. 如果是 foreground task，父会话等待这个 background job 完成；如果父会话被 abort，会 cancel child session 和 background job。
3. 每个 agent 可以配置 `steps`，旧字段 `maxSteps` 只是兼容入口。
4. runner 每一轮维护 `step` 计数；当 `step >= agent.steps` 时，不再 materialize tools，设置 `toolChoice: none`，并额外塞入 `MAXIMUM STEPS REACHED` 的强制总结 prompt。
5. 如果模型在 final step 仍然发出 tool call，runner 会把未完成 tool 标记为失败，原因是 tools 已因最大步数被禁用。

这说明 OpenCode 的硬约束不是“提醒 subagent 别跑太久”，而是在 runtime 层改变下一次模型请求：

```text
normal step:
  messages + tools + toolChoice auto

final step:
  messages + MAX_STEPS_PROMPT
  tools = []
  toolChoice = none
```

这个设计很值得借鉴，因为它把 budget enforcement 放在 agent 外部，而不是只依赖 prompt 服从。

但它也有明显边界：

- `steps` 不是 wall-clock timeout。单个 step 里的 provider stream、tool call、bash、网络请求仍可能很久。
- background job 的 `wait(timeout)` 只是返回 timedOut snapshot，不会自动 cancel job。
- provider timeout、retry、watchdog 在 OpenCode v2 spec 里还是 deferred；runner 没有统一 provider-stream inactivity 或 absolute timeout。
- subagent 并发主要来自模型同一轮发多个 `Task` tool call，runner 用 FiberSet 并发 settle tool calls；没有看到一个面向 subagent 的全局 `max_parallel` admission control。

但这不代表 OpenCode 没有主动中断能力。它有 session abort/interrupt 链路：

- HTTP API 有 `POST /session/:id/abort`。
- TUI 的 `session.interrupt` 会走同类取消路径。
- foreground `Task` 在父级 abort 时会 abort child task context，并 cancel child session/background job。
- `SessionRunState.cancel` 会取消当前 session 相关的 background jobs，并 interrupt 当前 runner。
- LLM/tool execution 接收 `AbortSignal`，多数 provider/tool 调用能被 abort 传播打断。

因此更准确的判断是：

```text
OpenCode 支持主动 abort running session/subagent。
OpenCode 支持 step budget 自动收口。
OpenCode 没有看到通用的、配置化的 per-subagent wall-clock timeout/watchdog 自动策略。
```

如果 subagent 一直输出乱码但 provider stream 还在持续，用户或外部控制面可以 abort session；但如果没有外部 watchdog 自动调用 abort，它不会仅因为“输出看起来像乱码”自动停。`steps` 也帮不上这种情况，因为 step 只有在一轮 provider stream 结束并进入下一轮时才会生效。

所以对 Search MCP 来说，不能把 OpenCode 的 `steps` 当成完整 timeout 方案。它解决的是“工具调用轮数失控”，不是“单轮 provider 输出失控、总时间、资源、并发失控”。

## Search MCP 的 budget 应该分层

agent/subagent 关系应该通过多维 budget 管理，而不是只有一个 `timeout_seconds`。

建议拆成四层：

### 1. Run Budget

整个 search run 的总预算：

```json
{
  "run_deadline_at": "...",
  "max_wall_seconds": 1800,
  "max_candidates": 32,
  "max_concurrent_agents": 4,
  "max_verifier_runs": 64,
  "max_tokens": null
}
```

这层由 runtime/admission controller 强制。任何新 agent session 启动前都要检查：

- run 是否已经超时。
- active agent 数是否达到 `max_concurrent_agents`。
- 剩余时间是否足够给这个 agent 一个最小有效窗口。
- candidate / verifier / token 预算是否已经耗尽。

如果不满足，不能继续“让 agent 自己判断”，应该直接返回 `budget_exhausted` 或进入 queued/pending。

### 2. Agent Session Budget

每条 autonomous search session 的局部预算：

```json
{
  "agent_session_id": "agent_001",
  "max_wall_seconds": 600,
  "deadline_at": "...",
  "max_steps": 12,
  "max_tool_calls": 40,
  "max_verifier_runs": 0,
  "heartbeat_interval_seconds": 30,
  "stale_after_seconds": 90
}
```

这里要同时有 wall-clock 和 step budget：

- `max_wall_seconds` 控制“这条探索链最多占用多久”。
- `max_steps` 控制“这条探索链最多有多少轮 ReAct/tool-using iteration”。
- `max_tool_calls` 控制“单条链最多消耗多少工具动作”。
- `max_verifier_runs` 控制 worker 是否能做本地 verifier。

OpenCode 已经证明 `max_steps` 可以通过“final step 禁 tool + 强制总结”实现。Search MCP 可以采用同样思想：当 session 接近 step 上限时，下一轮只能提交 best-so-far、发布总结或声明失败，不能继续探索。

### 3. Tool And Resource Budget

不是所有工具成本一样。需要区分：

- read/search/list 这类低成本探索工具。
- edit/write/apply_patch 这类改变 candidate 的工具。
- bash/exec 这类可能长时间运行的工具。
- verifier/scorer 这类最昂贵且会污染搜索行为的工具。

MCP 侧至少要对 verifier 做硬计数，因为 verifier 是 search objective 的真实反馈入口。对 bash/scratch experiment，也应该记录和限制，避免 subagent 写一个本地参数搜索器绕开预算。

### 4. Supervisor Policy

main agent 不应该只是等待 timeout。它应该周期性执行：

```text
list_agent_status
list_observations
list_active_sessions
mark stale sessions
cancel or salvage timed-out sessions
start queued sessions if slots free
verify submitted candidates
```

这层负责把 budget 用在有效方向上：

- 对 stuck/stale session 发一次 nudge。
- 对低价值方向提前 abort。
- 对接近 deadline 的 session 要求提交 best-so-far。
- 对已提交 candidate 的 session 尽快 runtime verification。

## Timeout 的语义要明确

当前 `worker_timeout_seconds` 更像 collection deadline：到点后 main agent 应该收集或放弃结果，但 V0 并不会终止 OpenCode subagent。

新设计里建议明确区分三种 timeout：

```text
soft_deadline:
  到点后要求 agent finalize best-so-far；允许短暂 grace period。

hard_deadline:
  到点后 runtime 取消/标记 session timed_out，不再接受新的 edits/verifier。

stale_timeout:
  超过一段时间没有 heartbeat/status update，supervisor 标记 stale 并决定 nudge/cancel。
```

这样 `timeout_seconds` 不再是含混字段，而是：

- `deadline_at`：预算时间点。
- `finalize_before_seconds`：提前多少秒进入 finalizing。
- `grace_seconds`：允许总结/提交的宽限。
- `stale_after_seconds`：观测层的卡死判断。

## 对当前 MCP API 的直接改动建议

保留 `search_prepare_worker` 作为兼容入口，但新 API 应该以 session 为中心：

```text
search_start_agent_session(run_id, candidate_id?, directive?, budget?)
search_update_agent_status(agent_session_id, phase, current_goal, last_action, next_step, heartbeat=true)
search_list_agent_status(run_id, include_stale=true)
search_wait_agent_events(run_id, timeout_seconds, wake_on?)
search_request_agent_finalize(agent_session_id, reason)
search_abort_agent_session(agent_session_id, reason)
search_abort_all_agent_sessions(run_id, reason)
search_record_agent_step(agent_session_id, tool_calls_delta?, verifier_runs_delta?, tokens_delta?)
```

其中 `search_start_agent_session` 必须做 admission control：

- active sessions >= `max_concurrent_agents` 时拒绝或排队。
- run deadline 已过时拒绝。
- requested per-agent budget 超过 run remaining budget 时截断。
- session 创建后写入 `deadline_at` 和 `budget_snapshot`，避免后续上下文漂移。

`search_get_agent_context` 返回的 prompt/brief 里仍然可以写 deadline，但 runtime 不能只依赖 prompt。真正的强约束应该由 MCP 状态机和 host integration 执行。

### Supervisor Wait Loop

main agent 可以采用“启动到 pool 满，然后 wait”的调度方式，但这个 wait 不应该是让 agent 自己 sleep，也不应该靠自然语言记时间。应该由 MCP/runtime 提供阻塞式 wait：

```text
search_wait_agent_events(
  run_id,
  timeout_seconds=300,
  wake_on=["agent_completed", "agent_failed", "agent_blocked", "slot_available", "run_deadline"]
)
```

语义是：

- 如果 5 分钟内有 subagent 完成、失败、blocked，立即返回事件和最新状态。
- 如果一直没有事件，5 分钟后返回 `timed_out=true` 和 active session snapshot。
- 如果 run hard deadline 到达，立即返回 `run_deadline_reached=true`。
- wait 本身不消耗 subagent slot，也不启动新工作。

这样 main agent 的控制循环可以是：

```text
while run budget remains:
  status = search_list_agent_status(run_id)

  while active_count(status) < max_concurrent_agents and queued/seeds remain:
    search_start_agent_session(...)

  event = search_wait_agent_events(run_id, timeout_seconds=300)

  if event.run_deadline_reached or run remaining time <= 0:
    search_abort_all_agent_sessions(run_id, "run budget exhausted")
    break

  if event has completed agents:
    read summaries / submitted candidates
    run official verifier
    decide whether to start next agents

  if event timed_out:
    inspect heartbeats/status
    request finalize for stale or near-deadline agents
    abort clearly stuck agents if policy says so

summarize history and select/promote best verified candidate
```

这个模型和 OpenCode 的内部 `BackgroundJob.wait(timeout)` 很像：等待完成或超时后把控制权还给上层。但 Search MCP 需要额外补上三件事：

- `wait` 返回的是 run-level event/status，不只是某个 job 的 snapshot。
- pool 上限由 MCP admission control 强制，而不是靠 main agent 自觉。
- run hard deadline 由 runtime 强制触发 `abort_all`，不能只依赖 main agent 看到时间后自愿停止。

所以推荐拓扑是：

```text
main agent = policy decision maker
MCP/runtime = clock + pool admission + durable status + hard abort owner
subagents = autonomous workers
```

main agent 可以决定“这个 stuck agent 是否值得再等 5 分钟”，但 runtime 必须保证“无论 main agent 怎么想，active subagent 数不超过 pool，run deadline 到了就不能继续烧资源”。

## 最小可实现策略

如果暂时不能真正 kill OpenCode subagent，也可以先做一个半硬版本：

1. MCP 持久化 `agent_session`，包含 deadline、heartbeat、budget counters。
2. main agent/supervisor 负责每 20-30 秒轮询 status。
3. 到 soft deadline 时调用 `search_request_agent_finalize`，让 worker 只能提交 best-so-far。
4. 到 hard deadline 时 MCP 将 session 标记为 `timed_out`，后续 `submit_candidate` 如果来自该 session，需要进入 `late_submission` 状态，默认不参与 selection，除非 main agent 显式 accept。
5. 如果 host 以后接入 OpenCode session control，再把 hard deadline 映射成真实 `cancel(child_session_id)`。

这个路径符合 OpenCode 的现实边界：现在能借鉴 `steps` 的 runtime gating 思想，但不假设 OpenCode 已经提供完整 wall-clock subagent timeout。

## 安全边界仍然必要

放宽 agent 自主性，不等于取消硬边界。

必须继续强管：

- 不能改 verifier/config。
- 不能改 main workspace。
- 每个 agent 有自己的 workspace。
- promotion 只能通过 runtime patch。
- destructive commands 受限制。
- official score 只能来自 runtime verifier。

这些不是对 agent 智能的束缚，而是实验环境的边界。

真正应该放宽的是：

- agent 是否只能尝试一个固定方案。
- agent 是否能在自己 workspace 内形成局部计划。
- agent 是否能根据 peer observation 改变方向。
- agent 是否能持续发布中间发现，而不是最后才交付。

## 是否能两者都满足

可以，但前提是把系统设计成：

```text
long-chain autonomous agents
  + parallel workspaces
  + MCP shared status/observation/history
  + runtime-owned verification
  + optional strategy advisors
```

这样：

- 单 agent 长链搜索仍然是自然模式。
- 多 agent 并发探索也成立。
- main agent 能看到每个 subagent 当前状态。
- subagent 能看到其他 agent 发布的有效信息。
- 固定 search algorithm 可以接入，但不支配整个架构。
- MCP 的价值从“限制 agent”转成“让搜索过程可观察、可恢复、可验证”。

## 一句话结论

应该重新考虑方案：Search MCP 的主路径不应是“外部策略生成候选，subagent 执行候选”，而应是“一个或多个长链 agent 自主搜索，MCP 提供共享状态、可观测性、工作区隔离和 runtime verification”。固定搜索算法仍然有价值，但应该作为 advisor/planner 给 autonomous agents 提供 seed、parent、frontier 和预算建议，而不是替代 agent 自己的搜索过程。
