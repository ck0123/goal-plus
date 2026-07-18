# 交付拆分

本页按代码所有权拆分可独立 review 的工作包。不要把模型、持久化、runtime
集成、host asset 和大范围并发改造放进同一提交。

## 当前边界

- AtomicPlan 生成 Agent：外部同事负责。
- SearchEvent、SearchState、admission、reservation 和完整 parallel loop-agent
  流程：作为同一外部实现方向交付，不再从本计划拆出一条本地优先主线。
- 统一统计与 HTML 报告：已完成；后续只随新增持久化字段扩展。

以下工作包用于说明外部实现的代码边界和 review 顺序，不表示需要在当前仓库中另行
启动相互竞争的实现。

## 工作包

### WP1：AtomicPlan 生成合同

**Owner：外部同事。**

- Typed Intervention models 与 provenance；
- plan normalization/validation；
- Pi/Codex plan-generation asset；
- 稳定输出 envelope 和 legacy fallback。

其他工作包不修改这套生成逻辑，只消费冻结后的 plan id 和字段合同。

### WP2：SearchEvent Core

- `SearchEvent`、event kind、invocation id 和 idempotency key；
- immutable event storage 与 read/export；
- observed Git/artifact/verifier fact extraction；
- legacy iteration importer；
- `tests/test_search_events.py`。

### WP3：SearchEvent Runtime Integration

- `run_verifier` 提交段写 event；
- iteration/results/event refs；
- failure boundary 与 retry；
- `search_list_events`、monitor、统计和报告字段；
- runtime backward-compatibility tests。

WP2 可以独立完成；WP3 等 AtomicPlan envelope 稳定后接入 plan refs。

### WP4：Static Schema 与 Projection

- schema v1 与 footprint models；
- deterministic projector；
- semantic projector 的 optional/provenance 输出；
- projection cache 与 schema-version linkage；
- `tests/test_search_projection.py`。

### WP5：SearchState Store 与 Read Models

- immutable state、parent chain、atomic HEAD；
- replay、orphan diagnostics 和 compact indexes；
- audit/planning/worker-local/monitor views；
- `search_get_search_state` 与 agent-context projection；
- `tests/test_search_state.py`。

### WP6：Serial Admission

- prepare/admit/get/explain APIs；
- state-version、budget、parent、edit-surface checks；
- exact duplicate rule 与 semantic advisory；
- compatibility `search_start_batch`；
- `tests/test_search_admission.py`。

### WP7：Schema Revision

- revision proposal/decision/lineage；
- extend/split/merge/re-index；
- complexity budget 与 interrupted recovery；
- old schema/state readability。

### WP8：Transactional Core

- reservation 与 optimistic concurrency；
- idempotent admission/evidence commit；
- crash points、orphan/recovery diagnostics；
- randomized interleaving tests；
- 不接真实 host 并行。

### WP9：Pi Integration

- Candidate 自主 AtomicPlan loop prompt 与 context；
- Main 一次性创建 initial candidates；completion 后只验收、更新 best、continue
  相同 candidate；
- 明确 state-level redispatch 与 native same-session resume 的能力差异；
- 不自动补槽或重规划；
- Pi skills、prompt、extension 和 focused host tests。

### WP10：Codex Integration

- Main 一次性 spawn initial candidates；
- Candidate 在同一 child turn 内自行完成 AtomicPlan/admission/verifier 循环；
- completion 后通过 existing task continuation resume 相同 native subagent；
- neutral resume/closeout 不承担搜索方向决策；
- Codex skill、agent、hook contract 和 focused host tests。

## 推荐提交边界

每个工作包按以下顺序拆提交：

1. models 与纯函数；
2. immutable persistence 与 recovery；
3. runtime lifecycle integration；
4. read API、monitor、统计和报告；
5. Pi assets；
6. Codex assets；
7. tests 与文档。

`.gp/`、candidate workspaces、raw host logs、transcript 和生成的 HTML 不提交。

## Feature Mode

| 能力状态 | 默认模式 |
|---|---|
| 对象或兼容读取未完成 | `disabled` |
| 新事实已持久化但不影响行为 | `shadow` |
| 状态与解释可稳定读取 | `advisory` |
| 确定性规则、幂等和恢复已覆盖 | limited `enforced` |
| 事务核心与 host 集成完整 | parallel `opt-in` |

Semantic overlap 默认不进入硬拒绝；exact duplicate 可以作为独立确定性规则。

## Host 支持矩阵

| 阶段 | Runtime | Pi | Codex | OpenCode | Claude Code |
|---|---|---|---|---|---|
| P1 | typed plan readable | plan generation | contract parity | legacy | legacy |
| P2 | event shadow | readable | readable | compatible records | compatible records |
| P3 | state shadow | reference | parity | disabled | disabled |
| P4 | serial admission | supported | supported | strict mode unsupported | strict mode unsupported |
| P5 | schema revision | same runtime | same runtime | unsupported | unsupported |
| P6 | transaction core | no host parallel | no host parallel | unsupported | unsupported |
| P7 | admitted task execution | opt-in | opt-in | deferred | deferred |

## Definition of Done

### 数据与兼容

- strict、versioned、向后可读；
- immutable facts 不可被正常 API 覆盖；
- event/state/schema/admission refs 可从 monitor 和报告定位；
- 旧 spec/run 行为有测试。

### 正确性

- retry、restart、duplicate request 和 crash 有确定行为；
- verifier invalidation、selection 和 promotion fence 保持有效；
- host pool state 不进入 Search records；
- focused tests、默认测试和 `git diff --check` 通过。

### Host 与文档

- 所需 Pi/Codex asset 与 runtime contract 同步；
- design、flow、api、debugging、monitor 和报告同步；
- OpenCode/Claude 支持状态不被夸大。

## 当前建议顺序

1. 与同事冻结 AtomicPlan envelope、plan id 和 legacy fallback。
2. 实现 WP2 SearchEvent Core，不修改 worker lifecycle。
3. 接 WP3 verifier/event 集成，并补 monitor/report 字段。
4. 实现 WP4 deterministic projection。
5. 实现 WP5 SearchState store/read models。
6. 再进入 serial admission、schema revision 和 transaction core。
7. 最后接 Pi/Codex host 集成。

## 交付清单

- [ ] AtomicPlan 生成合同已冻结
- [ ] SearchEvent core
- [ ] SearchEvent runtime integration
- [ ] static schema 与 projection
- [ ] SearchState store 与 read models
- [ ] serial admission
- [ ] schema revision
- [ ] transaction core 与 reservation
- [ ] Pi integration
- [ ] Codex integration
- [ ] 主文档、monitor、统计和 HTML 同步

---

[上一页：Runtime、数据与 API 设计](03-runtime-data-api-design.md) |
[返回总计划](README.md)
