# 当前差距与设计原则

当前 runtime 已具备持久化、candidate workspace、verifier、Git 迭代、
`results.tsv`、host adapter、monitor 和报告。缺少的是可重放的搜索事实层、
版本化决策状态，以及围绕状态版本的准入与提交协议。

## 当前能力与目标差距

| 目标对象 | 当前可复用能力 | 仍需增加 |
|---|---|---|
| Typed Intervention | proposal、hypothesis、structured handoff | typed envelope、provenance、expected observation |
| `SearchEvent` | `IterationRecord`、Git head、artifact hash、verifier report | run 级不可变事件、invocation id、幂等身份 |
| `SearchState` | history、feature ledger、research rollup | version、HEAD、coverage、claim state、bounded read models |
| Search Schema | strategy/scenario fields | schema version、projection rules、revision lineage |
| AtomicPlan Admission | `SearchPlan`、candidate allocation、`run.lock` | prepare/admit/rebase、overlap explanation、idempotency |
| Transaction Core | atomic JSON write、run lock | reservation、evidence commit、orphan/recovery diagnostics |

## 可复用接缝

### `run.lock` 与原子替换

现有 runtime 已用 run 级锁保护 candidate 分配和 verifier 提交。新的 state
commit 应继续复用该边界：先写不可变对象和 state 文件，最后原子替换
`HEAD.json`。

### `run_verifier` 两段式结构

物理 verifier 在锁外执行，持久化提交在锁内完成。`SearchEvent`、realized
footprint 和 evidence commit 应进入第二段，不得让长时间 verifier 占用锁。

### `search_get_agent_context`

它仍是 worker 获取权威 candidate 上下文的入口。后续只在其中增加 admitted
plan 和 worker-local state，不要求 worker 拼接多份可能漂移的状态文件。

### Git 迭代与 `results.tsv`

Git head、artifact hash 和 verifier 结果已经能绑定实际工件。新事件应引用这些
事实，不复制大 diff、日志或 workspace 内容。

## 设计原则

### 先可读，再有权

SearchEvent 和 SearchState 先以 shadow/read-only 形式存在。状态写入、旧 run
读取、重放和恢复稳定后，再允许 advisory 或 admission 使用它们。

### 事实层与解释层分离

- observed facts：Git、diff、hash、verifier、环境和时间；
- declared facts：Agent 提交的 target、mechanism、context 和 expected observation；
- inferred facts：normalizer/projector 生成的标签和关系。

每个字段必须保存 provenance。LLM 解释不能覆盖 observed facts。

### AutoResearch loop 保持局部自治

Candidate worker 继续在自己的 workspace 中修改、验证、保留或回退方案。
共享状态负责记录和提供上下文，不接管 worker 的内部循环。

### 保守处理 overlap

确定性 exact duplicate 可以形成明确规则；semantic overlap 默认只解释和提示。
合法的 refinement、replication、interaction 和 alternative implementation 必须能显式表达。

### 并发建立在事务状态之上

先完成单 worker 的 event/state/admission，再实现 reservation 和故障恢复，最后
接 Pi/Codex 并行。Runtime 只记录官方搜索空间占用，不记录 PID、heartbeat 或
虚构的 worker liveness。

### 首批只支持 Pi 与 Codex

共享数据模型保持 host-neutral。OpenCode/Claude 的旧 run 必须继续可读，但不
要求首批 strict admission 或 reservation asset parity。

## 首轮不做

- learned embedding/latent view；
- 跨 run/domain ontology 迁移；
- distributed transaction store；
- runtime-owned wait/abort/heartbeat；
- OpenCode/Claude strict mode；
- 将 raw logs、transcript 或完整 diff 写入 SearchState。

---

[上一页：总计划](README.md) | [下一页：分阶段实施路线](02-phased-roadmap.md)
