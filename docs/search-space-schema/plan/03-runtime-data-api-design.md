# Runtime、数据与 API 设计

本页定义推荐的数据与接口落点。它是实施方向，不要求第一阶段一次性暴露全部 API。原则是尽量复用现有 lifecycle，同时把 round planning、plan admission、physical execution 和 evidence commit 分开。

## 生命周期映射

当前：

```text
SearchPlan
  -> search_start_batch(proposals)
  -> candidate materialization
  -> search_start_agent_session
  -> host worker
  -> search_run_verifier
  -> IterationRecord / results.tsv / best run state
```

目标：

```text
SearchPlan                     # 一轮容量/策略决定，继续保留
  -> private CandidateProposal
  -> prepare AtomicPlan        # 纯编译/审查，无官方状态写入
  -> admit AtomicPlan          # 原子版本/预算/冲突/candidate/reservation
  -> materialize admission
  -> search_start_agent_session
  -> host worker
  -> search_run_verifier
  -> extract realized facts
  -> VerifiedEvidenceCommit    # event + coverage + reservation + state version
```

## 一等数据对象

### `TypedInterventionIR`

```text
base:
  candidate_id
  artifact_revision
  state_version

targets[]:
  entity
  operation
  before
  after
  provenance
  confidence

context:
  task/case
  environment/hardware/compiler/model
  resource/budget regime
  additional typed dimensions

hypothesis:
  bottleneck
  mechanism
  assumptions[]

expected_observations[]:
  metric/or signature
  expected direction/range
  support condition
  contradiction condition
```

Target 和 context 允许多标签及 `unknown`。值可以由 Agent 声明，也可以被 runtime 观测或系统推断；每个字段必须保留 provenance，不能在归一化后丢掉原始值。

### `SearchEvent`

不可变 canonical 事实记录，建议字段：

```text
event_id
event_kind
run_id / candidate_id / agent_session_id / iteration
atomic_plan_id / admission_id
base_artifact_revision / resulting_artifact_revision
declared_schema_version / realized_schema_version
declared_footprint / realized_footprint
alignment: aligned | partially_drifted | materially_drifted | unclassifiable
artifact_delta:
  git_head / parent_git_head / artifact_hash
  changed_files / changed_symbols / config_delta
execution_context
verifier_identity / verifier_hashes
outcome:
  validity/process/pass
  aggregate_score
  metrics
  failure_class
trace_refs / log_refs / diagnostics_refs
hypothesis_assessment
field_provenance
created_at
idempotency_key
```

`SearchEvent` 不嵌入大日志或完整 diff，只保存稳定 hash/ref 和小型事实。原始 artifact 仍由 Git/workspace/verifier diagnostics 保存。

### `SearchSchemaVersion`

```text
schema_version
parent_schema_version
meta_schema_version
ontology_nodes / ontology_relations
enabled_views
projection_rules
equivalence_or_overlap_policy
scenario_prior_ref
normalizer_version
embedding_version        # P8 前应为空
revision_type: bootstrap | extend | split | merge | policy_change
revision_evidence_refs
lineage
created_at
```

Schema 是解释层，允许被替代；旧版本永远可读。

### `SearchFootprint`

每个 view 独立保存，不压成一个单标量：

```text
artifact[]
configuration[]
mechanism[]
context[]
epistemic[]
behavior[]
unknown[]
projection_version
source_fields
confidence
```

计划执行前 behavior 通常未知；执行后 artifact/behavior 主要由 observed facts 更新。

### `AtomicPlan`

```text
atomic_plan_id
run_id
round_plan_id              # 现有 SearchPlan.plan_id
base_state_version
schema_version
typed_intervention_ir
declared_footprint
relation:
  new_axis | refinement | replication | interaction_test |
  alternative_implementation | representation_change
relation_evidence_refs
budget_request
parent/base candidate refs
normalization_provenance
created_at
```

`AtomicPlan` 是一个干预事务，不等于一轮 `SearchPlan`，也不等于 host session。

### `AtomicPlanAdmission`

```text
admission_id
atomic_plan_id
expected_state_version
observed_state_version
status: accepted | rejected | rebase_required
deterministic_checks
overlap_vector
conflict_refs
marginal_contribution
reason_codes / explanation
candidate_id             # accepted 时分配
reservation_id           # P4 起存在；P6/P7 才参与并发冲突
new_state_version        # accepted 时存在
idempotency_key
created_at
```

rejected plan 不扣预算、不分配 candidate；`rebase_required` 不自动重写 proposal。

### `VerifiedEvidenceCommit`

这是 runtime 在 verifier 结果和实际工件事实通过校验后形成的不可变事务结果，不是 worker 自述：

```text
evidence_commit_id
verifier_invocation_id
atomic_plan_id / admission_id / candidate_id
event_id
observed_state_version
declared_schema_version / realized_schema_version
realized_footprint_ref
alignment
reservation_transition
coverage_delta_ref
claim_updates
incumbent_update
schema_revision_proposal_refs
new_state_version
idempotency_key
created_at
```

Worker 只触发 `search_run_verifier`；runtime 从 actual diff、environment、verifier report 和 artifact hash 构造 `SearchEvent` 与本记录。这样可以保留论文中的 evidence commit 边界，而不开放一个允许 worker 直接写“verified”事实的接口。

### `Reservation`

```text
reservation_id
admission_id / atomic_plan_id / candidate_id
declared_footprint
status: active | released | cancelled | recovery_required
created_state_version
released_state_version
release_reason
```

Reservation 只表示官方搜索空间占用，不保存 PID、host job status、heartbeat、deadline 或 live worker 状态。

### `SearchStateCommit`

```text
state_version
parent_state_version
schema_version
incumbent/frontier refs
new_event_ids
event_index_ref
coverage_ref
claim_state_ref
active_reservations
budget_snapshot
admission/evidence commit refs
policy_version
created_at
```

完整 state 可以由 checkpoint + delta 组合；首版数据量较小时也可以每版保存紧凑 snapshot。重点是版本不可变、父链明确、`HEAD` 原子指向唯一已提交版本。

## 推荐持久化布局

```text
.gp/runs/<run_id>/
  run.json
  plans/                         # 现有 SearchPlan
  candidates/
  workspace/
  search-space/
    HEAD.json                    # 原子替换，只指向 committed state
    objects/
      events/<event_id>.json
      atomic-plans/<atomic_plan_id>.json
      admissions/<admission_id>.json
      evidence-commits/<evidence_commit_id>.json
      reservations/<reservation_id>.json
      schema-revisions/<revision_id>.json
    schemas/schema-000001.json
    states/state-000001.json
    projections/<schema_version>/<event_id>.json
    indexes/                     # 可重建派生索引/coverage
    diagnostics/                 # orphan/recovery/validation 小报告
```

`objects`、`schemas` 和 `states` 中的文件一旦由 `HEAD` 引用就不可修改。`indexes` 和 compact read model 可以重建，不作为永久事实。

## 文件后端事务协议

### Admission commit

在 `run.lock` 下：

1. 读取 `HEAD` 和当前 `RunRecord`；
2. 根据 idempotency key 查找是否已提交，若存在直接返回原结果；
3. 比较 `expected_state_version`；不一致则记录/返回 `rebase_required`；
4. 做 budget、parent、edit surface、schema 和 overlap 检查；
5. accepted 时在内存中分配 candidate id、预算和 reservation；
6. 先写所有新的 immutable objects；
7. 写新的 immutable `state-<v+1>.json`；
8. 最后原子替换 `HEAD.json`；
9. 更新兼容 `run.json` 计数/索引。若兼容写失败，可从 `HEAD` 修复，`HEAD` 是新状态的权威提交点。

在 HEAD swap 前 crash，未引用对象是安全 orphan；恢复工具可以删除或报告。HEAD swap 后 crash，新状态已提交，兼容 read model 可重建。

### Evidence commit

Physical verifier 在 lock 外运行。事实提取完成后，在 `run.lock` 下：

1. 检查 run invalidation fence 和 invocation idempotency；
2. 读取最新 state，不要求等于 admission 时版本；
3. 写 immutable `SearchEvent` 和最新 schema 下的 realized projection；
4. 释放 reservation，更新 coverage/claim/incumbent refs；
5. 写新的 state version；
6. 原子 swap HEAD；
7. 更新 `IterationRecord`、`results.tsv` 和兼容 `run.json`。

Evidence 以最新 state 提交，但 event 同时保存 admission 时的 declared schema/version，保证计划原意和事后解释都可审计。

### 幂等键

建议：

- prepare：客户端 request id，可重复生成但不写官方状态；
- admission：`run_id + atomic_plan_id + client_request_id`；
- verifier invocation：runtime 分配的 `verifier_invocation_id`；
- evidence：`run_id + invocation_id + artifact_hash + verifier_bundle_hash`。

不能只用 score 或 hypothesis 文本去重。

## Read models

同一事实生成不同有界视图：

| View | 内容 | 使用者 |
|---|---|---|
| audit | 完整 state/schema/event refs、版本父链 | 调试、恢复与重放 |
| planning | incumbent、coverage、active reservations、open questions、budget | loop agent / AtomicPlan Agent |
| worker-local | admitted plan、相关历史事件、局部 coverage、expected observation | loop agent |
| monitor | 一屏 state/schema version、coverage、reservations、recent commits/warnings | 操作者 |
| report | event/state/admission/reservation 汇总、可用指标和缺失项 | Markdown/HTML 报告 |

`get_agent_context` 应嵌入 worker-local view；不要要求 worker 先后读取多套互相可能漂移的 memory 文件。

## API 演进

### P1-P3 最小新增 API

| API | 类型 | 说明 |
|---|---|---|
| `search_prepare_atomic_plan` | write draft only / no official state | 将 proposal 编译为 typed IR 和 declared footprint，返回 validation/unknown |
| `search_get_search_state` | read-only | 按 `planning/worker/audit/monitor` profile 读取指定或最新版本 |
| `search_list_events` | read-only | 分页读取 canonical events，不返回大日志 |
| `search_list_schema_versions` | read-only | 查看 schema lineage |

P1 可先把 prepare 作为 runtime 内部 helper；P3 再暴露正式 tool。

### P4-P7 事务 API

| API | 类型 | 说明 |
|---|---|---|
| `search_admit_atomic_plan` | official state write | expected-version 准入，返回 accepted/rejected/rebase |
| `search_get_admission` | read-only | 幂等恢复与解释 |
| `search_explain_overlap` | read-only | 返回各 view 证据和合法 relation 提示，不改变状态 |
| `search_propose_schema_revision` | proposal write | 保存 revision candidate，不直接 apply |
| `search_apply_schema_revision` | runtime write | 基于已验证 revision decision 创建新 schema/state version |
| `search_close_reservation` | recovery/finalize write | 在 host 已提供终态证据后释放/取消悬挂 reservation |

首轮不建议增加 public `search_submit_evidence`：worker 不能自称 evidence verified。继续由 `search_run_verifier` 生成事实并内部执行 VerifiedEvidenceCommit。

### 现有 API 的兼容行为

- `search_plan_next`：保持 round planning，不偷偷变成 AtomicPlan。
- `search_start_batch`：
  - legacy/disabled：保持当前行为；
  - shadow/advisory：内部 prepare/admit 后 materialize，并返回 admission refs；
  - strict：只 materialize accepted plans，或要求显式 atomic plan 输入。
- `search_run_verifier`：保持 worker 入口；新增 plan/invocation/event refs，自动 evidence commit。
- `search_list_history`：保留兼容视图，并增加 state/event refs；不再作为唯一搜索知识来源。
- `search_get_agent_context`：加入 worker-local state，不改变其权威入口地位。
- `search_select/report/promote`：继续基于 verifier-backed immutable artifact；report 增加 event/schema/admission 运行证据。

## Feature mode 与兼容迁移

建议在 `StrategySpec` 中增加版本化 policy，而不是用全局环境变量改变 frozen run 语义：

```text
search_state_policy:
  mode: disabled | shadow | advisory | enforced
  schema_bootstrap: empty | universal | scenario_prior | supplied
  semantic_admission: off | advisory | enforced
  exact_duplicate_policy: allow | warn | reject
  max_state_tokens
  projection_version
  revision_policy
```

- 旧 spec 缺少字段时等同 `disabled`；
- 旧 run 不自动原地迁移；
- 离线 importer 生成 `legacy_inferred` event/state 供查看和迁移，不可伪装成原 run 已声明的 plan；
- 新 run 可逐级选择 shadow/advisory/enforced，策略进入 frozen spec/hash。

## 代码落点

推荐把新逻辑拆出 `runtime.py`，避免其继续膨胀：

| 文件 | 责任 |
|---|---|
| `src/goal_plus/search_events.py` | event 模型辅助、事实提取、幂等身份 |
| `src/goal_plus/search_schema.py` | schema/version/revision/lineage |
| `src/goal_plus/search_projection.py` | deterministic + semantic footprint projection |
| `src/goal_plus/search_state.py` | state commit、HEAD、read model、replay/recovery |
| `src/goal_plus/search_admission.py` | plan validation、overlap、relation、admission decision |
| `src/goal_plus/runtime.py` | 现有 lifecycle 集成和 transaction boundary |
| `src/goal_plus/models.py` | 严格 Pydantic public records |
| `src/goal_plus/tools.py` / `server.py` | JSON-friendly/MCP facade |
| `src/goal_plus/monitor.py` | compact state/schema/reservation observability |

## Pi 与 Codex 接入

### Pi

- 新增 long-lived loop prompt：每轮读取 state，自行调用 AtomicPlan Agent、admit、
  verifier/evidence，再进入下一轮；
- `.pi/skills/goal-plus/SKILL.md`：main 创建 initial candidates；每次 completion
  只验收、更新 best，并在全局 stop 为 false 时 continue 相同 candidate；
- `.pi/extensions/goal-plus.ts`：镜像新工具 schema 和 compact output；
- `pi_pool.py`：管理 candidate process 和 deadline，不持有 SearchState 或搜索策略；
- `pi_search_pool_continue` 通过新进程恢复相同 native session、`agent_session_id`、
  candidate/workspace，并用 `get_entries(since=...)` 增量采集 dispatch 指标；
- persistent same-PID supervisor 不属于当前合同；只有明确要求 OS 进程身份连续时才新增。

### Codex

- `.codex/skills/search/SKILL.md`：main 一次性 spawn initial candidates；每次
  completion 验收并在全局 stop 为 false 时 resume 相同 task，不再重规划或补槽；
- Candidate asset 从 agent context 读取 SearchState，并在同一 child turn 内完成
  多轮 AtomicPlan/admission/verifier；
- `followup_task` 用于 resume 相同 native subagent，但只能发送 neutral continuation，
  不承担方向选择；
- Codex hooks 继续负责 Goal Plus session binding/stop/pre-tool gate，不承担 reservation lease 或 SearchState 更新。

### 暂不支持的 hosts

OpenCode/Claude 的 spec 可以继续 `disabled`；runtime 新记录必须保持 host-neutral 可读，但不要求首轮资产、hook 或真实 smoke parity。

## 测试结构

- `tests/test_models.py`：strict schema、legacy defaults、provenance；
- 新 `tests/test_search_events.py`：fact extraction、idempotency、rollback；
- 新 `tests/test_search_state.py`：HEAD commit、replay、schema versions、read models；
- 新 `tests/test_search_admission.py`：version/budget/overlap/relation/rebase；
- `tests/test_runtime_unit.py`：现有 lifecycle 集成和 backward compatibility；
- `tests/test_server.py`：tool schema 和返回 contract；
- `tests/test_monitor.py`：compact view；
- `tests/test_pi_assets.py` / `tests/test_codex_assets.py`：host prompt/skill contract；
- `tests/st/test_st_pi_rpc.py`：Pi real serial/parallel；
- 新或现有 Codex ST harness：真实 serial SSI 和 parallel TSC。

---

[上一页：分阶段实施路线](02-phased-roadmap.md) | [下一页：交付拆分](05-delivery-and-gates.md)
