# 分阶段实施路线

本路线按“每一步都有独立产品价值、每一步都由实验决定晋级”组织。工作量用相对级别表示：S 为几天级，M 为约一到两周级，L 为多周且需要真实实验资源；它不是发布日期承诺。

## 依赖关系

```text
P0 baseline
  -> P1 typed intervention
  -> P2 immutable event ledger
  -> P3 versioned state in shadow/advisory
  -> P4 serial SSI admission
       ├─> P5 adaptive schema induction
       └─> P6 transactional core simulator
              -> P7 Pi/Codex parallel TSC

P8 latent/cross-task/other hosts: only after P7 decision
```

P7 需要 P4 和 P6；如果 P5 未通过，P7 可以使用冻结 schema，但必须在结论中明确“验证的是 static-schema TSC”，不能声称完成动态 Search Schema Induction。

## P0：建立可复现的 AutoResearch 基线

**工作量：S。行为变化：无。**

### 目标与即时价值

把当前单 worker 循环冻结成可复现对照，测清：run 成功率、最佳分数、verifier 次数、token/时间、时间重复、研究摘要可用性和 host 故障。即使后续方案停止，P0 仍会留下可靠 benchmark harness 和成本基线。

### 实现步骤

1. 选择 Tier A/B/C 任务并冻结 source commit、frozen spec、verifier hash 和 case identity。
2. 为当前 history/results.tsv/iteration 增加只读导出脚本，不改变规划行为。
3. 在现有 monitor/export 中补齐 run-level 指标：verifier count、iteration count、candidate lineage、token/time/cost、failure class。
4. 建立实验 manifest、run registry 和可重放分析脚本。
5. 定义第一版人工 overlap 标注规范，并从基线 run 抽样计划/事件对。
6. Pi 与 Codex 各跑至少一个真实串行 E2E；大量重复优先使用 Pi 和便宜任务。

### 关键实验

- E0：基线可重复性与运行失败分解；
- E0b：当前 history/research rollup 的 token、证据召回和重复判断基线；
- 人工标注 pilot：确认“无信息重复”和合法 refinement/replication 能被一致区分。

### 晋级门槛

- deterministic Tier A/B harness 可重放；
- Pi/Codex 基线 run 的关键工件和指标完整；
- 真实 host 完整 run 成功率达到 E0 初始门槛；
- 标注规范达到可接受的一致性，否则先修标签定义，不能直接训练/评估 overlap；
- 能为每个 run 给出完整预算和成本，不再只比较最终 score。

### 失败回退

若基线方差过高，先稳定 verifier、host budget 和 task harness。不得在不可比较的基线上继续声称 schema 改善。

## P1：Falsifiable AutoResearch——结构化干预计划

**工作量：M。行为变化：shadow，随后 advisory。**

### 目标与即时价值

让每个正式实验在执行前说明 Base、Target、Intervention、Context、Hypothesis 和 Expected Observation。此阶段不判断搜索空间覆盖，也不拒绝重复；价值是更好的实验纪律、结果归因、handoff 和报告。

### 实现步骤

1. 在 `models.py` 增加 `TypedInterventionIR`、`TargetChange`、`HypothesisClaim`、`ExpectedObservation` 和字段 provenance。
2. `CandidateProposal` 增加可选 typed IR 引用；保留现有自由文本字段以兼容旧策略。
3. 增加 deterministic validator：base/candidate 存在性、字段类型、expected observation 可测量字段、edit surface 提示。
4. 增加 LLM normalizer，但输出只进入 `system_inferred` 字段，并保存 normalizer/prompt version 与 confidence。
5. `search_get_agent_context` 返回当前 intervention schema 和本 candidate 已登记计划。
6. `search_run_verifier` 改为优先接收 `intervention_plan_id`；兼容旧 `hypothesis` 字符串，并标记 `legacy_unstructured`。
7. Pi worker prompt 首先生成 typed plan；Codex worker agent/skill 保持同一字段语义。
8. 报告中增加“预期观测—实际观测—支持/反驳/未判明”表格。

### Pi/Codex 顺序

- 先在 Pi 单 worker 完成真实 typed-plan E2E；
- Codex 先完成 asset/schema/unit parity，再完成一个真实串行 E2E；
- OpenCode/Claude 继续走兼容旧字段，不阻塞。

### 关键实验

- E1：typed plan 结构有效率、target grounding、expected observation 可证伪性；
- B0 vs B1：同预算下有效 verifier 尝试数、最终结果和 token overhead；
- 盲评结构化报告是否比当前 hypothesis/results.tsv 更容易复现和解释。

### 晋级门槛

- typed plan 有效率和人工质量达到 E1；
- 相对 B0 不显著降低有效尝试数和最佳结果；
- normalizer 失败可回退 `unknown`，不会阻塞 worker；
- Pi/Codex 至少各有一个真实 E2E，其中计划、Git commit 和 verifier evidence 可完整关联。

### 失败回退

若 LLM normalizer 不可靠，保留 Agent 直接填写的 typed envelope 和 deterministic fields；若结构化开销过大，将部分字段降为 optional/advisory，但保留 plan id 和 expected observation。

## P2：不可变 Intervention–Outcome Event 账本

**工作量：M。行为变化：shadow。**

### 目标与即时价值

每个“已准入并实际开始执行”的尝试都留下 run 级、不可变、带 provenance 的事实，即使 candidate 回退、未改善、编译失败、worker redispatch 或 solution reset。该阶段先解决认知不丢失，不把 event 反馈给规划。

### 实现步骤

1. 增加 `SearchEvent` 模型，绑定 run/candidate/session/iteration/plan、base revision、actual Git/diff、环境、verifier、outcome 和 provenance。
2. 将事件结果分为：`verified_outcome`、`execution_failure`、`infrastructure_failure`、`cancelled_before_execution`。只有实际开始执行的前三类进入 coverage 候选；取消但未执行只保留 admission/host 记录。
3. 在 `run_verifier` 的现有 lock 内提交段生成 canonical event；`IterationRecord` 增加 `search_event_id`。
4. 对 verifier report 前的异常增加显式 invocation id 和 failure event 路径，避免“失败无记录”；同时避免将 runtime 自身在准备阶段的异常误记为已执行实验。
5. 采用一事件一不可变文件或内容寻址对象，避免 JSONL torn append；生成 JSONL 只读导出视图。
6. 定义 idempotency key，重复 tool 调用或 host retry 返回同一 event，不重复扣预算和覆盖。
7. 增加 `search_list_events` 只读 API 和 monitor 事件计数/最近失败摘要。
8. 提供 legacy iteration -> inferred event 的离线 importer，仅用于实验/查看，不改写旧 run。

### 关键实验

- E2：declared 与 realized facts 的提取准确度；
- E3：rollback、candidate derivation、redispatch、runtime restart 和重复调用下的 event durability/idempotency；
- event replay：只用事件和 immutable artifact refs 重建 run 的关键实验时间线。

### 晋级门槛

- 所有已产生正式 verifier/failure outcome 的 invocation 恰好对应一个 event；
- event 能关联到 exact Git head、artifact hash、verifier hash 和 typed/legacy plan；
- solution reset 或 best-candidate 切换不删除历史 event；
- crash/retry 测试无丢失、无重复、无半写对象；
- event 仍不影响 selection 或 planning，便于先验证事实质量。

### 失败回退

若统一 failure event 边界不稳定，先只把已返回 `ScoreReport` 的过程作为 canonical event，其他异常保存 invocation audit；在 E3 通过前不让失败事件参与 coverage。

## P3：版本化 SearchState 与静态多视图投影

**工作量：L。行为变化：shadow -> advisory。**

### 目标与即时价值

在单 worker 场景中显式回答“已经搜索过什么”。使用固定 Universal Meta-Schema 加可选 scenario prior，将 event 投影到 artifact/config/mechanism/context/epistemic/behavior 多视图，形成版本化 coverage 和有界 worker state。

### 实现步骤

1. 增加 `SearchSchemaVersion`，第一版只支持 bootstrap 和冻结，不支持在线 split/merge。
2. 增加 `SearchFootprint`，每个 view 独立保存标签、值、confidence、provenance 和 unknown。
3. 实现 deterministic projector：base/hash/diff/changed symbols/config/environment/verifier/metrics。
4. 实现 semantic projector：target/mechanism/context/epistemic，输出多标签和 unknown，不强制唯一节点。
5. 增加 `SearchStateCommit` 和原子 `HEAD`，每次 event commit 后生成新 state version。
6. 构造 completed coverage、claim status、incumbent refs 和预算摘要；此阶段 `active_reservations` 为空。
7. 增加 read model：full audit view、main planning view、worker-local relevant view、monitor compact view。
8. 用相同 token 预算对比当前 history/research rollup 和新 state view。
9. shadow 记录 overlap vector 和 proposed relation，不改变 candidate materialization。
10. E4/E5 达标后，向 worker/main 展示 advisory：“高度相似历史”“可能的新 context/interaction”“证据未判明”。

### 关键实验

- E4：多视图 overlap 的金标 precision/recall，特别关注 hard duplicate false positive；
- E5：固定 token 预算下的 evidence recall、outcome prediction、下一步计划重复率和 ranking regret；
- B2 vs B3：仅引入 state feedback 是否改变计划质量和成本。

### 晋级门槛

- deterministic facts 达到近乎精确的提取标准；
- advisory overlap 达到 E4，hard reject 精度未达标时继续禁止 enforcement；
- compact state 在相同 token 预算下至少改善一个主要决策指标，且不显著损失关键证据召回；
- state version、schema version、event refs 可追溯，旧 version 可读取；
- Pi/Codex worker 看到的是同一 read model，不出现 host 特有状态语义。

### 失败回退

若 semantic projection 质量不足，保留 artifact/config/context 的确定性视图和 `unknown`；SearchState 可先作为审计/检索层，不进入 admission。

## P4：串行 SSI——AtomicPlan 与 completed-coverage admission

**工作量：L。行为变化：advisory -> limited enforcement。**

### 目标与即时价值

在没有并行 worker 的前提下验证 SSI 的核心产品价值：计划基于哪个 state version、相对已完成搜索新增什么、为何允许合法重合，以及能否减少时间碰撞。

### 实现步骤

1. 增加独立 `AtomicPlan`，包含 `round_plan_id`、`base_state_version`、`schema_version`、Typed IR、declared footprint、relation、budget request。
2. 增加 `AtomicPlanAdmission`，状态为 `accepted/rejected/rebase_required`，保存 reason codes、overlap evidence、candidate allocation 和 new state version。
3. `SearchPlan` 继续决定一轮容量；每个 proposal 必须准备/准入为独立 AtomicPlan。
4. 实现 `search_prepare_atomic_plan`：编译与审查草稿，不修改官方状态。
5. 实现 `search_admit_atomic_plan`：在 `run.lock` 下检查 expected version、预算、edit surface、parent、completed overlap，并原子分配 candidate id。
6. `search_start_batch` 保留为 compatibility composite wrapper；严格模式下只接受已准入 plan，旧模式自动 prepare/admit。
7. serial 模式也创建并持久化 reservation，但它只绑定唯一 admitted plan/candidate，不承担并发冲突过滤；这样可以提前验证 create/release/recovery 生命周期，同时本阶段的搜索价值仍只来自 completed coverage。
8. enforcement 分两步：
   - 先只硬拒绝版本/预算/parent/edit-surface 和确定性 exact duplicate；
   - semantic overlap 只 advisory，直到 E4/E6 的 hard-reject precision 达标。
9. 所有 rejection 都返回可操作解释：重复在哪些 views、合法 relation 缺什么、如何 rebase/refine/contextualize。
10. 将 admission 结果和实际 event 对齐，统计 false accept/false reject 和 plan drift。

### 关键实验

- E6：B0/B1/B4 固定预算串行 A/B；
- 消融：无 state、只给 raw history、只给 footprint、给 footprint + admission reason；
- 重复被拒绝后，Agent 是否能生成有价值的 rebase 计划，而不是换种措辞重复。

### 晋级门槛

- redundant attempt rate 达到 E6 的相对下降目标；
- best normalized score 对 B0 满足非劣效；
- cost per unique footprint 下降；
- hard rejection false positive 低于门槛；
- admission 与 candidate allocation 在重复调用下幂等；
- Pi/Codex 各完成真实串行 SSI E2E。

### 失败回退

若 hard admission 伤害结果，退回 advisory，但保留 AtomicPlan、event/state 和解释；若只有确定性 exact duplicate 有效，则 enforcement 仅覆盖这一子集。

## P5：在线 Search Schema Induction 与 Revision

**工作量：L。行为变化：advisory，局部自动 apply。**

### 目标与即时价值

让系统不依赖固定手工 ontology：无法分类、节点 outcome 多峰、重复误判和 context 交互可以触发 ontology extension、split、merge 和 re-index。价值必须体现为决策损失下降，而不是 schema 更丰富。

### 实现步骤

1. 定义 revision proposal：触发证据、受影响节点/事件、candidate split dimension、预期损失改善和复杂度代价。
2. 先实现 deterministic triggers：unknown 激增、节点 outcome 多峰、context-conditioned residual、false-duplicate 样本、长期低信息节点。
3. LLM 只提出 revision candidate；runtime 在历史事件 replay 和 held-out validation 上评估。
4. 增加 `SchemaRevisionDecision`：accepted/rejected、评估指标、reviewer/version 和 lineage。
5. accepted revision 生成新 schema version；原 event 不变，projection cache 按 schema version 重算。
6. 对正在执行/已准入计划同时保存 declared schema version 和 commit 时 realized schema version。
7. schema complexity 设硬预算：节点、关系、prompt/token 和 re-index 成本均可观测。
8. 默认只由 main/runtime apply revision；worker 可以提出 suggestion，不能直接更新官方 schema。

### 关键实验

- E7：已知隐藏结构的合成任务，验证过粗节点能正确 split、无效区分能 merge；
- E8：真实 Tier B/C run，比较 frozen schema 与 adaptive schema；
- replay/held-out：修订是否降低 outcome、redundancy 或 decision loss；
- drift：跨 schema 版本 coverage 是否仍可解释。

### 晋级门槛

- accepted revision 在 held-out 上达到 E7/E8 的最小改善；
- rejected revision 不改变官方 state；
- schema complexity 增长受控，unknown 不通过无限造节点被“消灭”；
- re-index 可重放、可中断恢复，旧版本仍可审计；
- 至少一个真实任务出现“静态 schema 会误判，而 revision 改善后续决策”的完整案例。

### 失败回退

若动态 revision 未显示可靠增益，冻结在 P4 的 static/scenario schema；保留 revision proposal 和离线分析作为研究工具，不允许在线自动 apply。

## P6：事务核心与 reservation 并发模拟

**工作量：L。真实 host 并行：暂不启用。**

### 目标与即时价值

先证明状态机正确，再花真实 Agent 预算。实现 optimistic concurrency、active reservation、read-committed visibility 和 evidence commit 的一致性，但用线程/进程模拟器和假 worker 验证。

### 实现步骤

1. admission 在同一 state commit 中完成 version check、budget deduction、candidate allocation 和 reservation creation。
2. stale plan 返回 `rebase_required(current_version, conflict_refs)`，不隐式重放 LLM proposal。
3. reservation 定义 owner admission、declared footprint、lease policy、状态和 release reason；它不是 host lifecycle record。
4. materialization 发生在 admission commit 后；失败时提交 system failure/release commit，不能回滚已发生的官方 admission。
5. evidence commit 追加 event、释放 reservation、更新 coverage/claim/incumbent refs 并推进版本。
6. 定义 abandoned reservation recovery：只能依据 admission/state 和 host 提供的终态证据由 main 显式 close，不由 runtime 猜测 worker 已死。
7. 实现 crash point injection：immutable object write 前后、state version write 前后、HEAD swap 前后。
8. 实现随机并发 interleaving、重复请求、乱序 evidence 和 restart recovery 测试。
9. monitor 显示 state/schema version、reservation、stale plan 和 orphan diagnostic，但不显示虚构的 worker liveness。

### 关键实验

- E9：1,000+ 随机并发交错下的线性化、budget/candidate/reservation 守恒；
- E10：crash matrix、重复 admission/evidence、乱序 evidence、恢复后 replay；
- 软/硬 conflict 规则在 active reservation 上的 false conflict 分析。

### 晋级门槛

- 零 candidate/event id collision；
- 零 budget over-allocation；
- 每条 accepted admission 和 evidence commit 都可放入唯一串行版本链；
- 重复请求 100% 幂等；
- crash recovery 无 reachable 半状态；
- 悬挂 reservation 只能通过显式恢复流程关闭，runtime 不越权控制 host。

### 失败回退

在 E9/E10 完全通过前，P7 禁止使用真实并行；单 worker P4/P5 继续可用。

## P7：Pi/Codex 事务化并行价值验证

**工作量：L。行为变化：opt-in；通过实验后才考虑默认。**

### 目标与即时价值

将已验证的事务核心叠加到现有 host pool：Pi 先行，Codex 随后。目标不是仅证明多个 worker 能同时跑，而是证明 active reservation 降低空间碰撞，并在固定墙钟下提高搜索结果。

### 实现步骤

1. Pi pool 只接受已准入 CandidateTask；submit/wait/continue/close 不自行规划或改 SearchState。
2. wait-any 后由 main 触发 parent final verify/evidence commit，再基于最新 state 补槽。
3. stale proposal 必须 prepare/rebase/re-admit；不能因 pool 有空槽跳过 admission。
4. Codex `spawn_agent` 使用同一 admitted task；`wait_agent/list_agents/followup_task/interrupt_agent` 继续只处理 host lifecycle。
5. Codex native continuation 保留原 admission；产生新实验前仍需使用当前 state 记录新的 intervention/event。
6. Pi state redispatch 复用 candidate workspace，但新的 worker session 只读取 committed state；它不创建新 AtomicPlan，除非 main 决定新的搜索干预。
7. active reservation 仅公开压缩 footprint、owner admission/candidate 和状态，不公开私有 reasoning/transcript。
8. 从 `max_parallel=2` 开始，再到 4；只有 E11 显示正向 scaling 才扩大。

### 关键实验

- E11：Pi 上 `serial SSI`、`naive parallel`、`TSC parallel` 的 fixed-compute 与 fixed-wall 对照；
- E12：Codex 重复同样设计，验证语义方向而非要求绝对分数等于 Pi；
- concurrency `1/2/4` scaling curve；
- static schema TSC 与 adaptive schema TSC 消融；
- active conflict reject、soft overlap admit 和合法 replication 的案例审计。

### 晋级门槛

- 相对朴素并行显著降低 spatial collision，提高 unique coverage/verifier；
- 相对串行 SSI 在固定墙钟下改善 time-to-threshold 或最终结果；
- 固定计算量下最佳结果非劣效，额外协调 token/cost 可接受；
- Pi/Codex 均无状态一致性或 host ownership 违规；
- 失败/中断/恢复后 reservation 和 event 可解释；
- 至少两个真实任务族满足主要方向，不能只依赖单个成功例子。

### 失败回退

若 TSC 只降低重复但总结果无收益，保留并行为成本敏感场景 opt-in；若协调开销超过收益，默认使用 P4/P5 串行 SSI；若只有 Pi 达标，则只宣布 Pi 支持，Codex 保持实验状态。

## P8：后续研究，不进入首轮承诺

以下能力必须各自重新立项和实验，不能顺带塞入 P0-P7：

- learned embedding/latent view 与 encoder versioning；
- 跨 run/domain ontology 迁移；
- schema/normalizer 的参数化训练；
- 搜索策略或 controller 自修改；
- OpenCode 和 Claude Code 正式 parity；
- distributed/multi-machine transaction store；
- Pareto/frontier 的更复杂长期 credit assignment。

## 每阶段公共交付物

每个 P 阶段至少交付：

1. 数据模型与状态迁移说明；
2. runtime 单元/并发/故障测试；
3. Pi/Codex 对应阶段所需的 asset contract；
4. API、flow、design、debugging 文档；
5. 预注册实验 manifest；
6. raw metrics + 可重放分析；
7. `advance / iterate / stop / advisory-only` 决策记录；
8. 未达标时的功能默认模式和回退策略。

---

[上一页：研究与实验标准](01-research-protocol.md) | [下一页：Runtime、数据与 API 设计](03-runtime-data-api-design.md)
