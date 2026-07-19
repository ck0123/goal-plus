# 分阶段实施路线

本路线只描述实现依赖。每一阶段都应保持旧 spec/run 可读，并通过 feature mode
控制新行为，避免一次性替换现有 candidate/iteration runtime。

## 依赖关系

```text
P1 AtomicPlan generation contract
  -> P2 immutable SearchEvent
  -> P3 versioned SearchState
  -> P4 serial admission
       ├─> P5 schema revision
       └─> P6 transactional core
              -> P7 Pi/Codex integration

P8 latent/cross-task/other hosts: deferred
```

P5 不是 P6/P7 的前置条件。冻结 schema 可以先支持事务和 host 集成。

## P1：Typed Intervention 与 AtomicPlan 生成合同

**状态：外部同事实现。默认模式：shadow。**

### 范围

1. 定义 `TypedInterventionIR`、target、context、hypothesis、expected observation
   和字段 provenance。
2. 保留原始 proposal/hypothesis 字段，旧调用等价于 `legacy_unstructured`。
3. AtomicPlan 生成 Agent 输出稳定的 plan envelope，不直接分配 candidate 或修改
   SearchState。
4. Runtime 做 deterministic validation，并保存 plan id、normalizer version 和
   unknown 字段。
5. Pi/Codex 使用同一字段语义。

### 接口边界

同事负责 plan 生成、normalization、prompt/asset 和 typed envelope。后续 runtime
工作只消费已经冻结的 plan contract，不另建第二套 AtomicPlan Agent。

## P2：不可变 SearchEvent 账本

**默认模式：shadow。**

### 实现步骤

1. 增加 `SearchEvent` 和 `verifier_invocation_id`。
2. 事件绑定 run、candidate、session、iteration、plan、Git、artifact、verifier 和
   outcome refs；plan/admission 引用允许 legacy 空值。
3. 使用一事件一文件的不可变存储，JSONL 仅作为导出视图。
4. 为 verifier retry 定义稳定 idempotency key。
5. 在 `run_verifier` 锁内提交段写入 event，并把 `search_event_id` 回写兼容
   iteration record。
6. 区分 verified outcome、execution failure、infrastructure failure 和未执行取消。
7. 增加 `search_list_events`、monitor 计数、最近 failure 和离线 legacy importer。

### 完成条件

- 同一 invocation 重试返回同一 event；
- event 引用 exact Git/artifact/verifier evidence；
- candidate 回退、派生、redispatch 和 runtime restart 不删除 event；
- 旧 run 不要求迁移即可读取；
- event 不改变现有 selection/planning 行为。

## P3：静态 Schema 与版本化 SearchState

**默认模式：shadow。**

### 实现步骤

1. 增加 bootstrap/frozen `SearchSchemaVersion`。
2. 增加多视图 `SearchFootprint`：artifact、configuration、mechanism、context、
   epistemic、behavior 和 unknown。
3. 先实现 Git/diff/config/environment/verifier/metric 的 deterministic projector。
4. Semantic projector 只产生带 provenance/confidence 的多标签结果。
5. 增加不可变 `SearchStateCommit`、parent chain 和原子 `HEAD.json`。
6. 构造 completed coverage、claim status、incumbent refs 和 budget summary。
7. 提供 audit、planning、worker-local 和 monitor read models。
8. `search_get_agent_context` 嵌入有界 worker-local view。

### 完成条件

- state/schema/event refs 全部可追溯；
- HEAD 只指向完整 committed state；
- read models 可以从不可变对象重建；
- Pi/Codex 读取同一状态结构；
- semantic 字段缺失时明确保留 `unknown`。

## P4：串行 AtomicPlan Admission

**默认模式：advisory；确定性规则可 limited enforce。**

### 实现步骤

1. `SearchPlan` 继续负责轮次容量，每个 proposal 单独编译为 AtomicPlan。
2. 增加 `search_prepare_atomic_plan`，只生成草稿，不修改官方状态。
3. 增加 `search_admit_atomic_plan`，在 `run.lock` 下检查 state version、budget、
   parent、edit surface 和 completed coverage。
4. 返回 accepted、rejected 或 rebase_required，并保存 reason codes 和 evidence refs。
5. Accepted admission 幂等分配 candidate id；rejected 不扣预算。
6. `search_start_batch` 保持兼容：旧模式保持现状，新模式只 materialize accepted
   plan。
7. Semantic overlap 只提示；确定性 exact duplicate 才允许硬规则。

### 完成条件

- admission retry 不重复分配 candidate 或预算；
- stale state 返回 rebase_required，不自动重写 proposal；
- rejection 可解释并包含冲突 refs；
- legacy start_batch 行为保持兼容。

## P5：Schema Revision

**默认模式：advisory。**

### 实现步骤

1. 定义 revision proposal、trigger evidence、affected refs 和 complexity cost。
2. 支持 extend、split、merge 和 policy change。
3. Loop agent 只能提出 suggestion；runtime 根据已验证事实和确定性规则决定是否
   apply。
4. Apply 创建新 schema version，不修改原 SearchEvent。
5. Projection cache 按 schema version 重建，并保留 lineage。
6. Revision 中断后可恢复，旧 schema/state 继续可读。

## P6：事务核心与 Reservation

**真实 host 并行保持关闭。**

### 实现步骤

1. Admission commit 原子完成 version check、budget deduction、candidate allocation
   和 reservation creation。
2. Evidence commit 原子追加 event、释放 reservation、更新 coverage 和推进 state。
3. 先写 immutable objects/state，最后 swap HEAD。
4. 增加 crash point、重复请求、乱序 evidence、restart 和 orphan recovery 测试。
5. 悬挂 reservation 只能在 host supervisor 提供终态证据后，由恢复/finalize
   操作显式关闭。
6. Monitor 显示 state/schema version、reservation 和 recovery diagnostics，不推断
   worker liveness。

### 完成条件

- 无 candidate/event id collision 或 budget over-allocation；
- state 形成唯一 parent chain；
- 重复 admission/evidence 幂等；
- crash 后没有 reachable 半状态；
- host lifecycle 字段不进入 SearchState。

## P7：Pi/Codex Parallel Loop 集成

**默认模式：opt-in。**

### Pi

- Main 一次性启动固定数量的 initial candidates；
- 每条 Pi candidate 自行完成多轮
  state -> AtomicPlan -> admission -> verifier/evidence；
- 每次 completion 后 main 只验收、更新 best，并在全局任务未完成且有时间时
  continue 相同 candidate；
- Pi continuation 通过新进程恢复相同 native session、`agent_session_id`、candidate
  和 workspace，并使用增量 entries cursor；
- 当前不要求同 PID 长驻；只有进程身份本身成为产品合同后才实现 persistent
  supervisor；
- Pool 不补槽、不生成新方向或 AtomicPlan。

### Codex

- Main 一次性 `spawn_agent` 启动固定数量的 initial candidates；
- 每条 Codex subagent 自己生成和提交后续 AtomicPlan；
- completion 后 main 验收、更新 best，并通过
  `search_continue_agent_session` + `followup_task` resume 相同 native subagent；
- `followup_task` 只传递 neutral resume，不携带 main 生成的新方向；
- `wait_agent`、`list_agents` 和 `interrupt_agent` 处理 host lifecycle；
- hooks 不承担 reservation lease 或 SearchState 更新。

### 完成条件

- Pi/Codex 使用同一 admission/evidence/state contract；
- 中断、redispatch 和恢复后 reservation/event 可解释；
- Main 启动 initial candidates 后不再执行新的 plan/start/refill；
- 未达到全局 stop 时，completion 只 resume 相同 subagent/candidate；
- Host pool 状态继续留在 host-local 目录。

## P8：延后范围

- learned embedding/latent view；
- 跨 run/domain schema 迁移；
- schema/normalizer 训练；
- controller 自修改；
- OpenCode/Claude strict parity；
- distributed/multi-machine transaction store。

## 每阶段公共交付物

1. strict models 与兼容默认值；
2. immutable storage、replay 和 recovery；
3. runtime lifecycle 集成；
4. JSON-friendly API 与 MCP schema；
5. monitor、统计、`report.md` 和 `report.html`；
6. Pi/Codex 所需 asset contract；
7. focused tests、默认测试和 `git diff --check`。

---

[上一页：当前差距与设计原则](00-current-gap-and-principles.md) |
[下一页：Runtime、数据与 API 设计](03-runtime-data-api-design.md)
