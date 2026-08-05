# 共享平面

Goal Plus 是面向长时间 agent 任务的宿主中立运行时。普通任务停留在 Goal
Mode；具有明确可度量目标的优化任务进入 Search Mode。Search Mode 会先冻结评价
合同，再让多个隔离、长期运行的 candidate 并行探索。

Search Mode 的核心是持久化的共享平面。共享平面不共享 agent 的私有推理，也不共享
可写工作区。它只共享冻结合同、精确 Git revision、verifier 支持的 Evidence、异步
生成的客观 View、可选的 verifier-settled 只读资产快照，以及选择和提升结果所需的
持久化状态。

## 总体架构

```text
main agent
  |  发现并冻结 SearchSpec，分配初始 candidate
  |  观察持久化结果，执行全局停止策略，选择并提升结果
  v
+------------------------- 共享运行时平面 -------------------------+
| Goal 记录与冻结 SearchSpec                                      |
| candidate 工作区与 Git revision                                 |
| verifier-backed Global Evidence 与异步客观 View                 |
| 可选的 run-scoped 只读 shared tools                           |
| candidate-local best、全局 best、报告与 promotion               |
+------------------------------------------------------------------+
       ^                         ^                         ^
       |                         |                         |
 candidate c001             candidate c002             candidate c003
 隔离工作区                 隔离工作区                 隔离工作区
 自主长期循环               自主长期循环               自主长期循环

宿主平面：启动、wait-any、continuation、deadline、interrupt、原生日志
```

各组件的职责边界如下：

| 所有者 | 职责 |
|---|---|
| Search runtime | 冻结合同、物化工作区、提交 Git 和 verifier Evidence、回滚、选择、报告与 promotion |
| candidate worker | 选择自己的技术方向、修改自己的工作区、调用 verifier、提交 handoff |
| main agent | triage、spec discovery、初始 candidate 分配、全局停止、最终收尾、确认 verifier 失效 |
| Codex 或 Pi 宿主 | 实际 worker 启动、等待、续跑、deadline、interrupt 和原生 transcript |
| Evidence annotator | 对每个已结算尝试生成一句客观描述 |

Search runtime 不是 worker supervisor。`AgentSessionRecord` 只记录上下文、来源和
宿主 launch payload，不表示进程是否存活。Pi 的 pool 状态因此单独保存在
`.gp/host-pools/pi/`；Codex 生命周期由原生 agent registry 管理。

## SearchSpec 与评价合同

一个 `run_id` 绑定一份不可变的 `SearchSpec`。它至少包含：

- 可度量的目标、`metric_name` 和方向；
- `source_path` 与允许修改的文件范围；
- process verifier 与 promotion verifier；
- 初始并发宽度、工作区后端和 worker 执行策略；
- 可选的用户模型选择 `strategy.models`；
- verifier 所依赖的源码内 artifact。
- 可选的 `shared_dir` 有界工具快照策略（默认关闭）。

当前逻辑由 main agent 在 Spec Discovery 中读取任务说明、公开数据和评分工具，然后
构造或选择 verifier，并调用 `search_freeze_spec`。Runtime 会：

- 使用 Pydantic 校验 SearchSpec 结构；
- hash-pin verifier artifact；
- 在一次性源码副本中预执行 ranking verifier；
- 检查退出码、数值 metric、超时和工作区副作用。

Runtime 不理解具体 benchmark 的语义，也不会自动证明本地 verifier 与官方 Judge
等价。公开评分公式、有效性条件、每例资源限制和聚合方式是否完整进入 verifier，仍由
SearchSpec 的创建者负责。隐藏数据只能作为明确声明的近似项，不能被无提示地替换为
另一套权威指标。

冻结后的合同不能原地修改。确认 verifier 合同有误时，应先使当前 run 失效并停止其
worker，再冻结修正后的 spec，创建 successor run。旧分数不能跨合同复用。

## 一次 Search Run

一次正常的 Search run 按以下顺序执行：

1. main agent 冻结 SearchSpec 和 verifier artifact。
2. 一次初始 `SearchPlan` 分配固定 candidate lane。
3. 每个 candidate 获得独立工作区并开始自主循环。
4. 宿主以 wait-any 方式返回完成事件；较慢 lane 不阻塞其他 lane 发布 Evidence。
5. 只要全局停止条件未满足，宿主续跑同一个 candidate、原生 session 和工作区。
6. 收尾时先让所有 live worker 静止，再选择精确 commit，执行 promotion gate 并生成报告。

`budget.max_parallel` 是 spec 的初始 candidate 和 live-worker 数量。普通
`parallel_loops` run 只做一次初始分配，不按分数创建替代 candidate，也没有后续
规划轮次。用户提供 `strategy.models` 时，runtime 在冻结前通过 host adapter 验证
名称，并在初始 plan 中生成与 lane 一一对应的 `selected_models`：未写数量时轮转，
全部写数量时按数量展开。每个 selected model 随 candidate 和原生 session 的续跑保持
不变，但所有 lane 仍共享同一份 Global Evidence。

## Candidate 循环

每个 candidate iteration 只需要以下协议：

1. 调用 `search_get_agent_context` 读取自己的权威状态和历史。
2. 调用 `search_get_global_evidence` 读取当前 run 的共享视图。
3. 独立选择方向，只修改自己的工作区。
4. 调用 `search_run_verifier`，并用一句 `hypothesis` 描述实际完成的尝试。
5. 从 verifier settlement 返回后的工作区继续。

启用 `shared_dir` 时，candidate 还可在自己的 `.tmp/share-out` 下提取最小工具或模块。
它仍只写自己的工作区；runtime 在通过的 worker process verifier 结算时原子认领 staging，
只快照相对该 candidate/路径新增或修改的顶层工具，并将快照绑定到该 iteration 和 attempt
commit。已接受或内容未变化的条目会被消费；失败、无归属或因限制被拒绝的条目保留给 worker
修正。内容相同的快照复用同一个哈希寻址物理目录。peer 只能通过 Global Evidence 和
run-scoped shared-dir 读取快照。采用代码时必须复制到自己的 `allowed_files` 并重新验证，
最终产物不能直接依赖 run shared-dir。

candidate 不需要在修改前提交 iteration plan。`hypothesis` 是完成尝试后的事实性
自述，与 verifier 结果一起保存；它不是 pending plan，也不形成协调锁。多个
candidate 可以同时读取同一版 Evidence 并并发工作。

main agent 不向 worker 提供后续技术方向。正常路径优先续跑同一个原生 session；
redispatch 只用于恢复，并继续使用同一个 candidate 工作区、Git 历史、verifier 历史
和有界 handoff。

Process iterations retain shared-dir staging diagnostics even when no
snapshot is published: staged entry/file/byte counts, consumed/deduplicated
entry names, plus `shared_tool_publish_status`. Parent fallback verification remains
unattributed and records `skipped_unattributed_verifier` instead of publishing
candidate-authored tools. Staging inspection and snapshot failures remain
advisory and cannot invalidate verifier Evidence. Legacy iterations infer a
published state from durable tools when possible and otherwise use
`legacy_unknown`.

## Global Evidence

`search_get_global_evidence` 将当前 run 中已结算的 worker iteration 投影为窄表：

```json
{
  "candidate_id": "c001",
  "iteration": 3,
  "commit": "<exact-attempt-commit>",
  "score": 13350,
  "disposition": "keep",
  "view": "将调度逻辑改为按依赖深度分组。",
  "shared_tools": [
    {
      "tool_id": "c001-i0003-...",
      "name": "dependency-depth-scheduler",
      "source_commit": "<exact-attempt-commit>",
      "snapshot_hash": "<sha256>",
      "read_only_path": "<run-scoped-path>"
    }
  ]
}
```

verifier 会同步发布 `candidate_id`、`iteration`、`commit`、`score` 和
`disposition`。`disposition` 取值为：

- `keep`：尝试有效，并且严格改善该 candidate 的历史最佳；
- `discard`：尝试有效，但分数相同或更差；
- `failure`：尝试未产生可用于排名的 verifier Evidence。

Global Evidence 只包含 worker 的 process-verifier 尝试。parent fallback verification
与 promotion verification 不会成为 peer Evidence。视图也不会暴露 peer transcript、
私有推理、annotation 内部任务状态或 peer 工作区路径。

`shared_tools` 是可选的 candidate-authored 研究工具，不等同于 verifier 对工具本身的独立
正确性证明。runtime 先运行 verifier；只有有归属且通过的尝试才递归扫描 staging。扫描受
`max_tools_per_iteration`、`max_files_per_iteration`、
`max_path_entries_per_iteration`、`max_depth` 和 `max_bytes_per_iteration` 的硬上限约束，
遍历一旦超过上限立即停止。runtime 只做路径安全、内容哈希和不可变快照，不执行、安装或
自动整合工具。失败的 process verifier 不发布工具；工具快照失败也不会使已经有效的
verifier Evidence 失效。

### Shared-dir 权限边界（Windows/Linux）

shared-dir 的权威写入者是 runtime。快照先复制到私有临时目录，再用同文件系统内的原子
rename 发布；索引也用临时文件加原子 replace 更新。staging 的整目录 rename、快照发布和
索引更新都在 run transaction 锁内，因此 peer 不会从正式 shared-dir 看到半成品或待验证
输入。索引只复用解析后仍位于 run 的 `shared/tools` 内的快照路径。

runtime 会在 POSIX 和 Windows 上尽力设置只读 mode/attribute，但这只是防止误写，不是同一
OS 用户下的安全隔离：POSIX owner 可以重新 `chmod`，Windows 的只读属性也不是目录 ACL。
因此候选“不得写 shared-dir”是 runtime/host 权限契约；需要抵抗恶意 worker 时，host 必须
用 sandbox、容器、不同 OS 身份或 ACL 让 shared-dir 对 worker 真正只读。runtime 不宣称用
跨平台 `chmod` 提供强安全边界；`snapshot_hash` 是内容身份和完整性证据，不替代 host 隔离。

## 客观 View

`view` 是绑定到准确 Evidence identity 的异步、不可变 annotation。后台 drainer 按
SearchSpec 的 `worker_host` 选择 Codex 或 Pi 的一次性无工具执行模式；不会为了
annotation 跨用另一个 host。Annotator 可读取：

- candidate 的一句 `hypothesis`；
- 从本轮 settled base 到 attempt commit 的完整 diff；
- 精确 attempt commit；
- verifier 结果与相关 metrics。

View 只用一句中文客观描述实际做了什么，不评价好坏、不推断动机、不排名，也不推荐下一
步。事实来源是 actual diff，而不是 candidate 的自述。

`view=null` 只表示 annotation 尚未发布，Evidence 本身已经有效。candidate 可以先按
自己的方向继续，不应等待、sleep 或轮询 View。

verifier settlement 和 Evidence 读取都可以触发 run-scoped annotator。一个 drainer
串行处理 backlog，但 verifier、selection 和 promotion 都不等待它。重试状态、解析后的
模型/provider、deadline、usage 和 View 都持久化保存。run 关闭后，发布 fence 会拒绝
迟到的 annotation 修改。

## Git 与 Candidate-Local Best

Evidence 中的 commit 表示 verifier 实际读取的完整 Git tree，不是相对于初始源码的
patch。Runtime 分别记录：

- `attempt_base_git_head`：尝试前的 settled HEAD；
- `git_head`：verifier 实际验证的 attempt commit；
- `attempt_changed_files`：用于 annotation 的 `base..attempt` 净变化；
- `changed_files`：相对于原始 source 的最终 artifact 变化，用于策略检查和 promotion。

Runtime 会在验证前提交所有 candidate-controlled 修改，并要求 artifact worktree
干净。candidate 可以包含多个手工 commit；annotation 使用完整
`settled-base..attempt` 范围，而不是只查看最后一个 commit。

每次 process-verifier 尝试都会永久保留。只有严格改善才更新 candidate-local best。
`discard` 或 `failure` 时，Runtime 会先把代码恢复到此前最佳版本，再追加不可变的
`results.tsv` ledger：

```text
keep:     settled -> attempt -> ledger
discard:  settled -> attempt -> restore-best -> ledger
```

attempt、恢复和 ledger commit 都保持可达。因此下一轮始终从 candidate-local best
规划，而 Global Evidence 仍保留好、差和失败的全部尝试。一个 candidate 的回滚不会
改变 peer 工作区，也不会修改 peer 的判断。

`git_worktree` 后端下，各 candidate 共享一个 Git common directory。只有在代码级
证据确有必要时，candidate 才从自己的工作区渐进式查看 peer Evidence commit：

```bash
git show <commit>:<allowed-file>
git diff HEAD <commit> -- <allowed-file>
```

candidate 不应 checkout、reset 或修改 peer revision。最终 promotion artifact 虽然是
相对于 source 生成的 patch，但这不会改变 Evidence commit 代表完整代码树的语义。

## 选择、Promotion 与失效

run-wide best 从有效 iteration record 中计算，与各 candidate-local best 分开。
`search_select` 将结果绑定到一个精确、通过验证的 worker Evidence commit。只有旧状态
或当前产物没有对应 durable Evidence 时，parent 才补做 process verifier。

promotion 是独立的验收 gate。Runtime 会检出选中的不可变 revision，在
`GOAL_PLUS_VERIFIER_PHASE=promotion` 下重跑配置的 promotion verifier，并把结果绑定
到 Git head 和 artifact hash。只有 promotion 成功，才生成可被 Git 应用的 patch；
source workspace 不会被静默修改。

worker 报告的 verifier concern 只是建议。main agent 确认缺陷后，Runtime 先原子
invalidate 并 fence 当前 run，宿主再停止所有 worker。修复后的合同必须进入 successor
run。可以继承有界研究上下文，但不能继承旧分数或把旧 Evidence 当作新合同下的结果。

## 持久化目录

```text
.gp/
  goal-plus/<goal_plus_id>/
  specs/<frozen_spec_id>/
  runs/<run_id>/
    run.json
    plans/<initial-plan-id>.json
    candidates/<candidate_id>/
      candidate.json
      evidence-annotations/iteration-<n>.json
    agent_sessions/<agent_session_id>.json
    workspace/<candidate_id>/
      .tmp/share-out/              # candidate-owned optional export staging
    shared/
      index.json                   # runtime-owned tool discovery index
      tools/<candidate>/<iteration>/<tool-hash>/
    report.md
    report.html
    promotion/<candidate_id>.patch
  host-pools/pi/
```

`candidate.json` 中的 iteration record 是 Evidence 的事实来源。Annotation task 保存
可选 View 及其执行状态。Global Evidence 在读取时即时投影，不是第二份可写共享账本。

## 核心不变量

- 并行工作开始前，先冻结 verifier 与编辑策略。
- 隔离可写 candidate 工作区，只共享持久化事实和 Git object。
- shared tools 由 candidate-local staging 产生、runtime 单写发布、peer 只读消费。
- 最终候选必须脱离 run shared-dir 独立验证和 promotion。
- 只有 verifier-backed 严格改善才能成为 candidate-local best。
- 回滚、选择和 promotion 后，所有精确 attempt 仍可审计。
- View 可以延迟，但不能阻塞优化。
- worker 生命周期和原生 transcript 不进入 Search runtime 状态。
- 一份有效合同对应一个 run，successor run 不复用旧分数。
- 全局停止依据持久化状态，而不是单个 worker 的意见或 lease 到期。

当前框架没有逐 iteration Global Plan、AtomicPlan admission、Search Space schema、
共享 chain of thought、peer-history feed，也没有 runtime-owned agent scheduler。
benchmark 专用任务、evaluator、campaign 和对比结果属于 `bench-goal-plus`；
本仓只维护可复用运行时。

具体工具见 [API](api.md)。宿主机制见
[Agent Host Adapters](agent-host-adapters.md)、[Codex](codex.md) 和 [Pi](pi.md)。
持久化状态排查见 [Debugging Runtime State](debugging-runtime.md)。
