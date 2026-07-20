# 两种 Agentic Search 范式的差异与共同缺口

Model-Driven Loop 与 Algorithm-Guided Agent Search 都使用 LLM 生成候选并通过 evaluator 获得反馈。两者的本质差异不是是否使用搜索，而是搜索控制权和持久状态位于哪里。

## Model-Driven Loop

模型同时承担 proposal、结果解释、方向选择和停止决策，搜索历史主要通过 context、summary 或 memory 传递。它不要求预先定义完整空间，可以从参数调整切换到算法、数据结构或架构重构，具有较强的语义开放性。

代价是缺少独立、稳定的全局搜索状态：多个 rollout 容易受相同模型先验影响，历史可能被压缩或遗忘，局部反馈也容易主导下一步。因此，它具有语义灵活性，但全局覆盖、去重和预算协调较弱。

## Algorithm-Guided Agent Search

外部算法维护 population、tree、archive 或 program database，并决定从哪个 parent 或节点继续、给不同分支多少预算；LLM 主要在选定上下文中生成 proposal。它能够显式保留多个方向、回溯、复用评价结果并控制 exploration/exploitation。

代价是后续搜索受外部状态强烈影响。Evolve 类方法从 seed 和已有 database 中采样 parent 与 inspiration，早期被保留的可能性会塑造后续 proposal 分布；Agent + MCTS 只能在已生成的 action 上分配访问，有限预算下的早期遗漏或 value 误判会产生较高的子树机会成本。

这些机制形成的是程序性多样性，不保证语义覆盖。不同 candidate、lineage 或 tree path 可能仍然属于同一机制和区域。

## 从 Config Space 到隐式语义空间

传统 GA、MCTS、BO 和 MAP-Elites 能够工作，通常以 candidate、state、action 或 feature 可以被显式编码为前提。固定 config space 限制了可表达的可能性，但相同、距离、邻域和覆盖至少可以在已有坐标中定义。

LLM proposal 扩大了搜索表达能力，可以跨参数、模块和抽象层级生成新方案；同时，空间也变成由模型、prompt、历史和反馈共同定义的隐式语义空间。外部 population、lineage 和 tree 仍能记录候选及其生成关系，却不再提供稳定的语义坐标。

因此，两种范式以不同方式遇到同一个问题：Model-Driven Loop 的空间始终隐含在模型中；Algorithm-Guided Agent Search 有显式控制结构，但该结构不能充分表达正在搜索的语义空间。两者都难以可靠判断重复、正交、覆盖、饱和，以及新 Evidence 是否要求改变当前空间划分。

Search Intelligence Scaling 的目标不是在两种范式之间二选一，而是把模型的语义建模能力与算法的显式全局控制结合起来：用 `Search Evidence` 保存已验证事实，用可修订的 `Search Schema` 表达当前对方向、维度和关系的理解。

参考：[AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)、[OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve)、[Language Agent Tree Search](https://proceedings.mlr.press/v235/zhou24r.html)、[MAP-Elites](https://arxiv.org/abs/1504.04909)。
