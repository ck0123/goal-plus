# SpaceAgent Runtime

本文定义 Goal Plus 当前的 run 级 SpaceAgent 运行时。SpaceAgent 是全局的语义准入器和
搜索经验维护者，不是 planner、conductor 或方向推荐器。

核心对象只有三个：

```text
Search Evidence   append-only 的 verifier 事实账本
Search Schema     周期性追加的全局空间描述快照
Search State      当前审核使用的 read model 和并发协调状态
```

不引入 `PlanProjection`，也不在每一轮额外调用一个 Agent 核对 declared plan 和 diff。
实际执行事实先确定性写入 Search Evidence；SpaceAgent 在后续准入时直接看到尚未归并的
Evidence，并只在周期 checkpoint 重写一次全局 Schema 描述。

## 架构总览

```text
 Candidate Lane 1       Candidate Lane 2                 Candidate Lane N
 planner + workspace    planner + workspace              planner + workspace
        │                       │                                │
        └───────────────────────┴─────────────── ... ────────────┘
                                │ three-field PlanCard
                                ▼
                    +---------------------------+
                    | Global Admission View     |
                    | Schema head               |
                    | + compact Evidence tail   |
                    | + active reservations     |
                    +-------------+-------------+
                                  │
                                  ▼
                    SpaceAgent admission review
                    accept / reject only
                    no direction, no Schema edit
                                  │
                         admission revision CAS
                         │                    │
                 reject  │                    │ accept
                         ▼                    ▼
               duplicate_of + replan    active reservation
                                              │
                                              ▼
                                      candidate executes
                                              │
                                              ▼
                                  existing Goal Plus verifier
                                   │                       │
                                   ▼                       ▼
                        Solution Artifact       immutable SearchEvidenceEvent
                        incumbent / rollback               │
                        existing lifecycle                 │ threshold reached
                                                           ▼
                                               global consolidation claim
                                                           │
                                                           ▼
                                          SpaceAgent Schema consolidation
                                          evidence description only
                                          no direction suggestion
                                                           │
                                               frozen-watermark commit
                                                           ▼
                                          append-only SearchSchemaSnapshot
                                                           │
                                                           └──> next global views
```

图中的 lane 只共享 Space 的全局视野，不共享各自的 planner 或 workspace。SpaceAgent 的
对 candidate 可见的输出只有准入判定。到 checkpoint 时，同一个全局 SpaceAgent 通过独立的
`consolidate` 操作生成完整 Schema 更新；这个结果只进入 runtime，不返回给 candidate。方向
选择始终留在各 lane。

## 1. 责任边界

现有 Goal Plus runtime 继续负责：

- candidate workspace 和 Git；
- verifier 执行、指标、有效性和 failure class；
- incumbent、solution artifact 的保留、撤销或回退；
- candidate lane 的生命周期。

Space 子系统只负责：

- 把已有 verifier 结果发布成不可变 Search Evidence；
- 维护全局 active reservations，防止并发 candidate 做同一件事；
- 对新 PlanCard 做 accept/reject 判别；
- 周期性把 Evidence 归并成新的 Search Schema 快照。

Space 子系统可以在 Evidence 中引用 artifact revision、diff hash、changed files 和 bounded
diff，但不拥有 artifact，也不执行 Git 回退。Solution artifact 回退不会删除已经发生的
verifier 事实。

## 2. Candidate 契约

每个 candidate 仍独立规划，只在实际修改或运行 evaluator 前提交最小 PlanCard：

```text
intervention              这一轮实际准备做什么
scope                     目标、参数、上下文和边界
expected_new_information  这一轮能新增回答什么问题
```

Candidate 不需要构造 region、point、feature taxonomy 或历史引用。不同 lane 可以使用不同
表述；语义归类由拥有全局视野的 SpaceAgent 完成，而不是逐字匹配。

Accept 只返回可执行的 plan id：

```json
{"plan_id": "ip-0007", "decision": "accept"}
```

Reject 必须指出具体重复对象：

```json
{
  "plan_id": "ip-0008",
  "decision": "reject",
  "duplicate_of": ["ip-0003"],
  "duplicate_plans": [
    {
      "plan_id": "ip-0003",
      "coverage_status": "completed_coverage",
      "plan_card": {
        "intervention": "...",
        "scope": "...",
        "expected_new_information": "..."
      }
    }
  ]
}
```

`coverage_status` 取 `completed_coverage` 或 `active_reservation`。Reviewer 的 rationale、
overlap、region/point key、完整 Evidence 和 Schema 都不暴露给 candidate。Reject 不附带替代
方案，也不暗示下一步方向；candidate 必须自己重新规划并提交新的 PlanCard。

一个 candidate 同时只能有一个 accepted/verifying plan。Verifier 调用必须携带该次准入
返回的 `intervention_plan_id`，因此 declared plan、实际执行和 verifier 结果通过稳定 id
关联，不依赖对话记忆。

## 3. Search Evidence

每次完成 verifier 后，runtime 从现有确定性结果构造 `SearchEvidenceEvent`：

- `event_id`、`event_index`、run/candidate/session/plan id；
- candidate 提交的 declared proposal；
- base/result Git head、artifact hash、delta hash；
- changed files、delta files、有限 symbol 摘要；
- bounded diff stat 和 patch；
- metric、score before/after/delta；
- validity、process result、failure class；
- `improved / neutral / regressed / invalid / infrastructure_failure` outcome；
- `coverage_eligible`。

事件写在：

```text
.gp/runs/<run_id>/search-space/events/se-000001.json
.gp/runs/<run_id>/search-space/events/se-000002.json
...
```

每个文件以 create-only 方式原子发布，发布后设为只读。事件包含自己的 content hash，以及
前一事件的 id/hash，形成连续的 append-only hash chain。加载时会校验编号、内容 hash、
父链和一个 plan 只能对应一个 Evidence event；已有事件不会被重写。

Coverage eligibility 只由 verifier 事实决定：

| Outcome | 进入 coverage | 仍写 Evidence |
|---|---:|---:|
| `improved` | 是 | 是 |
| `neutral` | 是 | 是 |
| `regressed` | 是 | 是 |
| `invalid` | 否 | 是 |
| `infrastructure_failure` | 否 | 是 |

因此负收益但有效的具体尝试仍然是已获得的经验；invalid 或基础设施失败保留审计事实，但不
声称对应语义点已经被有效探索。

Plan JSON 是可变的执行 read model，会随 `reviewing -> accepted -> verifying -> completed`
原子重写，并保存 `search_event_id`。不可变事实源是 Search Evidence，不是 Plan JSON。

## 4. Search Schema

Search Schema 不是每轮 plan 的投影，而是 SpaceAgent 对全局经验的周期性、完整描述。快照
写在：

```text
.gp/runs/<run_id>/search-space/schemas/schema-000001.json
.gp/runs/<run_id>/search-space/schemas/schema-000002.json
...
```

`schema-000001` 在打开 Search Space 时创建。后续快照包含：

- 完整 `space_schema`；
- 完整 coverage read model；
- `built_through_event_index/id`；
- revision summary 和所依据的 Evidence event ids；
- parent snapshot version/hash 和自己的 content hash。

Schema 快照同样 create-only、只读并形成 hash chain。新的理解通过追加新版本表达，旧版本
不会被覆盖。这样可以同时满足：

```text
Verified facts cannot be rewritten.
Current interpretation may be revised.
Previously acquired facts cannot be forgotten.
```

更新校验会拒绝：

- 引用不存在的 Evidence；
- 把 invalid/infra Evidence 写进 coverage；
- 遗忘旧快照已经包含的 eligible Evidence；
- 遗忘当前 tail 中新的 eligible Evidence；
- 为 coverage event 填写不一致的 plan id 或 outcome。

描述可以合并、拆分、改名或变细，但 coverage 必须始终能追溯到 immutable Evidence。

默认 checkpoint 是累计 20 条尚未归并的 eligible Evidence。为避免连续 invalid/infra 事件
使 tail 无限增长，当未归并事件总数达到 40 条时也会触发一次归并。两个阈值分别是
`schema_consolidation_interval` 和其两倍；测试可以使用更小阈值。阈值在 Evidence 发布后
检查，不再等下一条 candidate plan，也不再要求 admission review 同时产出 Schema。

SpaceAgent 负责语义描述，runtime 负责引用完整性。Consolidation 输出若遗漏 eligible Event，
runtime 会保留旧 coverage cell，并为仍遗漏的 Event 补充以该 Evidence 为锚点的保守 cell；
`revision_evidence_event_ids` 则由 runtime 按 frozen watermark 确定性写入。模型可以修订解释，
但不能因为一次结构化输出遗漏而遗忘已经发生的事实。固定的六个 view key 也由 runtime
归一化：模型提供有效新描述时采用，否则继承父 snapshot，并丢弃额外 view，避免一次格式
漂移使 checkpoint 无意义地失败。

### 4.1 空间如何映射

这里的 space 不是预先枚举的笛卡尔坐标、向量数据库或固定 taxonomy，而是一张
**Evidence-anchored semantic coverage map**。所谓“一个点”，是同一具体上下文中的 material
intervention 和它要回答的 epistemic question；所谓“区域”，是 Schema 对若干证据点的
语义分组。

映射分两层发生：

```text
admission mapping
  candidate PlanCard
    -> 对照 Schema coverage + raw Evidence tail + active reservations
    -> accept，或 reject(duplicate_of=[具体 plan ids])

evidence mapping
  declared PlanCard + realized artifact delta/result
    -> immutable SearchEvidenceEvent
    -> checkpoint 时归入新版 Schema 的 coverage cell
```

一个持久化 coverage cell 当前由这些字段表示：

```text
coverage_id
description
context
evidence_event_ids[]   事实锚点
evidence_plan_ids[]    candidate 可理解的冲突引用
outcomes[]             improved / neutral / regressed
```

因此映射关系的权威边是：

```text
SearchSchemaSnapshot.coverage[*].evidence_event_ids
    -> immutable SearchEvidenceEvent
    -> declared proposal + realized verifier evidence
```

SpaceAgent 使用 artifact、configuration、mechanism、context、epistemic、behavior 六个视角做
语义比较，所以不同 lane 的措辞可以归到同一 cell，而不是按字符串相等判定。实际 diff 和
verifier 结果优先于宽泛的 declared wording。

映射的生命周期如下：

1. PlanCard 刚提交时只是待审核点；accept 后成为 active reservation，还不是已探索事实。
2. Verifier 完成后，它成为 tail 中的 raw Evidence point，立刻参与后续判重。
3. 到 checkpoint 时，SpaceAgent 可以把多个等价 Evidence 合并到一个 coverage cell，也可以
   随更细证据拆分或改写旧描述。
4. Schema 可以重新分组，但不能删除任何 eligible Evidence ref，也不能把 invalid/infra
   event 放进 coverage。

`region_key` 和 `point_key` 只是每次 review 的内部审计标签，不是稳定的空间主键，也不会逐
轮修改历史 plan。`coverage_id` 是某一 Schema 版本中的语义 cell id，允许随新版 Schema
重组；跨版本稳定的事实身份是它引用的 immutable `evidence_event_id`。

例如：

```text
declared:  "优化 hot loop 的 tiling"
realized:  BLOCK_M 64 -> 128，public workload，neutral
mapped:    coverage cell "hot-loop / BLOCK_M 64-to-128 / public workload"
           evidence_event_ids = ["se-000021"]
```

后续 lane 即使写成“把 row block 翻倍”，只要 context、实际干预和信息目标相同，就会映射到
该 cell 并 reject；如果改的是 `BLOCK_N`、workload 前提不同，或明确回答新的不确定性，则可
以同一区域中的新点 accept。

### 4.2 为什么每个 Schema 版本使用独立文件

Schema snapshot 可以改成单个 JSONL 文件追加，但当前实现有意保留：

```text
schemas/schema-000001.json
schemas/schema-000002.json
...
```

原因不是 snapshot 内容大，而是原子性和不可变性：

- 每个版本通过 create-only hard link 原子发布，发布前就设为只读；
- 已提交版本可以独立校验 content hash 和 parent hash；
- crash 后可以直接识别“文件已发布、state head 尚未推进”的情况；
- 单个 JSONL 必须长期保持可写，进程中断还可能留下 torn tail record，无法对单个旧版本设置
  文件级只读保护。

调到 20 轮后，Schema 文件数约为 `1 + eligible_events / 20`。即使有 200 次有效尝试，也只有
约 11 个 Schema 文件；真正数量更多的是逐轮 Evidence，而不是周期快照。另外，当前文件是
完整 snapshot，不是小型 patch，后续版本会携带完整 coverage。

因此当前不把 Schema chain 合并成一个热 JSONL 文件。如果未来真实运行达到数千个 Schema
版本并出现 inode 压力，更合适的演进是把已经关闭的版本打包成只读 segment，并保留 head
manifest，而不是牺牲在线提交的原子性。

## 5. Search State 与审核上下文

`state.json` 是当前协调状态，不是事实账本。它保存：

- `state_version`；
- `admission_revision`；
- `evidence_revision`；
- 当前 `schema_revision` 指针；
- `next_plan_index`；
- 全局 `active_reservations`；
- verifier-backed `completed_coverage` plan ids。

每次准入时，runtime 组合出的私有 Search State 视图是：

```text
latest immutable SearchSchemaSnapshot
+ SearchEvidence tail after built_through_event_index
+ global active reservations
+ candidate PlanCard
```

Admission review 注入到 reviewer config 的 `_runtime_search_state` 包含：

- snapshot version 和 built-through event；
- snapshot 的 coverage review view；
- 尚未归并的 compact Evidence event views；
- active reservation 的 plan id、candidate id 和 PlanCard。

Evidence 在 verifier 完成后立即进入 tail，下一次任何 lane 的审核都会看到，不需要等待 20
轮 checkpoint。因此周期 Schema 不会造成在线判重的信息空窗。

已经归并的 completed plans 不再逐条重复塞进 reviewer history；它们由 snapshot coverage
及其 Evidence refs 表示。只有 tail 对应的 completed plans 和 active reservations 保留逐
plan 详情，所以深度搜索的审核上下文不会随全部历史无界线性增长。

Admission 的输出模型只有 accept/reject 分类，不含 `schema_update`。Checkpoint 到达时，
runtime 另行构造 consolidation view：完整 snapshot coverage refs、截至 frozen watermark 的
Evidence tail、`schema_refresh_due=true` 和 `target_event_id`；它不包含 candidate PlanCard 或
active reservations，因为这次调用不做准入。

这里没有第二个 per-round diff verifier Agent。`review` 和 `consolidate` 是同一个全局
SpaceAgent 的两个操作：前者每个 plan 调用，后者每累计 20 条 eligible Evidence 才调用一次。
Schema consolidation 的延迟不会改变已经提交的 verifier 事实或 candidate admission。

### 5.1 上下文长度

SpaceAgent admission 的逻辑输入只有：

```text
candidate PlanCard + Search Schema + Search Evidence tail + reservations
```

周期 consolidation 不带 candidate PlanCard 和 reservations，只带 Search Schema、完整 coverage
refs 和截至 frozen watermark 的 Evidence tail。

但磁盘对象和 prompt view 必须分开。磁盘上的 immutable Event 保留 bounded diff，供审计、
恢复和未来重建；送入 SpaceAgent 的 Event view 不包含 `diff_patch`，只包含：

- 三字段 declared PlanCard；
- artifact delta hash；
- 最多 12 个、合计最多 1,200 字符的 delta files；
- 最多 12 个、合计最多 1,200 字符的 changed symbols；
- 最多 400 字符的 diff stat；
- 最多 1,000 字符的 deterministic diff excerpt；
- score、outcome、validity 和 failure class。

PlanCard 每个字段最多 2,000 字符。普通 admission 中，每个 coverage cell 最多带 4 个代表性
Evidence/plan refs 和完整 ref count；到 20 轮 checkpoint 时才提供完整 refs，使 SpaceAgent
能够生成不遗忘任何 Evidence 的完整新 snapshot。

所以旧实现中潜在的 `20 x 12KB diff` 不会进入 prompt。上下文仍不是数学上的固定常数，因为
Schema 的 cell 数会随真实覆盖增长，但在当前两小时级实验中主要增长项已经受控。如果未来
coverage 达到数千个 cell，应增加分层 Schema 或检索层，而不是重新塞回完整 Event history。

## 6. 语义判重

SpaceAgent 分别比较六个视角：

```text
artifact       修改的代码或对象
configuration  参数和结构设置
mechanism      声称生效的因果机制
context        baseline、workload 和前提
epistemic      希望回答的不确定性
behavior       期望观察到的行为
```

核心判定规则：

- 文字改写但 material intervention 和信息目标相同，reject；
- 只把 broad plan 说得更详细，不自动成为新点；
- 参数、上下文或机制存在实质差异，并会回答新的问题，可以 accept；
- refinement、replication、interaction、alternative implementation 这些标签本身不能证明
  新颖；
- 不确定时 accept；
- active reservation 与 completed coverage 都参与全局判重。

Declared proposal 是执行前意图，Search Evidence 是执行事实。比如 declared plan 只写
“处理 tiling”，实际 diff 是 `BLOCK_M: 64 -> 128`：

1. verifier 后立即追加同时包含 declared plan 和具体 diff 的 Evidence；
2. checkpoint 前，后续审核直接从 tail 看到这个具体事实；
3. checkpoint 时，SpaceAgent 可以把当前 Schema 描述变细，并把 coverage 绑定到该 event；
4. 原始 Evidence 和旧 Schema 版本保持不变。

这代替了逐 plan 的 mutable projection。语义解释在全局 Schema 中按证据演化，不在下一轮
偷偷重写上一轮事实。

## 7. 全局并发与 CAS

Candidate lane 各自规划和执行，但 SpaceAgent、Evidence chain、Schema chain 和 reservation
集合都是 run 级全局对象。Reviewer 调用可能较慢，因此不能在模型调用期间持有文件锁。

准入采用乐观并发：

```text
1. 锁内分配 plan_id，写 reviewing plan
2. 锁内读取 revisions、schema snapshot、tail events、coverage、reservations
3. 锁外调用 SpaceAgent
4. 重新加锁，比较 admission/evidence/schema revisions
5. revision 未变：验证 reviewer 输出并提交
6. revision 已变：丢弃陈旧结果，基于最新全局状态重新审核
```

Accept 会原子加入 reservation 并推进 `admission_revision`。完成 verifier 会释放 reservation、
追加 Evidence，并仅在 eligible 时加入 completed coverage。Schema checkpoint 会先发布新的
immutable snapshot，再推进 `state.json` 中的 schema head。

两个 lane 同时提交等价 plan 时，都可能在旧快照上得到初步 accept，但只有第一个能通过
CAS。第二个必须重审，此时能看到第一个的 active reservation，并返回带具体
`duplicate_of` 的 reject。

Schema consolidation 使用独立的全局 lease，不占用 admission reservation：

```text
1. Evidence 提交后，锁内检查阈值和现有 claim
2. 只有一个调用能 claim(parent schema revision, target event watermark)
3. 锁外调用 SpaceAgent consolidate
4. 同期 admission 和 verifier 可以继续，新的 Event 追加在 watermark 之后
5. 锁内确认 claim 和 parent revision 后发布 snapshot，再推进 schema head
6. watermark 之后的新 Event 保留为下一版 tail，不触发整次重做
```

因此多个 lane 同时越过阈值时只有一个 consolidation 调用和一个 snapshot commit。Admission
revision 的变化不会让 Schema 归并饥饿；新 Evidence 也不会使已经冻结的 target 失效。Claim
持有者崩溃或超过 reviewer timeout 后，后续 Evidence 提交可以回收 lease 并重试。

## 8. 原子发布与恢复

可变 `state.json` 是 read-model head；immutable Event/Schema 是事实和版本源。发布顺序允许
在崩溃后恢复：

- Evidence 已发布、plan/state 尚未完成：加载状态时从 Event 恢复 completed plan、释放
  reservation 并推进 evidence revision；
- completed plan 已写、state 尚未完成：从 plan 完成 reservation 到 coverage 的迁移；
- Schema snapshot 已发布、state 仍指向父版本：校验完整父链后把 schema head 推进到已经
  发布的最新版本，并清除遗留 consolidation claim；
- consolidation claim 已写但没有 snapshot：超过 lease 后允许新 attempt 从相同或更后的
  Evidence watermark 重新 claim；旧 attempt 即使迟到也因 attempt id 不匹配而不能提交；
- reviewing plan 已分配、next id 尚未推进：从已有 plan 文件恢复下一个 plan index。

恢复永远不会覆盖 immutable 文件。内容 hash、父链、编号或 plan/event 唯一性损坏会直接
报错，不会静默接受被改写的历史。

## 9. Solution 回退

Solution artifact 可以撤销或回到 incumbent；Verified Evidence 只能追加；Search Schema
可以随 Evidence 修订。

所以一次有效但无收益的修改即使随后被 Git reset：

- artifact 可以消失；
- 对应 Evidence event 仍存在；
- 该具体点仍属于 coverage；
- 后续等价计划仍可被判为重复。

回到已经 verifier-backed 的 revision 是 Solution artifact lifecycle，不是新的搜索点，不应为了
恢复动作再申请 Space admission；否则 SpaceAgent 会正确地把“重做同一 intervention”判为
重复，却反过来阻断 incumbent 恢复。类似地，`.tmp/handoff.json` 是非 material 的控制面输出，
不进入 Search Space。进入 selection 时 runtime 会把尚未 verifier 的 reviewing/accepted plan
标记为 aborted 并释放 reservation，避免 run 完成后遗留一个伪 active point。

SpaceAgent 不需要记录 `kept / rolled_back / advanced`，也不负责恢复 results ledger。这些都
属于现有 Solution/Goal Plus runtime，而不是 Space 的故事。

## 10. Failure Policy 与模式

Admission reviewer 超时、进程失败或输出格式错误记录在 plan audit 中，并按现有策略
fail-open。Schema consolidation 的失败不属于 candidate admission 失败：runtime 清除 claim、
增加 consolidation failure 计数、保留完整 Evidence tail，并在后续 Evidence 提交时重试；
它不会创建 `reviewer_fail_open` plan，也不会停止 candidate。Immutable chain 校验失败属于
状态完整性错误，不 fail-open。

| 模式 | Reviewer | Reject 是否阻止执行 | 周期 Schema |
|---|---|---:|---:|
| `observe` | 在线审核 | 否 | 开启 |
| `enforce` | 在线审核 | 是 | 开启 |
| `b1` | 不调用 reviewer | 否 | 关闭 |
| `b4` | 在线审核 | 是 | 关闭 |

`b1/b4` 是冻结 VLIW 对照实验的兼容模式，继续使用 `space-experiment/` 路径；正式模式使用
`search-space/`。关闭 legacy arm 的周期归并可以保证旧实验协议和 analyzer 不被新 Schema
行为改变。

Status 提供 plan/outcome 计数、duplicate probability、active collision、admission reviewer
latency、Evidence head、Schema head/tail/coverage、consolidation attempt/success/failure、当前
claim、Schema reviewer latency/usage，以及按 candidate 统计的连续重复信号。
`possible_spinning` 只用于观测，不给 candidate 方向建议。
