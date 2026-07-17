# Goal Plus 搜索图式方案实施总计划

本文档集把《搜索图式诱导：面向开放式智能体优化的非冗余探索与事务化协同》转换为一条可实施、可证伪、可逐步交付的 Goal Plus 路线。它不是“把论文对象一次性全部编码”的大重构计划，而是从当前单 worker、单 incumbent 的 AutoResearch 式循环开始，每一步先产生独立价值，再用研究实验决定是否晋级。

> 当前状态：仅为设计计划，不表示这些能力已经实现。

## 核心结论

推荐顺序是：

1. 先把当前 AutoResearch 循环变成可复现的研究基线。
2. 再加入可证伪的结构化干预计划，但暂不做搜索图式和并行调度。
3. 将每次已执行尝试固化为不可变 `SearchEvent`，解决失败实验随代码回退而“认知丢失”的问题。
4. 在单 worker 下以 shadow/advisory 模式构造版本化搜索状态和多视图足迹，先验证它确实能减少时间重复。
5. 只有静态图式已显示价值后，才加入在线 split/merge/re-index 的搜索图式诱导。
6. 先用确定性并发模拟器验证事务语义，再接 Pi 并行池和 Codex 多 Agent。
7. 并行化只有在固定计算量和固定墙钟两种实验中都体现边际价值后，才成为默认能力。

这条路线中，**代码完成只是工程门槛，不是阶段完成**。每个阶段都必须同时通过：

- 工程门槛：状态、兼容性、故障恢复和单元/系统测试正确；
- 研究门槛：预注册实验达到该阶段的主要效果指标；
- 运行门槛：Pi/Codex 的真实 host 路径能产出可检查的持久化证据。

## 范围决定

### 首批支持

- 共享 Python runtime 保持 host-neutral。
- Pi 作为参考执行 host：先验证单 worker，再验证 pool 并行。
- Codex 作为第二个正式支持 host：先保持串行语义一致，再验证多 Agent。

### 延后支持

- OpenCode 和 Claude Code 不阻塞 P0-P7。
- Learned latent space、跨任务 ontology 迁移、搜索策略自修改不进入首轮关键路径。
- 不新增 runtime-owned wait loop、abort worker API、heartbeat、worker lifecycle 状态或 observation bus。

## 术语边界

本计划中的“AutoResearch 基线”特指：

```text
单 incumbent / 单 worker
  -> 提出一个修改假设
  -> 修改工件
  -> verifier 评估
  -> 保留或回退 solution
  -> 带着历史继续下一次尝试
```

需要特别区分两个计划对象：

| 对象 | 当前/目标职责 | 是否相同 |
|---|---|---|
| `SearchPlan` | 当前 runtime 的轮次、预算、worker policy 和 work order 决策 | 否 |
| `AtomicPlan` | 针对特定 `SearchState` 版本的单项干预事务，包含 Typed IR、声明足迹和准入结果 | 否 |

`SearchPlan` 应继续存在；`AtomicPlan` 是其候选提案进入官方搜索状态之前的新边界，不能把两者粗暴合并。

## 阶段总览

| 阶段 | 能力增量 | 当期独立价值 | 主要研究问题 | 默认模式 |
|---|---|---|---|---|
| P0 | 可复现 AutoResearch 基线与测量协议 | 知道当前系统真实效果、成本和重复率 | 基线是否稳定到足以比较 | legacy |
| P1 | Typed Intervention / 可证伪计划 | 更好的实验纪律、报告与复盘 | 结构化是否提高计划质量且不妨碍搜索 | shadow/advisory |
| P2 | 不可变 `SearchEvent` 账本 | 失败尝试不再随 solution 回退丢失 | 事件能否忠实重建实际搜索 | shadow |
| P3 | 版本化 `SearchState` + 静态多视图投影 | 显式回答“已搜索什么” | 压缩状态能否识别时间重复并支持下一步决策 | shadow -> advisory |
| P4 | 串行 `AtomicPlanAdmission` | 单 worker 也能避免无意义重复 | SSI 是否比现有历史摘要更有用 | advisory -> limited enforce |
| P5 | Schema split/merge/re-index | 空间表示随证据适应新任务 | 动态图式是否优于冻结图式 | advisory |
| P6 | 事务核心与 reservation 模拟 | 并发前先证明状态一致性和恢复性 | 准入/证据提交能否线性化、幂等、恢复 | simulator |
| P7 | Pi/Codex 事务化并行 | 在不重复浪费的前提下降低时间成本 | TSC 是否优于朴素并行和串行 SSI | opt-in -> default by evidence |
| P8 | Latent view、跨任务迁移、其他 hosts | 后续扩展 | 是否值得产品化 | deferred |

P5 不是 P7 的绝对前置条件：如果动态图式研究未通过，但 P4 的冻结/静态图式已可靠降低重复，P6/P7 可以在冻结图式上继续。这样不会让一个高风险研究分支阻断已证实有价值的事务化协同。

## 目标架构

```text
main agent
  -> SearchPlan: 决定一轮预算和候选方向
  -> private proposal
  -> Typed Intervention IR
  -> AtomicPlan(base_state_version, schema_version)
  -> AtomicPlanAdmission
       -> accepted: state commit + candidate id + reservation
       -> rejected: reason + evidence
       -> rebase_required: latest state version
  -> materialize candidate
  -> host launches worker (Pi/Codex)
  -> worker executes in isolated workspace
  -> search_run_verifier
       -> observed facts + realized footprint
       -> immutable SearchEvent
       -> VerifiedEvidenceCommit
       -> new SearchState version
```

Runtime 只拥有官方状态、准入、workspace、verifier、证据和版本提交。Host 仍然拥有 worker 启动、等待、deadline、interrupt 和 native transcript。

## 文档导航

- [当前差距与设计原则](00-current-gap-and-principles.md)：为什么不能直接在当前模型上加几个字段，以及哪些能力可直接复用。
- [研究与实验标准](01-research-protocol.md)：所有阶段共同遵守的基线、指标、统计、数据和晋级协议。
- [分阶段实施路线](02-phased-roadmap.md)：P0-P8 的逐步实现、实验、门槛和失败回退。
- [Runtime、数据与 API 设计](03-runtime-data-api-design.md)：目标对象、持久化事务、兼容 API 和 Pi/Codex 接入点。
- [关键实验矩阵](04-experiment-matrix.md)：E0-E13 的具体对照、样本、指标和初始阈值。
- [交付拆分与决策门](05-delivery-and-gates.md)：工作包、支持矩阵、Definition of Done 和最先执行的任务。

## 最终成功标准

完整方案不能只证明“系统能跑”，而应同时证明：

1. `SearchEvent` 在 solution 回退、candidate 派生、worker redispatch 和 host 恢复后仍不丢失；
2. 紧凑 `SearchState` 在相同 token 预算下比当前 history/research rollup 更能支持重复判断和下一步选择；
3. 串行 SSI 相对当前 AutoResearch 基线显著降低无信息重复，且最优结果不劣化；
4. 动态 schema 的每次修订能在 held-out 事件上改善预测、重复判断或决策损失，而不是只生成更复杂的 ontology；
5. 并行 TSC 的官方状态具备可串行解释、幂等证据提交和 crash recovery；
6. Pi/Codex 并行相对朴素并行提高单位 verifier 的有效覆盖，并相对串行 SSI 提高固定墙钟下的结果；
7. 若某项研究假设失败，前序已验证能力仍可独立保留，不要求回滚为大爆炸式旧架构。

---

[下一页：当前差距与设计原则](00-current-gap-and-principles.md)
