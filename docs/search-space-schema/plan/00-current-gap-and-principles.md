# 当前差距与设计原则

本文把论文对象与当前 Goal Plus 的真实实现逐项对照。结论不是“当前方案需要推倒重来”，而是：现有 runtime 已有持久化、workspace、verifier、Git 迭代和 host 边界等关键地基，但搜索知识仍以 candidate/iteration 和有界摘要为中心，缺少可重索引的事件事实层与版本化决策状态。

## 当前能力与目标差距

| 论文/目标概念 | 当前可复用能力 | 明显差距 | 推荐迁移 |
|---|---|---|---|
| Universal Intervention Meta-Schema | `CandidateProposal` 已有 `base_candidate_id`、`hypothesis`、`intent`、`instructions` | Base/Target/Intervention/Context/Hypothesis/Expected Observation 未被类型化；大部分内容仍是自由文本 | 新增独立 `TypedInterventionIR`，先 shadow 生成，不立即强制 |
| `AtomicPlan` | `SearchPlan`、`CandidateWorkOrder`、`CandidateProposal` | `SearchPlan` 是轮次计划，不绑定 `SearchState` 版本，也没有声明足迹、关系和准入事务 | 保留 `SearchPlan`；新增一项提案对应一个 `AtomicPlan` |
| 不可变 `SearchEvent` | `IterationRecord`、`ResultLedgerEntry`、workspace Git commit、verifier log | 记录以 candidate/score 为中心；没有统一事实/解释 provenance；异常发生在 verifier report 前时没有正式事件 | `run_verifier` 后处理生成 canonical event；iteration 反向引用 `event_id` |
| Declared/Realized Footprint | proposal 的 `metadata`、changed files、metrics | 没有统一多视图投影，也不比较执行前意图与实际 diff/behavior | P1 生成 declared，P2 生成 realized，P3 开始比较与聚合 |
| Versioned Search State | `RunRecord`、plans、candidate records、history view | 没有单调 `state_version`、schema version、coverage、reservation；`RunRecord` 不是决策充分状态 | 增加独立、不可变 `SearchStateCommit` 和原子 `HEAD` |
| 事件永久、抽象暂定 | Git 迭代与 `results.tsv` 可持续继承 | research rollup 是派生、截断和当前实现逻辑决定的摘要，不能重索引；event 与 abstraction 未分层 | 事实层 append-only，schema/coverage 作为版本化派生层 |
| Search Schema | 无一等对象 | 无 ontology、view、projection rule、split/merge lineage、embedding version | 先静态 schema，再在线 revision；latent view 延后 |
| Marginal Non-Redundancy | history、feature ledger、pitfalls、proposal `search_action` | 没有 overlap vector、合法重合关系、false rejection 评估 | 先离线标注和 shadow score，再 advisory，最后只强制高置信规则 |
| Atomic Plan Admission | `plan_next`/`start_batch` 已在 `run.lock` 下串行、candidate id 和预算会持久化 | 没有 expected state version、stale/rebase、reservation、语义冲突审查 | 复用 `run.lock`，增加乐观版本检查和独立 admission record |
| Verified Evidence Commit | `run_verifier` 在外部执行后进入 `run.lock` 写 iteration、ledger、best/run | 未形成统一 event + coverage + reservation release + state version 提交 | 把现有提交段升级为 evidence commit，不让 worker 直接写官方状态 |
| Read-committed visibility | worker 通过 `get_agent_context` 读取官方 context/history；host session 是 provenance | worker 还看不到 schema/version/coverage/reservation 的压缩视图 | 扩展只读 context；仍不暴露其他 worker 私有推理或 workspace |
| 并行 host | Pi durable pool；Codex `spawn/wait-any/followup/interrupt`；host adapter contract | 当前并行主要靠 main agent policy，未以共享 footprint reservation 协调 | 最后阶段在现有 host 生命周期上叠加 TSC，不移动 ownership |

## 当前实现中最有价值的接缝

### 1. `run.lock` 与原子文件替换

当前 `plan_next`、`start_batch` 和 verifier 结果记录已经在 per-run lock 下修改持久状态；JSON 写入使用临时文件替换。它们可以支持：

- `expected_state_version` 检查；
- admission 的 candidate id、预算和 reservation 同一提交；
- evidence commit 的 event、coverage、reservation release 和新版本提交；
- 幂等重试与 crash recovery。

但“每个 JSON 单文件原子”不等于“多对象事务原子”。目标设计必须使用不可变对象 + 最后原子更新 `HEAD` 的提交协议，不能依赖依次覆盖多个可变文件。

### 2. `run_verifier` 的两段式结构

当前 verifier 在 lock 外执行，避免长时间占用 run lock；结果、Git head、artifact hash、changed files 和 metrics 计算完成后，再在 lock 内写 iteration 和 run。这正好对应：

```text
physical execution outside lock
  -> factual extraction
  -> VerifiedEvidenceCommit under run.lock
```

迁移时应保持这条性能和 ownership 边界，不把 verifier 或 worker 生命周期塞入事务锁。

### 3. `get_agent_context`

它已经是 worker 的权威恢复入口，包含 workspace、run/candidate/session id、iterations、results ledger 和 history。新的搜索状态不应另建第二套 worker memory 协议，而应在这里增加有界只读视图：

- `search_state_version`；
- `schema_version`；
- 当前 candidate 的 admitted `AtomicPlan`；
- completed coverage 摘要；
- active reservation 摘要；
- 与本计划最相关的历史事件与未决假设。

### 4. Git 迭代与 `results.tsv`

当前 verifier-backed Git commit 已能绑定实际工件；`results.tsv` 是 workspace 内可继承、runtime-owned 的连续实验账本。它们应继续服务 worker 的本地 AutoResearch loop。

新的 `SearchEvent` 不应取代它们，而应成为 run 级 canonical 事实层：

- Git commit 回答“实际工件是什么”；
- `results.tsv` 回答“该 candidate 链上看到了哪些简洁结果”；
- `SearchEvent` 回答“整个 run 发生了哪些带 provenance 的干预—结果事实”；
- `SearchState` 回答“当前规划需要看到哪些压缩决策信息”。

## 必须坚持的设计原则

### 原则 A：先证明状态有用，再让状态有权

所有语义能力按四档演进：

```text
disabled -> shadow -> advisory -> enforced
```

- shadow：计算但不影响候选准入；
- advisory：向 main/worker 提示重合和新增价值，但允许继续；
- enforced：只对已达到高精度标准的确定性约束或高置信重复进行拒绝。

禁止从“能生成 overlap 分数”直接跳到“自动拒绝计划”。

### 原则 B：事实层与解释层严格分离

事实层包括：hash、diff、changed symbols、环境、verifier 版本、指标、trace、失败类型和时间。解释层包括：target 归一化、mechanism、hypothesis、ontology 节点、overlap 和 schema revision。

- 事实层 append-only；
- 解释层 versioned；
- 每个解释字段保存 `observed`、`agent_declared`、`system_inferred`、`experimentally_supported`、`experimentally_contradicted` 或 `unknown` provenance；
- schema 更新只能重投影事件，不能改写原始事件。

### 原则 C：AutoResearch loop 仍是最小执行单元

P1-P5 不要求每次 verifier 只做一次修改。一个 worker 仍可在自己的 candidate workspace 中持续：分析、实现、验证、保留/回退、再尝试。新增能力是让每个正式尝试：

- 先有可证伪的干预声明；
- 后有忠实的实际事件；
- 被纳入同一 run 的搜索状态；
- 下一次尝试能看到结构化的已搜索信息。

这样每一步都增强普通 AutoResearch，而不是先换掉整个执行范式。

### 原则 D：不把 LLM 判断伪装成事实

LLM 可以：

- 初始化 ontology；
- 归一化 target/mechanism/context；
- 提出 overlap、split/merge 和 relation；
- 解释 outcome 与 hypothesis 的关系。

Runtime 必须：

- 生成确定性 base/hash/diff/environment/verifier 字段；
- 校验结构、版本、预算、edit surface 和 reservation；
- 保存 LLM 判断的模型/提示版本、置信度和输入事件引用；
- 允许 `unknown`，不强迫错误分类。

### 原则 E：不提前引入 embedding 复杂度

P0-P5 的主路径使用：结构化字段、规则、LLM 归一化、显式多标签和小规模统计。只有当这些方法已证明覆盖或检索瓶颈，才在 P8 引入 learned latent view。否则 encoder 版本漂移会过早污染 schema revision 和历史可比性。

### 原则 F：并发是最后一项价值假设

P0-P5 只需单 worker 就能产生价值：实验纪律、永久账本、搜索状态、重复提醒和图式修订。P6 仅验证事务正确性，P7 才验证真实并行收益。

如果 P7 未通过研究门槛：

- 保留串行 SSI；
- 保留事件账本和 advisory admission；
- 将 reservation/parallel 维持为 opt-in；
- 不把“并行可运行”宣传为“并行搜索更智能”。

### 原则 G：host-neutral 不等于首批支持所有 host

数据模型、状态事务和 API 保持 host-neutral；首批 executable assets 只更新 Pi/Codex。OpenCode/Claude 的旧路径可继续以 `disabled` 模式使用现有 runtime，直到后续有人承担真实 smoke 和 asset contract。

## 首轮明确不做

- 不删除现有 `SearchPlan`、`IterationRecord`、`results.tsv` 或 `inherited_research`。
- 不把 `RunRecord` 塞成包含全部 event/schema/coverage 的超大 JSON。
- 不让 worker 直接提交“已验证事实”；事实由 runtime 从工件和 verifier 提取。
- 不用自然语言相似度作为唯一重复判断。
- 不用 active reservation 代替 host pool state。
- 不让 runtime 等待、终止、续跑或统计 live worker。
- 不把一次 candidate 失败升级成全局禁止方向。
- 不因动态 schema 尚未成熟而阻塞静态 schema 上的串行价值。

---

[上一页：总计划](README.md) | [下一页：研究与实验标准](01-research-protocol.md)
