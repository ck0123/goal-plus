# 交付拆分与决策门

本页把阶段路线拆成可单独 review、测试和回退的工作包，并规定 host 支持与 Definition of Done。推荐每个工作包保持窄范围，不把数据模型、并发、动态图式和 host 资产混进一个大提交。

## 工作包顺序

### WP0：Baseline & Experiment Harness

- 冻结 Tier A/B/C manifests；
- 导出当前 run/iteration/results/research rollup 指标；
- 建立统计脚本和 report/decision 模板；
- 完成 Pi/Codex 串行基线；
- 产出 E0 和人工标签规范 pilot。

**完成后价值：** 任何后续改动都有真实可比基线。

### WP1：Typed Intervention Models

- `TypedInterventionIR` 和 provenance；
- CandidateProposal 兼容字段；
- deterministic validation；
- shadow normalizer；
- Pi/Codex prompt/asset contract；
- E1。

**完成后价值：** 即使不做 SSI，也获得可证伪实验记录和更强报告。

### WP2：Canonical SearchEvent

- event model、invocation id、immutable storage；
- `run_verifier` evidence extraction；
- iteration/event/results.tsv 关联；
- event list/replay/monitor；
- E2/E3。

**完成后价值：** solution 回退不再导致认知回退。

### WP3：Static Schema & SearchState Shadow

- schema v1、footprint、projection；
- state commit/HEAD/read models；
- current history vs compact state 实验；
- shadow overlap；
- E4/E5。

**完成后价值：** 可审计的“已搜索什么”与重复诊断。

### WP4：Serial Admission

- AtomicPlan、Admission、rebase；
- `SearchPlan`/AtomicPlan 明确分层；
- compatibility `start_batch`；
- advisory 和 limited enforcement；
- Pi/Codex real serial SSI；
- E6。

**完成后价值：** 单 worker 减少时间碰撞，这是第一版可称为 SSI MVP 的产品节点。

### WP5：Adaptive Schema

- revision triggers/proposals/decisions；
- held-out evaluation；
- split/merge/re-index/lineage；
- schema complexity budget；
- E7/E8。

**完成后价值：** 空间表示不再完全依赖预定义 scenario schema。

### WP6：Transactional Core

- active reservation 和 optimistic concurrency；
- immutable commit/HEAD fault injection；
- idempotent admission/evidence；
- recovery diagnostics；
- randomized interleavings；
- E9/E10。

**完成后价值：** 可证明的并发状态正确性，但尚不宣称 Agent 并行收益。

### WP7：Pi Parallel

- pool 只执行 admitted tasks；
- wait-any -> evidence commit -> replan；
- stale/rebase 和 recovery；
- N=1/2/4；
- E11。

**完成后价值：** 第一条被研究验证的 TSC 真实 host 路径。

### WP8：Codex Parallel

- Codex skill/agent contract；
- spawn/wait-any/followup/interrupt 与 reservation/state 边界；
- continuation/redispatch 语义；
- E12。

**完成后价值：** 第二 host 复现，证明不是 Pi pool 特例。

### WP9：Ablation & Default Decision

- E13 完整消融；
- 成本和 failure-class 汇总；
- 决定默认 `disabled/shadow/advisory/enforced`；
- 决定 Pi/Codex parallel 是 experimental、opt-in 还是 default；
- 更新主 design/flow/api/debugging/README。

## 推荐的提交边界

每个 WP 建议进一步拆成：

1. 模型与纯函数；
2. 持久化与恢复；
3. runtime lifecycle 集成；
4. read APIs/monitor；
5. Pi assets；
6. Codex assets；
7. benchmark harness 与实验报告。

不要把实验原始大数据、`.gp/`、candidate workspaces、host logs 或 transcript 提交到仓库。只提交小型 manifest、汇总结果、分析脚本和必要金标夹具。

## Host 支持矩阵

| 阶段 | Runtime | Pi | Codex | OpenCode | Claude Code |
|---|---|---|---|---|---|
| P0 | 当前行为 | real baseline | real baseline | existing only | existing only |
| P1 | typed/shadow | real E2E | real E2E | legacy disabled | legacy disabled |
| P2 | event/shadow | real E2E | asset + focused real E2E | event-compatible, no new asset promise | event-compatible, no new asset promise |
| P3 | state/shadow/advisory | reference | parity | disabled | disabled |
| P4 | serial admission | stable | stable | unsupported strict mode | unsupported strict mode |
| P5 | adaptive schema | stable if E8 | stable if E8 | unsupported | unsupported |
| P6 | simulator only | no real parallel requirement | no real parallel requirement | unsupported | unsupported |
| P7 | TSC | parallel candidate | parallel candidate | deferred | deferred |

“unsupported strict mode”不意味着 runtime 文件不可读；它表示没有完整 asset contract 和真实 smoke，因此不能宣称该 host 支持 SSI/TSC enforcement。

## Feature 默认值决策

| 研究状态 | 默认模式 |
|---|---|
| 尚未跑实验 | `disabled` 或 `shadow` |
| 表示质量达标、搜索价值未达标 | `advisory`，不拒绝 |
| exact duplicate 高精度达标 | 只 enforce deterministic exact duplicate |
| serial SSI E6 达标 | Pi/Codex serial 可 `advisory` 或 limited `enforced` |
| dynamic schema E8 未达标 | 冻结 schema，revision proposal 离线 |
| transaction E9/E10 达标但 parallel E11 未达标 | reservation/parallel 保持 experimental |
| Pi E11 达标、Codex E12 未达标 | 只宣布 Pi parallel 支持 |
| E11/E12 与成本门都达标 | 才评估将 TSC parallel 设为默认 |

## 阶段决策门

### D0：是否拥有可信基线

输入：E0、成本完整性、人工标签 pilot。

- `advance`：进入 Typed Intervention；
- `iterate`：修 verifier/host/harness；
- 不允许跳过。

### D1：Typed Intervention 是否值得保留

输入：E1。

- 若提高实验质量且结果非劣效：保留为正式协议；
- 若只有报告价值：保留 optional/advisory；
- 若高开销且低 grounding：缩减字段或停用 normalizer。

### D2：Event 是否成为 canonical 事实层

输入：E2/E3。

- 只有 0 丢失/重复/断链后，event 才能驱动 SearchState；
- 否则继续 shadow，`IterationRecord` 仍为现有权威兼容记录。

### D3：SearchState 是否进入 worker/main context

输入：E4/E5。

- state 决策价值达标：进入 advisory；
- 只有审计价值：保留 monitor/research view；
- semantic hard reject precision 未达标：禁止 enforcement。

### D4：Serial SSI 是否成为产品 MVP

输入：E6。

- 重复下降 + score 非劣效 + cost 改善：可宣布 SSI serial MVP；
- 只减少重复但伤害 score：advisory-only；
- 无效果：停止 admission enforcement，保留 event/state 审计。

### D5：Dynamic Schema 是否在线启用

输入：E7/E8。

- held-out 决策损失改善：允许受控 apply；
- 只有合成任务有效：真实任务保持 frozen schema；
- complexity 爆炸：停止自动 revision。

### D6：是否允许真实并行

输入：E9/E10。

- 任一事务不变量失败：P7 hard block；
- 全部通过：从 Pi `max_parallel=2` 开始。

### D7：并行是否成为默认

输入：E11/E12/E13。

- 必须同时有 fixed-compute 覆盖收益和 fixed-wall 用户价值；
- 只跑得更快但多花大量计算：保留 opt-in；
- 只 Pi 达标：默认支持范围只写 Pi。

## Definition of Done

一个阶段只有满足以下全部条件才标记完成：

### 数据与兼容

- 新记录是 strict、versioned、向后可读；
- 旧 spec/run 的行为有测试；
- immutable facts 不可被正常 API 覆盖；
- state/schema/event refs 可从 monitor 和 debug 工具定位。

### 正确性

- focused tests、默认 `python -m pytest -q`、`git diff --check` 通过；
- concurrency/crash 阶段有 fault injection，不只 happy path；
- verifier invalidation、selection、promotion 仍受现有 fence/invariant 保护；
- host pool state 未进入 Search records。

### 研究

- 实验在运行前有 manifest；
- 对照、消融和预算匹配完整；
- raw metrics 与分析可重放；
- 阶段阈值达标，或明确记录 `iterate/advisory_only/stop`；
- 失败 run 和 negative result 被报告。

### Host 与文档

- 本阶段要求的 Pi/Codex real E2E 已完成；
- skill/prompt/tool schema 与 runtime contract 同步；
- design、flow、api、debugging 和 monitor 输出更新；
- OpenCode/Claude 的支持状态没有被夸大。

## 第一批立即执行任务

建议现在不要开始 reservation 或 parallel pool 改造。第一批只做 P0 + P1 的最小闭环：

1. 在 `docs/search-space-schema/experiments/` 或独立 benchmark 目录定义 E0 manifest/schema。
2. 固定三个便宜任务：`k_module`、`circle_packing`、一个 CPU model/kernel 场景；登记一个 Tier C 待运行任务。
3. 跑当前 Pi/Codex 单 worker 基线，产出 run-level score/cost/failure/重复数据。
4. 从基线 run 抽取 100-200 对 plan/event，完成第一版 overlap/relation 金标规范。
5. 只在 `models.py` 增加 P1 typed models 和 legacy defaults，不碰并发。
6. 让 Pi worker shadow 生成 Typed Intervention；runtime 校验并保存，但不影响 materialization/verifier。
7. 跑 E1；若达标，再做 Codex 同语义 E2E。
8. D1 review 后才开始 WP2 `SearchEvent`。

这批工作本身就能回答两个关键问题：当前系统到底有多少重复，以及结构化预注册是否真的让普通 AutoResearch 更好。只有答案为正，才值得继续投入 SearchState 和事务化并行。

## 最终交付清单

- [ ] P0 基线与实验 harness
- [ ] P1 Typed Intervention + E1
- [ ] P2 immutable SearchEvent + E2/E3
- [ ] P3 static SearchState shadow/advisory + E4/E5
- [ ] P4 serial SSI + E6
- [ ] P5 adaptive schema 或明确 frozen-schema 决策 + E7/E8
- [ ] P6 transaction simulator + E9/E10
- [ ] P7 Pi parallel + E11
- [ ] P7 Codex parallel + E12
- [ ] E13 消融和默认模式决策
- [ ] 主文档与 host support matrix 更新
- [ ] negative results、fallback 和 deferred scope 归档

---

[上一页：关键实验矩阵](04-experiment-matrix.md) | [返回总计划](README.md)
