# AscendC Direct Invoke 目标驱动开发方案

英文版见：[ascendc-direct-invoke-development-plan.md](ascendc-direct-invoke-development-plan.md)

## 1. 产品契约

用户唯一入口是 `/goal-plus`。用户只需要提供：

- 算子语义和 API 意图；
- 大致的 Shape、dtype 范围；
- CANNBench、AKG、PyTorch、文档或本地代码等参考提示；
- 如果不是加权耗时，额外说明优化目标。

用户不需要准备任务目录、Verifier、Golden、Case、SearchSpec 或平台清单。
Goal Plus 在 Spec Discovery 阶段生成这些资产，然后进入标准 Search 主流程。

示例：

```text
/goal-plus 实现并优化一个 AscendC Direct Invoke sigmoid 算子。
输入 x 的 rank 为 1 到 4，总元素数大约为 1 到 65536，支持 float16 和
float32；输出 Shape 和 dtype 与 x 相同。语义参考 torch.sigmoid，Case 和
误差阈值参考 CANNBench sigmoid 任务。
```

当前只支持 Direct Invoke。Candidate 在冻结的编辑面内自由选择实现和优化
方法。不接入知识源仓库的 Agent、Plugin、Hook、审批流或嵌套编排。

## 2. Goal Plus 主流程

```text
自然语言目标
  -> goal_plus_create
  -> triage: scenario=ascendc_direct_invoke, phase=spec_discovery
  -> 归一化需求并解析参考来源
  -> 生成工作区、Golden、Cases、Verifier、Baseline、SearchSpec
  -> 自检 Verifier 并冻结完整契约
  -> 标准 Goal Plus Search Candidate
  -> 选择有 Verifier 证据的 Git Revision
  -> Promotion 全量验证
  -> 从不可变 Selected Commit 生成 Patch
  -> Raw-goal Audit
```

AscendC 逻辑属于 Host Skill 和示例资产，不进入领域无关的 Runtime。Runtime
仍然只处理 Source Path、Edit Surface、冻结资产、命令、Pass/Fail、有限数值
Metric、Git Revision 和 Patch。

规范流程以
[`examples/ascendc-direct-search/SPEC_DISCOVERY.md`](../examples/ascendc-direct-search/SPEC_DISCOVERY.md)
为准。

## 3. 职责边界

| 角色 | 职责 |
|---|---|
| 用户 | 描述语义、大致 Shape/dtype 和参考提示。 |
| Main Agent | 解析证据、生成并自检任务契约、测量 Baseline、冻结资产并编排 Search。 |
| Candidate Worker | 只修改允许的 AscendC 文件，使用冻结任务和只读知识实现与优化。 |
| Goal Plus Runtime | 隔离 Candidate、执行 Verifier、记录分数、选择 Git Revision、报告和 Promotion。 |
| Promotion Verifier | 对选中 Revision 执行 Clean Build 和完整验收。 |

## 4. 参考来源解析

每个参考来源必须声明一个或多个角色：

- `semantics`：数学和 API 行为；
- `golden`：可执行正确性 Oracle 的证据；
- `cases`：Shape、dtype、属性、数据范围和权重；
- `tolerances`：数值误差标准；
- `baseline`：可测量的非 Candidate 实现；
- `implementation`：仅提供实现思路。

Main Agent 记录仓库 URL、精确 Commit、选中文件、哈希、角色和转换过程。
CANNBench 可以提供 Golden 和 Case；AKG 可以提供实现、测试和语义证据；
PyTorch 可以作为 Golden 和 Baseline。任何来源都不能自动获得其文件无法证明
的角色。

Baseline 不要求是 AscendC。用户提供的 Golden 或其他独立可执行参考都可以
作为 Baseline；Candidate 不能作为自己的 Oracle 或 Baseline。

## 5. 动态生成的任务资产

Spec Discovery 在 Source-owned Workspace 中生成：

```text
operator/                         # 从 Direct Invoke 模板派生的源码
_task/operator_request.json       # 归一化用户契约
_task/reference_manifest.json     # 固定版本的证据和哈希
_task/target_platform.json        # SoC/CANN/torch/torch_npu 身份
_task/search_policy.json          # Cases、Metric、测量和 Promotion 策略
_task/baseline.json               # 参考实现性能
_task/verifier_readiness.json     # Checker 自检结果
_oracle/reference.py              # 独立 Golden
_oracle/cases.jsonl               # 稳定 Case ID 和来源
_oracle/tolerances.json           # 显式 dtype 误差策略
_verifier/                        # 针对当前任务生成的入口
_skills/                          # 固定版本、以 AKG 为主源的只读知识
```

仓库不提供固定 Task Preparer 或通用 AscendC Verifier。Host Agent 根据仓库中的
Request Schema、Source Template、Knowledge Selection、动态物化器和规范文档，
为每个目标生成具体任务资产。

## 6. Direct Invoke v1 边界

进入 Search 前必须满足：

- 只有一个 Tensor 输出；
- Schema 第一个参数是非 Optional Tensor；
- 输出 Shape 和 dtype 关系明确；
- 可以通过 PyTorch NPU Extension 暴露 Direct Invoke 接口；
- 所有具体 dtype 和有界代表 Shape 已确定。

Broadcast、Reduction、Matmul、动态输出或多输出需要单独定义 Scaffold Profile，
不能被强行套入 v1 Shape-preserving 模板。

## 7. 精度与性能证据

所有 Performance Case 必须先通过 Search 精度。Precision Evidence 必须绑定：

- 稳定的 Passed Case IDs；
- 完整 Cases 文件 SHA-256；
- 精确 Built Candidate Artifact Hash。

Benchmark 必须拒绝缺失、过期、覆盖不完整或不匹配的精度证据。正确性检查覆盖
输出数量、Shape、dtype、Device、超过 `atol`/`rtol` 的普通有限浮点误差、适用
时的整数误差、NaN 位置、Inf 位置和 Inf 符号。

生成的 Checker 在测量 Baseline 前，必须用 Positive Control 和上述 Negative
Controls 证明自身有效。Source Workspace 的 Ranking Command 成功并输出有限
Metric 后，才能开始 Search。这个 Source Workspace 是最小正确 Seed：主 Agent
必须让它 Build 成功并通过全部共享正确性 Case，但性能优化由 Candidate Worker
负责。非 Candidate Baseline 是独立的对比输入，不是 Seed。

Search 与 Promotion 执行同一份冻结验收契约，包括相同的正确性与性能 Case ID、
Oracle、Tolerance、评分元数据、测量协议、聚合方式、Metric 方向和拒绝阈值。
Promotion 只是在 Clean Build 上独立重新执行并获取 Fresh Measurement，不能增加
Search 没有执行过的新门禁。

Metric 优先级依次为用户明确指定的评分、选中 Reference 提供的可执行评分契约、
默认评分。Reference 评分需要适配为有限 Metric，并为 Baseline 和所有 Candidate
使用同一份冻结 Scorer 与对比基准。没有用户或 Reference 评分时，所有正确性
Case 必须通过，并使用有限的 `weighted_latency_us`、方向 `minimize` 进行排名。

## 8. Knowledge 边界

`knowledge.sources.json` 以 AKG Commit
`a2c1a23fd371e234b7e767247e8c4753462ecdca` 中整理后的 AscendC Skill Tree 为
主源，只展开 `SKILL.md` 和 `reference/`、`references/` 下的 Markdown。输入路径
既可以是 AKG 仓库根目录，也可以直接是
`akg_agents/python/akg_agents/op/resources/skills/ascendc` 子目录。

AKG 当前覆盖 Direct Invoke、常用 API/Pattern、Elementwise、Broadcast、
Reduction、性能优化和调试。对于尚未覆盖的 Architecture、Matmul、Cube-Vector
融合、SIMT、Attention、Sort 和 Conversion，选择文件只从 CANNBot Commit
`d5ddcacc6e51eeaa8b52fa446c3b768c6813602e` 引入显式补充清单。物化器从两个
Commit 的 Git Object Database 读取文件，不读取 Live Working Tree。

每个任务创建时，物化器删除编排和不安全指令，重写或拒绝依赖，分别保留 Apache
2.0 与 CANN OSL 2.0 License，并把 Source Role、Resolved Commit、Materializer
Hash、Source Blob、Source Hash、Rendered Hash 和转换审计写入 Manifest。只有
生成后的只读 `_skills/` 会进入 Candidate Workspace。冻结的 Task 和 Reference
Contract 优先级更高；知识不能定义语义、Cases、Tolerance、Score、Edit Surface
或工作流。

## 9. 环境

所有依赖 NPU 环境的发现和验证命令先执行：

```bash
source "${GOAL_PLUS_NPU_CONDA_SH:?set GOAL_PLUS_NPU_CONDA_SH}"
source "${GOAL_PLUS_NPU_ENV_SH:?set GOAL_PLUS_NPU_ENV_SH}"
```

检测到的 Target 信息写入并冻结在任务资产中，不作为用户需要填写的命令行参数。

## 10. 验收标准

- 四个 Host 的 `goal-plus` Skill 都把 `ascendc_direct_invoke` 路由到规范文档；
- 仓库不再包含 Task Preparer、固定 AscendC Verifier 或旧 Profile 兼容文件；
- Template、Knowledge Selection、物化器和生成的 Provenance 内部一致；
- 生成的 Checker 包含所有必要 Negative Controls；
- Performance Case 全部有精度覆盖并绑定证据；
- Search 与 Promotion 执行相同的冻结验收和评分契约；
- 只有 Candidate 实现文件可编辑；
- Promotion Patch 从不可变 Selected Git Commit 导出；
- 仓库单元测试和 `git diff --check` 通过；
- 用户提供具体算子目标后，使用真实 `/goal-plus` 完成 NPU Smoke。
