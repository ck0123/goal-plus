# 研究与实验标准

本协议定义 P0-P7 的共同研究标准。任何阶段都不能以“实现完成、测试通过、跑出过一个好结果”作为研究结论。研究单位是完整 run；单次 iteration、单个最佳案例或一次成功 demo 只能作为诊断证据。

## 研究问题与可证伪假设

| 编号 | 主要假设 | 可证伪条件 |
|---|---|---|
| H0 | 当前单 worker AutoResearch 基线足够稳定，可作为后续比较对象 | 同配置重复 run 的失败率或方差过高，无法区分方法效应 |
| H1 | Typed Intervention 能提高计划可执行性、可证伪性和结果可归因性 | 结构化字段大量空泛/错误，或明显降低有效尝试数和最终结果 |
| H2 | 不可变事件账本能在回退、派生和恢复后无损保存搜索事实 | 事件丢失、重复、无法关联实际 Git/verifier 证据，或恢复后状态不一致 |
| H3 | 紧凑 SearchState 比当前 history/research rollup 更接近决策充分统计量 | 在相同 token 预算下，重复判断、下一步选择或 outcome 预测没有改善 |
| H4 | 静态/冻结 schema 下的串行 admission 能减少时间碰撞且不牺牲结果 | 重复率不降、false rejection 过高，或最佳结果显著劣化 |
| H5 | 在线 split/merge/re-index 能改善 held-out 决策损失 | schema 只变复杂，预测/重复判断/计划排序不改善或历史不可比较 |
| H6 | AtomicPlanAdmission 与 VerifiedEvidenceCommit 能在并发和故障下保持线性化、幂等和可恢复 | 出现预算超扣/漏扣、重复 candidate/event、悬挂 reservation 或不可解释版本序列 |
| H7 | TSC 并行比朴素并行提供更高有效覆盖，并比串行 SSI 提供更好墙钟收益 | 相同计算量下覆盖无提升，或相同墙钟下结果/成本无优势 |

## 必须保留的对照组

不能只比较“最终完整系统”和“旧系统”，否则无法知道收益来自哪一层。实验至少保留以下方法变体：

| ID | 变体 | 用途 |
|---|---|---|
| B0 | 当前单 worker AutoResearch：现有 proposal/history/results.tsv | 真实产品基线 |
| B1 | B0 + Typed Intervention，仅结构化，不提供 schema/coverage | 测量实验纪律本身 |
| B2 | B1 + immutable SearchEvent，仅作为记录，不反馈规划 | 测量永久账本本身 |
| B3 | B2 + 静态 schema shadow，不影响行为 | 测量投影和 overlap 质量 |
| B4 | B3 + serial advisory admission | SSI 的最小价值版本 |
| B5 | B4 + dynamic schema revision | 测量诱导和修订的增量 |
| B6 | 当前朴素并行：多个 rollout，无 footprint reservation | 并行基线 |
| B7 | 静态或动态 schema + TSC parallel | 最终并行方案 |

若资源有限，P1-P5 至少做 `B0/B1/B4` 三组；P7 至少做 `B0/B4/B6/B7` 四组。不得用 B7 只对比 B0，因为那会混合状态表示、admission 和并发三个效应。

## 任务分层

### Tier A：确定性语义与状态夹具

用于快速、低成本地验证表示和事务，不依赖模型偶然表现：

- 同一 diff 的不同自然语言描述；
- 相同 target、不同 context；
- 不同 artifact、相同 mechanism；
- 合法 `replication/refinement/interaction_test` 与无信息重复；
- 声明只改参数但实际同时改结构的 drift；
- 故意设置过粗 schema，需要按 context split；
- stale plan、并发 admission、重复 evidence、crash-before/after-HEAD；
- solution reset 后 event 仍存在；
- candidate 派生/redispatch 后 state 和 ledger 可追溯。

Tier A 必须有人工/程序给定的 ground truth，不能用待评估的同一个 LLM 自评。

### Tier B：仓库内便宜 E2E

优先复用现有可运行场景：

- `k_module`：最小 verifier、Git 迭代和 host smoke；
- `circle_packing` two-round：多轮历史、方向切换和候选派生；
- `model-optimize` 或 CPU 可运行的 kernel 示例：多维指标与真实代码修改；
- 人工构造的 schema split/interaction benchmark。

Tier B 用于每个阶段的回归、消融和较高重复次数。

### Tier C：真实开放式优化

至少覆盖两个不同任务族：

- EdgeBench 风格的系统/算法优化；
- kernel、CANNBench/TileLang/AscendC 或 VLIW 长时程优化（按本地硬件和许可选择）；
- 可选的模型训练/编译器优化场景，用于检验 ontology 泛化。

Tier C 负责验证“真实搜索价值”，不能只由 toy benchmark 代替。若硬件不足，应明确标记为研究缺口，而不是用 Tier A/B 宣称完成最终价值验证。

## 实验单位与预算匹配

### 实验单位

- 主要统计单位：完整 `run_id`；
- 配对单位：相同任务、相同 baseline snapshot、相同模型配置、相同预算的不同方法变体；
- iteration 只用于解释重复率、coverage 和 failure mode，不作为独立样本计算显著性。

### 固定因素

每个实验 manifest 必须冻结：

- source commit 与 task/case identity；
- frozen verifier hash；
- host、model/provider、reasoning/thinking 配置；
- prompt/skill/ontology/schema bootstrap 版本；
- `max_candidates`、`max_parallel`、worker runtime/turn budget；
- verifier 调用上限和硬件资源；
- temperature/seed（若 host 可控）；
- state token budget 和 history token budget；
- 并行实验的 fixed-compute 或 fixed-wall-clock 口径。

### 两种并行比较必须分开

1. **固定总计算量**：各方法拥有相同 verifier 次数、token 和设备预算，比较最佳结果、覆盖和重复浪费。
2. **固定墙钟**：各方法拥有相同截止时间和并发上限，比较 time-to-threshold、最终结果和资源成本。

只做第二种会把“多花计算”误当作“协调更智能”；只做第一种又无法证明并行的用户价值。

## 主要指标

### 结果指标

- `best_normalized_score`：按任务预定义尺度归一化的最佳合法结果；
- `success_rate`：达到任务预注册阈值的 run 比例；
- `time_to_first_improvement` 与 `time_to_threshold`；
- `selection_survival`：worker 最佳结果通过 parent final verify/promotion 的比例。

### 搜索效率指标

定义“无信息重复”为：相对 completed events 或 active reservations 高 overlap，且未声明或未证实 `refinement`、`replication`、`interaction_test`、`alternative_implementation` 等合法新增关系的已执行尝试。

```text
redundant_attempt_rate
  = no_new_information_attempts / executed_attempts

unique_coverage_per_verifier
  = accepted_unique_footprints / verifier_calls

cost_per_unique_footprint
  = total_cost / accepted_unique_footprints
```

还应报告：

- 与 completed coverage 的 temporal collision rate；
- 与 active reservation 的 spatial collision rate；
- 被拒绝后 rebase 产生有效新计划的比例；
- `unknown` footprint 比例，防止系统靠过度自信提高表面覆盖。

### 表示质量指标

- Typed IR field validity/completeness；
- observed target/diff/context 的 exact match；
- declared-realized alignment 分类 macro F1；
- pairwise overlap precision/recall，按每个 view 分开报告；
- hard-reject duplicate precision；
- schema 节点内 outcome prediction loss；
- next-plan ranking regret；
- state compression ratio 与重要证据召回率；
- re-index 前后同一事件的 lineage 可追溯率。

### 事务与运行指标

- state version 是否单调、唯一且父链完整；
- candidate id、event id、admission id 是否唯一；
- budget/reservation 守恒；
- evidence commit 幂等率；
- crash recovery 后 orphan object、悬挂 reservation 和重复事件数量；
- Pi/Codex restart/resume 后官方状态一致性；
- host lifecycle 状态是否仍只存在 host 侧。

### 成本指标

- input/output/reasoning token；
- worker 墙钟、verifier 墙钟、设备占用；
- LLM normalization/review/schema revision 的额外调用；
- SearchState 读取 token；
- 每一分 normalized score、每个 unique footprint 和每次成功 run 的成本。

## 人工标注与校准

语义 overlap、mechanism 和 expected observation 不能完全依赖自动评审。建立小型金标集：

1. 从 Tier A 和早期真实 run 分层抽样计划/事件对；
2. 两名标注者独立判断 target、mechanism、context、epistemic overlap 和合法关系；
3. 先计算一致性并解决标签规范问题，再冻结 adjudicated label；
4. 用不同于 proposal/normalizer 的模型作为辅助评审，但不替代金标；
5. hard rejection 阈值只根据 held-out 金标集确定。

标注集必须包含困难负例：文本相似但机制不同、diff 不同但机制相同、相同干预在不同 context 下有效，以及合法 replication/refinement。

## 重复次数与统计报告

初始标准：

- Tier A：每个确定性用例全量运行；随机并发交错至少 1,000 次；
- Tier B：每个方法 × 任务至少 10 个完整 run，资源不允许时不得低于 5；
- Tier C：每个方法 × 任务至少 5 个完整 run，并明确置信区间较宽；
- 并行扩展：每个任务至少比较 concurrency `1/2/4`，超出 4 仅在前述结果显示收益后进行。

统计报告使用：

- 中位数、IQR、均值和 bootstrap 95% CI；
- 配对差值和 effect size，而不是只给 p-value；
- 完整失败率和 failure-class 分布，失败 run 不得静默删除；
- 多任务结论同时给 macro average 和每任务结果；
- 非劣效门槛在实验前冻结。

如果模型 provider 不支持稳定 seed，应使用 block randomization：在同一时间窗口交错运行各方法，避免服务端版本或负载漂移只影响某一组。

## 每个阶段的三重门槛

### 工程门槛

- 模型和状态向后可读；
- deterministic unit/integration tests 通过；
- crash、重试、重复调用和旧 run 读取有明确行为；
- `git diff --check` 和默认测试通过；
- 不破坏 runtime/host ownership。

### 研究门槛

- 预注册主要指标和阈值；
- 对照/消融完整；
- 原始结果和统计脚本可重放；
- 主要指标达到 [实验矩阵](04-experiment-matrix.md) 的阶段门；
- 未达标时记录 `stop/iterate/keep-as-advisory` 决策，不能只解释为“模型波动”。

### 运行门槛

- Pi 真实 E2E 产出预期 `.gp` 工件；
- 对要求 Codex parity 的阶段，Codex 真实 E2E 也必须通过；
- monitor 能在一屏内显示关键状态而无需读 raw transcript；
- host 中断/恢复后无官方状态损坏；
- 原始 host logs/transcripts 留在 ignored 目录，不能提交。

## 研究产物合同

每个正式实验保存：

```text
experiments/<experiment_id>/
  manifest.json          # 预注册配置、主要指标、阈值、case identity
  environment.json       # commit、host/model、依赖、硬件、verifier hashes
  runs.jsonl             # 每个 run 的结果和状态引用
  metrics.json           # 聚合前的机器可读指标
  analysis.py            # 可重放统计与图表
  report.md              # 结论、CI、失败和决策
  decision.json          # advance / iterate / stop / advisory-only
```

大日志、模型 transcript、candidate workspace 和设备产物仍保存在 ignored 外部路径；manifest 只保存稳定引用和 hash。

## 防止结论污染

- 实验开始前冻结主要指标、阈值和排除规则；
- 不在看到最终分数后改变“重复”的定义；
- schema normalizer/reviewer 不得读取 held-out 人工标签；
- 使用标准答案的 QA benchmark 时，gold/scorer 必须对 worker 不可见，且不能用 gold score 做 candidate 内部选择；
- 本方案的主要价值验证优先使用 evaluator-guided optimization，而不是可通过 oracle 反馈反复试答的隐藏答案任务；
- 论文或报告中必须分别陈述工程正确性、表示质量、串行搜索价值和并行扩展价值。

---

[上一页：当前差距与设计原则](00-current-gap-and-principles.md) | [下一页：分阶段实施路线](02-phased-roadmap.md)
