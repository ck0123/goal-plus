# Goal Plus 并行自治 Loop 设计

本文定义 Goal Plus 在并行模式下如何取消 main agent 的搜索 conductor 角色。

这里不是重新实现一套 loop runtime。当前 candidate worker 已经能够在同一个
workspace 内持续执行多轮 AutoResearch：分析、修改、调用 verifier、比较结果并继续。
真正需要改变的是：

1. 将 AtomicPlan/Search State 逻辑放入 subagent 自己的循环；
2. main 不再根据 subagent 的结果判断下一轮方向并派发新 Agent；
3. subagent 返回但全局任务尚未结束时，main 只 resume 原 subagent；
4. main 在每次 completion 时完成验收，并让 runtime 更新 best-so-far；
5. 只有明确不可恢复的 workspace/session 污染才允许创建替代 Agent。

搜索空间和 AtomicPlan 语义参考
[搜索图式诱导与事务化协调](search_space.md)。本文只描述 main/subagent/host 的
控制流程。

> 本文中的 API 名称表示逻辑合同，不要求最终必须新增同名 MCP 工具。

## 当前实现状态

本次仓库改动已经交付不依赖 AtomicPlan 的控制流部分：

- `parallel_loops` strategy mode 和单次 initial SearchPlan 约束；
- Codex 的 completion 验收、best 观察和同一 native worker continuation；
- Pi 的 wait-any、best 观察和同一 candidate/workspace logical continuation；
- Codex/Pi 真机 cycle 与报告证据。

AtomicPlan、共享 Search State、admission 和 EvidenceCommit 不在本次实现范围内，仍由
对应的外部工作补齐。Pi 当前继续使用新的 `--no-session` worker segment，因此保持的
是逻辑 candidate/workspace，而不是同一个 native session。

## 1. 核心结论

当前系统已经具备绝大部分底层能力：

- candidate 对应独立 workspace；
- 一个 subagent 可以连续完成多轮修改和 verifier；
- verifier 结果进入 `IterationRecord` 和 `results.tsv`；
- `run_verifier` 会根据 metric direction 更新 `run.best_score` 和
  `run.best_candidate_id`；
- Codex 可以通过 `search_continue_agent_session` + `followup_task` 继续原生
  subagent；
- Pi 可以通过 `pi_search_pool_continue` 恢复相同 candidate/workspace，但当前会
  启动新的无 session Pi 进程。

因此，不需要新增一个独立的 Loop Agent 调度层，也不需要把每个 AtomicPlan
materialize 成新的 subagent。

目标变化可以压缩为一句话：

> **Main 负责启动和验收；subagent 负责搜索；未结束就 resume 原 subagent，不再由
> main 设计并派发“下一轮”。**

---

## 2. 改造前流程与问题

### 2.1 已有的 subagent 内部循环

当前 worker prompt 已要求 subagent 在一个 candidate workspace 内持续运行：

```text
读取 candidate context
-> 分析瓶颈
-> 修改 artifact
-> search_run_verifier
-> 比较 results.tsv
-> 继续下一种 hypothesis
-> 最后返回 main
```

这已经是一条完整 AutoResearch loop。AtomicPlan 应进入这条循环，而不是由 main
在 subagent 外面再包一层智能调度。

### 2.2 改造前 main 仍然是 conductor

当前 Codex/Pi skill 在每个 worker completion 后要求 main 选择：

```text
continue current candidate
or create a new candidate
or refill a free slot with another direction
or leave the slot idle
or begin final selection
```

问题不在于 main 会收到 completion，而在于 main 需要解释每个结果、判断方向价值、
生成下一轮 proposal，并决定应该继续谁或替换谁。

这会导致：

- subagent 的搜索策略在每次返回时被 main 截断；
- main prompt 承担大量跨 candidate 比较和方向分配；
- main 与 subagent 形成反复的 conductor/worker 对话；
- 并行数量增加后，main 的 completion 决策成为中心瓶颈；
- 同一条搜索链可能因为 main 的短期判断被替换，而不是让原 subagent基于完整上下文
  自己调整。

### 2.3 改造前能力判断

| 能力 | 当前状态 | 需要变化 |
|---|---|---|
| 一个 subagent 内多轮 AutoResearch | 已存在 | 加入 AtomicPlan/Search State |
| 一个 candidate 长期复用 workspace | 已存在 | 保持不变 |
| 多个 candidate 同时执行 | 已存在 | 保持不变 |
| verifier 更新 best-so-far | 已存在 | main completion 时确认即可 |
| Codex 原生 subagent continuation | 已存在 | 改成默认 resume 路径 |
| Pi 相同 candidate 状态恢复 | 已存在 | 当前不是同一原生进程 |
| main 根据结果选择 continue/refill/new | 已存在 | 从正常流程删除 |
| subagent 自己生成 AtomicPlan | 不存在 | 由外部 AtomicPlan 工作补齐 |
| shared Search State/admission | 不存在 | 由 loop/runtime 工作补齐 |

因此，目标不是从零实现 parallel loop，而是把当前的并行 candidate worker 从
“main 决定下一段”改成“subagent 持续拥有自己的搜索链”。

---

## 3. 目标流程

### 3.1 启动

Main 只在 run 开始时进行一次搜索链部署：

```text
raw goal
-> freeze SearchSpec / verifier
-> search_create
-> create initial N candidates
-> start one subagent for each candidate
```

初始 candidate 可以有不同 seed/directive，但这只是第一次任务分配。Main 不在后续
completion 中重新设计方向。

第一版可以继续使用现有 `search_plan_next` 和 `search_start_batch` 创建初始 N 个
candidate。关键约束是：**run 启动后，正常 completion 不再触发新的 plan/batch。**

### 3.2 Subagent 内部循环

每个 subagent 在已有 candidate workspace 内自主执行：

```text
while local budget remains:
    context = search_get_agent_context(...)
    state = read latest shared Search State

    atomic_plan = AtomicPlan Agent(
        objective,
        candidate workspace/history,
        shared state,
        remaining budget
    )

    admission = submit atomic_plan

    if admission is rejected or stale:
        refresh state
        generate another AtomicPlan
        continue

    apply the admitted intervention in the same workspace
    report = search_run_verifier(...)
    commit verified evidence / update Search State
    compare with candidate-local and global evidence
    continue

return only when the current host turn ends or a stop condition is reached
```

AtomicPlan 控制的是 subagent 的下一次干预，不是 main 的下一次 Agent 派发。

### 3.3 每次 completion 的 main handler

Main 仍然会被 host completion 唤醒，但处理必须是确定性的：

```text
on subagent completion:
    bind terminal host evidence
    run parent-owned final verifier when required
    observe runtime best-before / best-after

    if candidate improved the global best:
        keep the new best candidate/artifact as latest answer

    if global success condition is satisfied:
        do not resume this subagent
    elif outer time cannot fit another useful worker turn:
        do not resume this subagent
    elif run/spec/verifier has been invalidated:
        do not resume this subagent
    else:
        resume the same subagent on the same candidate/workspace
```

Main 不需要根据 score 判断“这个方向值不值得继续”。只要全局任务没有完成、run
仍有效且还有时间，就 resume 原 subagent，让它自己根据 Search State 和 AtomicPlan
决定如何调整。

### 3.4 最终收尾

当全局目标完成、外层预算结束或所有链进入不可恢复终态后：

```text
drain/interrupt remaining subagents
-> search_select
-> parent-owned verification
-> search_report
-> search_promote when requested
-> raw-goal final audit
```

Final selection 仍由 main 负责，但不能反向变成下一轮搜索调度。

---

## 4. 角色边界

### 4.1 Main Agent

负责：

- 创建 Goal Plus/Search run；
- 一次性生成初始 candidates 并启动 subagents；
- 等待任意 subagent completion；
- 验收 completion 对应的 artifact/verifier evidence；
- 观察 runtime 是否更新 best-so-far；
- 在全局任务未完成且有剩余时间时 resume 原 subagent；
- 最终 drain、select、verify、promote、record 和 audit；Goal Plus 进入终态后再
  一次性生成 report。

不负责：

- 给 resume 的 subagent指定新的技术方向；
- 根据某次低分决定替换 candidate；
- 把一个 subagent 的 handoff 编译成另一个 subagent 的 proposal；
- completion 后再次调用 `search_plan_next`/`search_start_batch` 补槽；
- 在多个方向之间动态分配 exploitation/exploration；
- 逐轮生成 AtomicPlan。

Main 是 completion validator 和 continuation trigger，不是 search conductor。

### 4.2 Subagent

负责：

- 持续拥有一个 candidate workspace；
- 读取本 candidate 的完整 iterations/results/handoff；
- 读取已提交的共享 Search State；
- 每轮调用 AtomicPlan Agent；
- 处理 admission reject/rebase；
- 自己选择继续当前机制、换 hypothesis、rebase 或探索新方向；
- 每个实质修改后调用 verifier；
- 在 return 前写入可恢复 handoff。

不负责：

- 最终 select/report/promote；
- 修改官方 verifier/spec；
- 直接写 verified Search State；
- 读取 peer 的 chain-of-thought 或未提交 workspace；
- 指挥其他 subagent。

### 4.3 AtomicPlan Agent

AtomicPlan Agent 是 subagent 每轮调用的单步 plan compiler，不是独立 conductor。

输入：

- 当前 objective 和硬约束；
- 当前 candidate workspace/history；
- 最新 Search State/schema；
- active reservations；
- 剩余预算。

输出：

- typed intervention；
- hypothesis 和 expected observation；
- base state/schema version；
- declared footprint；
- budget request。

它不创建 subagent、不操作 host pool、不决定 global stop/select，也不写 verified facts。

### 4.4 Runtime

负责：

- SearchSpec、candidate workspace 和 budget；
- AtomicPlan admission/reservation；
- verifier、Git/artifact provenance；
- EvidenceCommit 和共享 Search State；
- best-so-far、selection、report 和 promotion 数据。

不负责：

- launch/wait/stop/resume host process；
- 决定 candidate 是否“值得继续”；
- 自动创建替代 subagent；
- 保存或猜测 host liveness。

### 4.5 Host

Host 负责：

- spawn、wait、resume、interrupt；
- worker deadline 和 closeout；
- native session handle、日志和 observability；
- 将 completion 交还 main。

Host 不生成搜索方向，也不根据 score 自动替换 Agent。

---

## 5. Candidate 就是 Loop

第一版不新增 `LoopLane`、`LoopRecord` 或另一套 workspace 生命周期。

现有对象已经能表达一条搜索链：

```text
candidate_id
candidate workspace
agent_session_id
native host handle
iterations
results.tsv
progress handoff
worker budget
```

目标映射是：

```text
one initial candidate
    = one autonomous search loop
    = one long-lived workspace
    = one native subagent when the host supports continuation
```

同一个 subagent 可以在 workspace 内产生多次 AtomicPlan 和 verifier iteration。
AtomicPlan 不要求新建 candidate；它只是对下一次搜索干预进行结构化和准入。

### 5.1 Resume 保持什么

正常 resume 必须保持：

- 相同 `candidate_id`；
- 相同 workspace 和 Git history；
- 相同 results ledger；
- 相同逻辑 subagent identity；
- Codex 中尽量保持相同 native task/session；
- AtomicPlan/Search State 的最新 committed context。

Resume prompt 只应表达：

```text
Continue the same autonomous search loop from the latest committed evidence.
Refresh Search State, generate the next AtomicPlan, verify each material change,
and keep working while the assigned budget remains.
```

它不能包含 main 新生成的技术 hypothesis 或方向排名。

### 5.2 Codex

Codex 当前已经有正确的底层 continuation：

```text
search_continue_agent_session(agent_session_id)
-> followup_task(existing task)
```

目标变化主要是 skill/prompt policy：completion 后只要全局任务未结束且时间允许，
默认调用上述 continuation；不再在 continue/new/refill 之间做主观选择。

### 5.3 Pi

Pi 当前的 `pi_search_pool_continue` 会对相同 candidate/workspace 执行
`redispatch=true`，但 worker 使用 `--no-session`，因此会启动新的 Pi 进程。

这能保持同一条**逻辑搜索链**，但不是同一个**原生 subagent session**。如果产品要求
Pi 也严格保持同一原生 Agent，需要增加 Pi session continuation 或让单个 Pi RPC
进程在外层预算内不提前退出。该差距不能用“已经支持 same-agent resume”描述。

---

## 6. 并行时序

Main 一次启动 N 个 candidate/subagent：

```text
Subagent A: loop -> return -> validate -> resume A -> loop -> ...
Subagent B: loop -----------> return -> validate -> resume B -> ...
Subagent C: loop ----> return -> validate -> resume C -> ...

Runtime:    AtomicPlan admissions and EvidenceCommits serialize
Main:       wait-any, validate, update best, resume same subagent
```

这里没有 batch barrier，也没有 completion 后的方向补槽：

- A 返回时不等待 B/C；
- main 验收 A 的当前 artifact；
- 如果 A 改进全局 best，runtime 记录新的 best；
- 如果全局尚未结束且时间允许，main resume A；
- A 自己决定下一步 AtomicPlan；
- main 不创建 D 来替换表现暂时较差的 A。

AtomicPlan admission 和 EvidenceCommit 仍按 `search_space.md` 串行提交：

```text
A/B/C draft AtomicPlans in parallel
-> admissions serialize
-> A/B/C execute in parallel
-> verifier-backed evidence commits serialize
```

---

## 7. Best-So-Far 与“最新答案”

Main 每次 completion 检查的不是“应该继续哪个方向”，而是“当前验证结果是否刷新
官方最优”。

Runtime 已有 metric-direction-aware best 更新：

```text
if process_passed and score is better than run.best_score:
    run.best_score = score
    run.best_candidate_id = candidate_id
```

并行模式下应保持：

- 每次 worker verifier 都可以产生 best-so-far；
- main completion verifier 用于验收当前返回 artifact；
- 更优结果更新 `best_candidate_id` 和对应 Git/artifact revision；
- 较差结果不会导致 candidate 被替换；
- `search_select` 仍然只在最终 drain 后执行；
- 不需要每次改进都 promotion 或创建新 run。

报告中的“latest answer”应指当前 verifier-backed global best，而不是最近完成的
subagent，也不是 main 主观挑选的方向。

---

## 8. Resume 与 Stop Policy

### 8.1 正常 Resume 条件

Subagent completion 后，只检查全局条件：

```text
run is valid
and global success is not satisfied
and user did not stop
and remaining outer time can fit another worker turn plus final closeout
```

满足时 resume 原 subagent。

以下条件不能作为停止或替换该 subagent 的理由：

- 当前 score 较低；
- 最近一次没有改进；
- main 更喜欢另一个方向；
- 另一个 candidate 暂时领先；
- subagent 的方案与 main 的直觉不同。

方向价值和 pivot 由 subagent + AtomicPlan/Search State 处理。

### 8.2 正常 Stop 条件

- 明确的 success criterion 已满足；
- 外层时间只够 final verification/report；
- Search run/spec/verifier 被 invalidated；
- 用户要求停止；
- host 返回不可恢复终态；
- subagent 已提交有证据的“当前解空间无可执行下一步”，且 runtime/目标合同允许结束。

### 8.3 污染后的 New Agent Escape Hatch

创建新 Agent 不是正常 refill，只能作为后续的显式恢复能力。

可以考虑的客观触发条件：

- workspace/Git/results ledger 无法恢复一致状态；
- native session context 已损坏或不可继续；
- candidate 长期基于错误 base，且无法安全 rebase；
- verifier infrastructure failure 修复后必须使用新 frozen spec/run；
- subagent 明确报告可复现的环境污染，而不是单纯低分。

优先顺序：

1. resume 相同 native subagent；
2. 用相同 candidate/workspace 做 state-level resume；
3. 从该 candidate 的最后一个 verified revision 恢复；
4. 只有前面都不可用时，创建带明确 lineage 的新 candidate/Agent。

“表现差”或“暂时没提升”本身永远不触发第 4 步。

---

## 9. Prompt 变化

### 9.1 Main Prompt

从当前 main workflow 删除：

- completion 后在 `deepen_incumbent`、`transfer_feature`、`macro_restart` 之间选择；
- 根据结果决定 continue 还是 new candidate；
- 为 free slot 调用新的 `search_plan_next`/`search_start_batch`；
- 将 handoff 手工改写为下一轮技术 directive；
- 因低分、无提升或方向不喜欢而停止/替换 subagent。

替换为：

```text
1. Create and launch the initial parallel candidates once.
2. Wait for any subagent completion.
3. Validate the returned artifact and verifier evidence.
4. Observe whether runtime best-so-far improved.
5. If the global stop policy is false, resume the same subagent with a neutral
   autonomous-loop continuation prompt.
6. When the global stop policy becomes true, drain and perform final selection.
```

### 9.2 Subagent Prompt

保留当前 AutoResearch loop 规则，并增加：

- 每轮基于最新 Search State 调用 AtomicPlan Agent；
- admission 通过前不实施该干预；
- stale/rejected plan 自己刷新并重提；
- return 后可能由 main resume，同一搜索链必须从 committed context 继续；
- 不等待 main 提供新的 hypothesis；
- 自己判断 exploitation、pivot、rebase 和新方向；
- 每次 material change 继续由 verifier 提供官方证据。

Subagent 仍禁止 select/report/promote 和修改 verifier。

### 9.3 AtomicPlan Prompt

AtomicPlan prompt 只生成当前 subagent 的下一次干预：

- 引用当前 state/schema version；
- 描述 hypothesis、intervention 和 expected observation；
- 声明 footprint、base artifact 和预算；
- 说明与已有 coverage/reservation 的关系；
- 不生成 host action；
- 不决定是否替换其他 Agent；
- 不决定 global stop/select。

---

## 10. Strategy 变化

### 10.1 SearchPlan 只负责初始部署

第一版不需要新的 strategy runtime。现有 `SearchPlan` 可以继续创建初始 N 个
candidate，但 run 开始后不再反复产生 batch。

```text
initial SearchPlan
-> N candidates/subagents
-> each candidate owns its autonomous loop
-> no recurring SearchPlan from main
```

`max_parallel` 表示同时活跃的初始搜索链数量。若不启用污染恢复，
`max_candidates` 可以直接等于初始 loop 数量。

### 10.2 搜索决策归属

| 决策 | 目标归属 |
|---|---|
| 初始启动多少条搜索链 | main，run 启动时一次决定 |
| 初始任务/seed | main，启动时一次下发 |
| 下一次技术干预 | 当前 subagent + AtomicPlan Agent |
| overlap/reservation/admission | runtime |
| continue/pivot/rebase | 当前 subagent |
| completion artifact 是否有效 | verifier/runtime/main 验收 |
| 是否刷新 global best | runtime 的 metric 比较 |
| 是否 resume | main 根据全局 stop/time policy |
| 是否创建替代 Agent | 仅显式污染恢复策略 |
| 最终 select/promote | main |

### 10.3 不新增中央 Meta-Agent

不能把原 main conductor 换成另一个 scheduler/meta-agent。多个 subagent 通过共享的
committed Search State、AtomicPlan admission 和 verifier evidence 间接协调，不通过
中央模型逐轮分配方向。

---

## 11. Runtime 与 Host 改动范围

### 11.1 Runtime 复用

继续使用：

- `search_freeze_spec` / `search_create`；
- 初始 `search_plan_next` / `search_start_batch`；
- candidate workspace；
- `search_get_agent_context`；
- `search_run_verifier`；
- `search_continue_agent_session`；
- best-so-far 更新；
- `search_select` / `search_report` / `search_promote`。

AtomicPlan/Search State 接入后，`search_get_agent_context` 增加 committed state，
`search_run_verifier` 同时完成 EvidenceCommit。无需新增 runtime-owned wait loop 或
Agent supervisor。

### 11.2 Codex Host 改动

主要是修改 `.codex/skills/search/SKILL.md`：

- initial fill 后禁止正常 refill；
- completion 后先 verifier/best update；
- 未达到全局 stop 就调用 `search_continue_agent_session`；
- `followup_task` 只 resume 同一 task，不携带 main 生成的新方向；
- `wait_agent`/`list_agents`/`interrupt_agent` 继续管理 host lifecycle。

Candidate agent asset 增加 AtomicPlan/Search State 循环，现有 workspace、verifier、
handoff 和 lease 规则继续保留。

### 11.3 Pi Host 改动

Pi main skill 同样删除 new/refill/replan，只对相同 candidate 调用
`pi_search_pool_continue`。

若只要求逻辑搜索链连续，当前 state-level redispatch 可以复用；若要求严格的原生
same-agent resume，则需要改变 Pi `--no-session` worker 模式或提供持久 RPC session。

### 11.4 不应新增

- runtime-owned worker wait loop；
- runtime heartbeat/liveness；
- peer-to-peer Agent channel；
- main 维护的 per-round search strategy state；
- 为每个 AtomicPlan 创建新 candidate 的强制流程；
- 独立的 LoopLane 生命周期数据库。

---

## 12. 报告与 Timeline

现有 candidate/session/iteration timeline 可以直接表达该流程：

- candidate 行表示一条逻辑搜索链；
- 同一 Codex native session 的多个 resume segment 连续显示；
- Pi state-level resume 显示为同一 candidate 下的多个 session segment；
- verifier 点位表示 subagent 内部迭代和 completion 验收；
- global best 更新在时间线上标记；
- AtomicPlan/admission/EvidenceCommit 数据持久化后再加入对应 candidate 行；
- 长时间横向滚动，多 candidate 纵向滚动；
- 不从 transcript 猜测缺失的 native session 数据。

需要区分：

```text
candidate = logical autonomous loop
agent session = one native execution segment
iteration = one verifier-backed artifact state
AtomicPlan = one proposed intervention inside the loop
```

---

## 13. 实施顺序

### S1：Main Policy 去 Conductor

- initial candidates 仍按现有方式启动；
- completion handler 固定为 validate -> update best -> global stop check -> resume same；
- 删除 normal new/refill/replan；
- 污染恢复先保持 disabled。

### S2：Codex Same-Agent Resume

- 使用现有 continuation API 和 `followup_task(existing task)`；
- neutral resume prompt；
- 保留 candidate/workspace/session identity；
- main 不下发新 hypothesis。

### S3：AtomicPlan 进入 Subagent Loop

- AtomicPlan Agent 从 subagent 内调用；
- Search State 加入 agent context；
- admission reject/rebase 在 subagent 内处理；
- verifier 驱动 EvidenceCommit。

### S4：Parallel Completion

- main 使用 wait-any 处理任意完成者；
- 每个 completion 独立验收并 resume 原 subagent；
- 不等待 batch barrier；
- best-so-far 对所有 candidates 原子可见。

### S5：Pi Continuation 选择

- 明确产品只需要 logical same-candidate resume，还是 native same-session resume；
- 前者复用 `pi_search_pool_continue`；
- 后者实现 persistent Pi session。

### S6：污染恢复

- 定义客观污染信号；
- 优先恢复 same candidate；
- 最后才允许 fork 新 candidate/Agent；
- 报告必须保留 replacement lineage。

---

## 14. 完成条件

- 一个 subagent 在同一 candidate workspace 内完成多轮 AtomicPlan/verifier；
- main 只在 run 开始时创建 initial candidates；
- normal completion 不再调用新的 `search_plan_next`/`search_start_batch`；
- completion 后 main 验收 artifact，并确认 runtime best-so-far 是否更新；
- 全局任务未完成且时间允许时，main resume 相同 subagent/candidate；
- resume prompt 不包含 main 生成的新技术方向；
- 低分或暂时无提升不会触发 Agent replacement；
- 多个 subagent 可以独立 completion/resume，不存在 batch barrier；
- AtomicPlan admission/EvidenceCommit 保持 runtime 事务化；
- Codex 明确保持 native same-agent continuation；
- Pi 明确标注 logical resume 与 native resume 的差异；
- final drain 后仍由 main select、独立 verify 和 promote；完成 record/audit 并
  将 Goal Plus 置为终态后，再由 main 一次性生成最终 report；
- HTML 能区分 logical candidate loop、native session segment、verifier iteration 和
  AtomicPlan。

---

[参考：搜索图式诱导与事务化协调](search_space.md) |
[现有实施计划](plan/README.md)
