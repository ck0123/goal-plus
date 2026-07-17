# 关键实验矩阵

本矩阵把研究问题变成可执行的实验。阈值是首轮预注册建议，不是不可修改的真理；若要调整，必须在读取目标实验结果之前更新 manifest 和 decision record。看到结果后再改阈值，只能作为 exploratory analysis，不能用于阶段晋级。

## 总表

| ID | 阶段 | 问题 | 主要对照 | 主要指标 | 首轮晋级门槛 |
|---|---|---|---|---|---|
| E0 | P0 | 当前 AutoResearch 是否可稳定比较 | B0 重复 run | completion、score variance、cost completeness | deterministic ≥ 95% 完成；真实 host ≥ 90%；成本字段 100% |
| E1 | P1 | Typed plan 是否真实提高实验质量 | B0 vs B1 | field validity、grounding、falsifiability、score | validity ≥ 95%；target grounding ≥ 90%；falsifiable ≥ 80%；结果非劣效 |
| E2 | P2 | declared/realized facts 是否忠实 | 金标事件夹具 | exact match、drift macro F1 | observed facts ≥ 99% exact；drift F1 ≥ 0.85 |
| E3 | P2 | 事件是否永久、幂等、可重放 | rollback/retry/crash matrix | loss、duplicate、broken refs | 正式 invocation 恰好一个 event；0 丢失/重复/断链 |
| E4 | P3 | overlap 是否足以 advisory/enforce | held-out pairwise 金标 | per-view P/R、hard reject precision | advisory precision ≥ 0.85；hard reject precision ≥ 0.95，recall ≥ 0.70 |
| E5 | P3 | compact state 是否决策充分 | current rollup vs raw history vs state | evidence recall、duplicate proposal、ranking regret、tokens | 相同 token 下重要证据 recall ≥ 90%；重复 proposal 相对下降 ≥ 20% |
| E6 | P4 | 串行 SSI 是否有真实搜索价值 | B0/B1/B4 | redundant attempts、best score、cost/coverage | 重复相对下降 ≥ 25%；score 非劣效 5%；cost/unique coverage 下降 ≥ 15% |
| E7 | P5 | schema split/merge 是否恢复已知结构 | frozen vs adaptive on synthetic | held-out combined loss、complexity | combined loss 改善 ≥ 15%，无 event 改写，复杂度预算内 |
| E8 | P5 | 动态 schema 是否改善真实搜索 | B4 vs B5 | decision loss、duplicate、best score、revision utility | 至少 2 个任务中 1 个主要决策指标改善且无 score 劣化；每次 accepted revision held-out 改善 |
| E9 | P6 | admission/evidence 是否可线性化 | randomized interleavings | invariant violations | ≥1,000 interleavings 零违反；重复请求 100% 幂等 |
| E10 | P6 | crash/restart 是否可恢复 | injected crash points | reachable half-state、orphan、reservation | 0 reachable half-state；orphan 可诊断；reservation 显式恢复 |
| E11 | P7 | Pi TSC 是否优于朴素并行 | B4/B6/B7，N=1/2/4 | collision、coverage/verifier、wall time、score | vs B6 collision -30%、coverage/verifier +20%；vs B4 fixed-wall 至少一个主要结果 +20% |
| E12 | P7 | Codex 是否复现同一方法方向 | Codex B4/B6/B7 | 同 E11 + parity invariants | 状态语义/事务零差异；至少两个任务主要效应方向与 Pi 一致 |
| E13 | P7/P8 | 哪一层贡献收益 | full ablation | effect/cost by component | 能把收益归因到 typed/event/state/admission/revision/reservation 中至少一层 |

“score 非劣效 5%”表示方法相对 B0 的配对 normalized score 差值，其 bootstrap 95% CI 下界不低于 `-0.05`。对于越小越好的任务，先统一归一化方向。

## E0：基线可重复性

### 设计

- Tier B 每任务至少 10 个 B0 run；Tier C 每任务至少 5 个；
- Pi 和 Codex 固定 model/provider、reasoning config、budget 和时间窗口；
- 记录 host failure、verifier infrastructure failure、worker timeout、selection failure；
- 分开报告“系统没跑完”和“搜索没改进”。

### 通过解释

完成率门槛不是为了宣称模型稳定，而是确保后续 effect 不被基础设施失败吞没。若 score 方差很大但 completion 稳定，可增加 paired repetitions 和 block randomization；若 completion 不稳定，先修基础设施。

## E1：Typed Intervention 质量

### 金标抽样

每个任务至少抽样 30 个正式计划，两名标注者判断：

- target 是否指向真实可编辑对象；
- intervention 是否足够执行；
- context 是否包含决定有效性的关键条件；
- hypothesis 是否包含机制而非“希望变好”；
- expected observation 是否有明确支持/反驳条件；
- 实际修改能否关联原计划。

### 额外约束

- `unknown` 不算 schema invalid，但要单独报告；
- 自动 normalizer 的质量在 held-out 计划上评估；
- B1 的 normalization token/cost 必须纳入总成本；
- 若结构化减少了尝试数，应判断是消除了低价值尝试还是造成不必要阻塞。

## E2/E3：Event 忠实度与永久性

### 夹具维度

- clean improvement；
- verified regression and rollback；
- compile failure；
- verifier infrastructure failure；
- edit-surface violation；
- no-op diff；
- same invocation retry；
- process restart during immutable object write/state write/HEAD swap；
- child candidate inherited baseline；
- same-candidate redispatch；
- run invalidation race。

### 必须验证的不变量

```text
official executed invocation -> exactly one reachable SearchEvent
SearchEvent -> exact plan/admission/artifact/verifier refs
solution rollback -> event set unchanged
retry same idempotency key -> same event id and state result
replay HEAD parent chain -> same compact state digest
```

不要求未开始执行的 cancelled plan 生成搜索证据；它应保留 admission/cancellation audit，但不进入 completed coverage。

## E4：Overlap 金标

### Pair 类型

至少平衡以下样本：

- literal/exact duplicate；
- paraphrase duplicate；
- same config, different context；
- different code, same mechanism；
- same mechanism, different epistemic question；
- legal refinement；
- required replication；
- interaction test；
- alternative implementation；
- representation change；
- unknown/unclassifiable。

### 分层门槛

- artifact/config deterministic duplicate 可先单独达到高 precision；
- semantic overlap 未达到 `0.95` hard-reject precision 时只能 advisory；
- 每个 view 分别报告，不允许一个整体 F1 掩盖 context/epistemic 误判；
- hard reject 的 false positive 必须人工审计，因为它直接损失探索空间。

## E5：决策充分性与状态压缩

### 三组上下文

1. 当前 `history + research_rollup`；
2. 原始长历史，截断到相同 token budget；
3. 新的 versioned compact SearchState。

给同一 proposal policy 生成下一步计划，并测量：

- 是否重复已完成 event；
- 是否错误避开合法 refinement/interaction；
- 对 plan outcome 的方向/失败类型预测；
- 对候选计划的 pairwise ranking；
- 重要证据召回；
- prompt token 和 latency。

为了避免 evaluator LLM 自洽偏差，最终重复/价值判断使用 E4 金标、真实执行结果和预注册规则。

## E6：串行 SSI 在线 A/B

### 对照

- B0：当前 AutoResearch；
- B1：Typed plan；
- B4-advisory：state + overlap reason，不拒绝 semantic overlap；
- B4-enforced：只拒绝满足 E4 高精度门槛的重复。

### 主要终点

1. `redundant_attempt_rate` 相对 B0 下降至少 25%；
2. `best_normalized_score` 非劣效 margin 5%；
3. `cost_per_unique_footprint` 至少下降 15%。

三个终点必须联合解释：只减少 verifier 次数但最佳结果下降不是成功；只提高 score 但用更多重复计算也不能证明 SSI 机制成立。

### 次要终点

- rebase 后计划的新颖度和收益；
- time-to-first-improvement；
- unknown footprint 比例；
- 合法 refinement/replication 的接受率；
- state/normalization 额外 token 占比，首轮目标不超过总 agent token 的 15%。

## E7/E8：Schema Revision

### 合成任务

构造一个初始 schema 故意把两类 context 合并的任务，例如：同一 intervention 在 `small/large` regime 下 outcome 分布相反。系统应：

1. 从 residual/multimodality 触发 split proposal；
2. 给出候选 split dimension 和证据 refs；
3. 在 held-out events 上验证；
4. 创建新 schema version；
5. 重索引旧 event；
6. 改善重复判断或下一步选择。

同时构造无效 split 和应 merge 的冗余节点，防止系统只会增殖 ontology。

### Combined loss

```text
L = L_outcome
  + alpha * L_redundancy
  + beta * L_decision
  + lambda * schema_complexity
```

权重在实验前冻结。Accepted revision 必须在 held-out 上降低 combined loss；training/replay events 上改善不够。

### 真实任务门槛

E8 不要求所有任务都改善，因为 schema 的任务依赖性较强。但至少需要：

- 一个完整、可复现的正例；
- 一个 revision 被正确拒绝的负例；
- macro average 无明显 score/cost 劣化；
- 每个 accepted revision 有 held-out evidence，而不是只凭 LLM 解释。

## E9/E10：事务与故障注入

### 随机交错操作

- 多个 plan 读取同一 state version；
- accept/reject/rebase；
- candidate id allocation；
- reservation create/release；
- verifier evidence 乱序返回；
- duplicate admission/evidence；
- run invalidation；
- materialization failure；
- process restart。

每个随机 trace 结束后验证：

```text
sum(allocated budget) <= frozen budget
accepted admissions have unique candidate ids
active + released reservations map to accepted admissions
reachable events are unique and immutable
state versions form one parent chain
incumbent references reachable verified evidence
host lifecycle fields are absent from SearchState
```

### Crash points

对每个事务注入：

- before/after immutable object write；
- before/after state file write；
- before/after HEAD swap；
- before/after compatibility `run.json` write。

恢复后允许存在 unreachable orphan object，但必须可诊断、不可被正常 read model 当作已提交状态。

## E11/E12：并行价值

### 方法与并发度

每个任务比较：

- serial SSI，`N=1`；
- naive parallel，`N=2/4`；
- TSC parallel，`N=2/4`；
- 可选 static-schema vs adaptive-schema TSC。

### 固定计算量门槛

相对 naive parallel：

- spatial collision 相对下降至少 30%；
- unique coverage/verifier 提升至少 20%；
- best score 满足 5% 非劣效；
- 额外 coordination token 不超过总 agent token 的 20%，或由更低 verifier/device 成本抵消。

### 固定墙钟门槛

相对 serial SSI，至少一个预注册用户价值指标改善 20%：

- time-to-threshold；
- deadline 内 success rate；
- deadline final normalized score。

同时不能出现明显更差的正确性、promotion survival 或 failure rate。

### Codex parity 的含义

Codex 不需要达到与 Pi 相同绝对 score，因为模型/provider/continuation 机制不同。Parity 要求：

- 数据对象、state version、admission/evidence 语义完全一致；
- host lifecycle 仍在 Codex agent registry；
- 相对其自身 naive/serial baseline，主要效应方向与 Pi 一致；
- 中断和恢复没有 SearchState 破坏。

## E13：完整消融

在最终结论前至少做一次 factorial 或逐层消融：

```text
typed plan
event feedback
static coverage
admission explanation
hard exact duplicate rejection
dynamic revision
active reservation
parallelism
```

目标不是要求每层都提高最终 score，而是明确每层带来哪种价值：可审计、减少重复、提高覆盖、改善结果或降低墙钟。没有可测价值的层应保持实验性或被删除，不能因概念完整而永久增加产品复杂度。

## 实验报告的最小结论格式

每个实验最终只允许四种决策：

| 决策 | 含义 |
|---|---|
| `advance` | 工程/研究/运行三重门均通过，可以进入下一阶段 |
| `iterate` | 假设仍可能成立，但当前实现/测量未达标，保持当前默认模式 |
| `advisory_only` | 表示有审计/提示价值，但不足以影响准入或默认并行 |
| `stop` | 主要假设未获支持，停止该分支，保留已验证前序能力 |

报告必须包含未达标项和失败 run；不得只输出推荐结论。

---

[上一页：Runtime、数据与 API 设计](03-runtime-data-api-design.md) | [下一页：交付拆分与决策门](05-delivery-and-gates.md)
