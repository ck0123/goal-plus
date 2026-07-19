# Goal Plus 搜索图式实施计划

本文档集只描述 Goal Plus 搜索图式的产品能力、数据对象、实现依赖和
Pi/Codex 接入边界。它不绑定任务类型、数据集、运行次数或仓库位置；
Goal Plus 可以用于当前仓库内外的任意可验证优化任务。

并行执行的目标控制流程以
[并行 Loop Agent 设计](../parallel-loop-agent-design.md)为准：main agent 只负责
初始任务分配、每次 completion 验收、best-so-far 更新和最终验收；各 subagent
自己完成连续的 AtomicPlan、执行与验证循环。任务未完成且仍有时间时，main 只
resume 原 subagent，不重新判断方向或派发“下一轮”。本文档集中的阶段编号只表示
组件依赖，不应被解释为 main agent 的逐轮 conductor workflow。

## 当前状态

- AtomicPlan 生成 Agent、SearchEvent/SearchState 以及完整 parallel loop-agent 流程均已
  交由外部同事负责；本计划只保留合同和集成边界。
- 统一统计、时间线和自包含 `report.html` 已实现。
- 外部实现新增持久化数据后，再同步接入现有 monitor/statistics/report 链路。
- verifier iteration 已保存 exact Git head、artifact hash 和 score；best artifact 可从
  现有记录确定并由 selection 恢复，不存在额外的 `RunRecord` 持久化待办。
- Pi 已验证跨进程恢复同一 native session 并增量读取 entries；同 PID 双轮原型也已
  验证，但当前产品合同不要求 PID identity，因此不实现 persistent supervisor。

## 实施原则

1. AutoResearch 的单 candidate 循环仍是最小执行单元。
2. runtime 保存可验证事实，Agent 提供计划和解释，两者不能混为一层。
3. `SearchPlan` 只负责初始 candidates；之后由每个 subagent 在同一 candidate
   workspace 内连续提交 `AtomicPlan`。
4. 先建立不可变事件和版本化状态，再让状态影响准入。
5. runtime 不接管 worker 启动、等待、终止、heartbeat 或 host lifecycle。
6. Pi 先接入，Codex 使用同一 runtime 数据语义；OpenCode/Claude 延后。
7. 每增加一种持久化事实，都要同步补充 monitor、统计和 HTML 报告读取。

## 实施顺序

| 阶段 | 能力 | 默认行为 |
|---|---|---|
| P1 | Typed Intervention 与 AtomicPlan 生成合同 | shadow |
| P2 | 不可变 `SearchEvent` 账本 | shadow |
| P3 | 静态 schema、版本化 `SearchState` 和 read models | shadow |
| P4 | 串行 AtomicPlan admission | advisory，确定性规则可 enforce |
| P5 | schema revision、split/merge/re-index | advisory |
| P6 | reservation、幂等提交和 crash recovery | simulator/disabled |
| P7 | Pi/Codex host 集成 | opt-in |
| P8 | latent view、跨任务迁移和其他 hosts | deferred |

P5 不阻塞 P6/P7。动态 schema 未启用时，事务与 host 集成可以使用冻结 schema。

## 目标链路

```text
main agent
  -> freeze spec / create run / launch initial N candidates

each subagent
  -> read SearchState
  -> AtomicPlan proposal
  -> AtomicPlanAdmission
  -> mutate the same candidate workspace
  -> search_run_verifier
  -> VerifiedEvidenceCommit
  -> read the next SearchState version and continue

on each subagent completion
  -> parent verify
  -> keep runtime best-so-far when improved
  -> if global task remains and time allows, resume the same subagent

main agent after global stop
  -> select / parent verify / promote
  -> record Search result / final raw-goal audit / terminal Goal Plus status
  -> generate the final report once
```

Runtime 拥有官方状态、workspace、verifier、事件、版本提交和 promotion；host
拥有 worker 生命周期和原生日志。

## 报告规则

`report.html` 是 `.gp` 持久化状态的最终离线视图，不是独立状态源。Goal
Plus 运行中不生成中间报告；只有记录进入终态后才调用一次
`search_report`。新增
event/schema/state/admission/reservation 字段时，必须同时更新：

- `goal_plus_monitor_snapshot`；
- 统一统计 read model；
- `report.md` / `report.html`；
- unavailable 数据说明。

原 Session 退出后，Agent 仍可用同一 `.gp` root 和 `run_id` 重新调用
`search_report`。不存在的宿主数据不得从 transcript 猜测。

## 文档导航

- [当前差距与设计原则](00-current-gap-and-principles.md)
- [分阶段实施路线](02-phased-roadmap.md)
- [Runtime、数据与 API 设计](03-runtime-data-api-design.md)
- [交付拆分](05-delivery-and-gates.md)
- [并行 Loop Agent 设计](../parallel-loop-agent-design.md)

## 完成条件

- 新记录 strict、versioned、向后可读；
- 不可变对象不能被正常 API 覆盖；
- retry、restart 和 crash recovery 有确定行为；
- Pi/Codex 使用相同 runtime 合同；
- host pool 状态不进入 Search records；
- 新状态可从 monitor、Markdown 和 HTML 报告定位；
- 默认测试和 `git diff --check` 通过。

---

[下一页：当前差距与设计原则](00-current-gap-and-principles.md)
