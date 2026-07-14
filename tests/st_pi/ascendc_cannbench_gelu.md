实现并优化 CANNBench Level1 GELU 的 AscendC Direct Invoke 算子。

以 `{{CANNBENCH_ROOT}}/tasks/level1/gelu` 和 CANNBench 仓库自己的评测实现作为
Semantics、Cases、Tolerance、Golden 与 Score 的权威来源。自行检查仓库并解析当前
Git Commit。不能因为用户已经给出路径，就用临时 allclose 或自定义计时替代官方评测
契约。

在 `{{ST_PI_RUN_ROOT}}/gelu-source` 创建全新的 Source Workspace，完整遵循
`{{PROJECT_ROOT}}/examples/ascendc-direct-search/SPEC_DISCOVERY.md`。从
`{{AKG_ASCENDC_SKILLS_ROOT}}` 物化 AKG 主源 AscendC 知识，并从
`{{CANNBOT_SKILLS_ROOT}}` 物化声明过的 CANNBot 缺口补充。只支持 Direct Invoke；
知识中的工程习惯不能覆盖 CANNBench Task 与评测契约。

每条依赖 NPU 的 Shell Command 都必须先执行由测试 Harness 注入的环境设置：

```bash
{{NPU_ENV_SETUP}}
```

生成并自测任务专用 Verifier，测量非 Candidate Baseline，然后冻结 Oracle、Cases、
Policy、Reference、Platform、Verifier 和 Knowledge。SearchSpec 必须使用：

```json
{
  "budget": {"max_candidates": 2, "max_parallel": 2},
  "strategy": {
    "name": "random",
    "driver": "builtin",
    "worker_host": "pi-rpc",
    "worker_mode": "agent-session-pool",
    "worker_budget": {
      "max_runtime_seconds": 3600,
      "max_turns": 40,
      "on_exceed": "interrupt"
    }
  }
}
```

这是流程级 Smoke，而不是正式全量性能报告。Search Precision 必须覆盖 GELU 的全部
官方 Case；Search Performance 和 Baseline Performance 使用 CANNBench 原始 Case ID
`1,2,6,7`，不修改这些 Case 的 Shape、DType、Attribute、Value Range 或评测语义；
Baseline 与 Candidate Benchmark 使用官方 Runner，固定 `warmup=2`、`repeat=5`。
Promotion Precision 再次覆盖 GELU 的全部官方 Case，Promotion Performance 对同一组
`1,2,6,7` 使用相同的 Baseline、评分公式、`warmup=2` 和 `repeat=5` 重新测量。
Search 与 Promotion 的 Oracle、Case、Tolerance、Metric 和拒绝条件必须相同，
Promotion 只允许增加非门禁诊断。把这个 Smoke 范围明确记录到
`_task/search_policy.json`，不能把它描述成完整 CANNBench 性能结果。

只创建一个包含 `c001`、`c002` 的 Batch，然后只调用一次
`pi_search_run_batch(run_id, ["c001", "c002"], final_verify=true,
max_parallel=2)` 并发运行两个 Pi Candidate。主 Host 和 Candidate 都使用 Pi；禁止调用
OpenCode 或创建 OpenCode Worker。

每个 Candidate 的 Search 精度证据必须包含 `passed_case_ids`、`cases_sha256` 和精确
Candidate `artifact_hash`。所有 Performance Case 都必须包含在 `passed_case_ids` 中；
Benchmark 必须拒绝过期或不完整的精度证据。两个 Candidate 都完成后，选择最优合法
Candidate，运行完整 Promotion Verifier，从不可变 Selected Git Commit 生成 Patch 和
Report，记录 Goal Plus Search Result，按原始目标审计完成度，最后把 Goal Plus 状态设为
`complete`。

Candidate Workspace 对 Verifier 是只读输入。Process 与 Promotion Verifier 每次调用都
必须把 Candidate 复制到唯一的
`$GOAL_PLUS_VERIFIER_TMPDIR/workspace`，只在这个临时副本中完成 Build、Wheel 安装、
`.so` 生成、官方 CANNBench Precision/Performance 和 Report 解析。官方 JSON、Profiler、
Precision Evidence 与 Result 也只能写入本次 `GOAL_PLUS_VERIFIER_TMPDIR`，不能写回
Candidate 根目录、`build/`、`dist/` 或 `_verifier/`。成功时在 stdout 输出完整证据且最后
一行是有限 Metric JSON；失败时在 stdout 输出完整结构化失败证据而不是临时文件路径，
然后非零退出。生成的 Wrapper 使用
`goal_plus.verifier_support.isolated_verifier_workspace` 建立副本；若存在
`GOAL_PLUS_VERIFIER_DIAGNOSTICS_DIR`，把精简的官方 JSON 与失败证据同步到该持久目录。

SearchSpec 中所有使用 NPU 的 `process_verifiers` 和 `promotion_verifiers` 必须配置完全
相同的非空 `resource_lock`（例如 `ascend-npu:<detected-device-id>`），让两个并发 Worker
的 NPU 评测串行执行。Baseline、两个 Candidate、Search 和 Promotion 必须使用同一份
冻结评分输入与聚合方式。Promotion 必须 Clean Build、覆盖全部官方 Precision Case，
并重新执行指定 Performance Case 和正式 Profiler（若官方契约要求）。
