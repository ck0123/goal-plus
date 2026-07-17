# 面向开放式 Agentic Search 的搜索图式诱导与事务化协调

## 背景、核心矛盾、方案设想与框架设计

## 0. 核心摘要

当前 Agentic Search 大致存在两种范式：

1. **模型主导的循环搜索**：依赖强模型在工具和验证器支持下反复提出、执行和改进方案。
2. **算法引导的智能体搜索**：将 LLM 作为 proposal generator，使用进化、MCTS、UCB、模拟退火或 Quality-Diversity 等算法组织探索。

前者开放、通用，但多个 rollout 在相同模型、prompt、harness 和初始状态下容易高度相关；增加并发往往增加的是尝试数量，而不是有效覆盖。后者能够显式维持分支和多样性，但依赖人预先定义搜索空间、节点、行为描述符、距离函数和搜索算子。

二者背后存在一个共同缺失：

> **系统没有持续构造一个显式、共享、可修订的模型，用来表示已经搜索过什么、当前如何理解搜索空间，以及哪些区别对下一步决策真正重要。**

因此，我们设想的第三种路径不是再设计一个更复杂的搜索控制器，而是引入两个核心机制：

### 1. Search Schema Induction：搜索图式诱导

使用一套跨场景通用的干预元语法，由 LLM 根据具体任务诱导领域本体和运行时搜索图式；每次搜索尝试被编译为结构化、多视图的 Search Footprint。空间表示不是预先固定的，也不是 LLM 一次性生成的，而是随着实验结果不断被拆分、合并和修订。

### 2. Transactional Search Coordination：事务化搜索协调

多个 Agent 可以并行生成方案，但方案不能直接执行。每个方案必须以 `AtomicPlan` 的形式，针对某一版本的共享 Search State 提交；只有经过审核、冲突检测和原子预留后才能进入执行。执行结果经 verifier 验证后，再通过独立的 `EvidenceCommit` 原子写入共享状态。

整体原则可以压缩为：

> **Agents speculate; plans commit; evidence accumulates; schemas revise.**

中文即：

> **Agent 可以自由猜想，计划必须原子提交，证据持续积累，空间图式不断修订。**

---

# 1. 背景

## 1.1 问题范围

这里讨论的对象可以统一称为：

# Evaluator-Guided Agentic Optimization

# 评价器引导的智能体优化

系统中存在：

* 一个可修改的对象，例如代码、模型配置、算法、kernel 或研究工件；
* 一个能够提出和执行修改的 Agent；
* 一个可重复运行的 evaluator 或 verifier；
* 一个有限的搜索预算；
* 一个希望被最大化或最小化的目标。

基本循环是：

$$
\text{Proposal}
\rightarrow
\text{Execution}
\rightarrow
\text{Evaluation}
\rightarrow
\text{Revision}
$$

这类任务的关键特点是：

1. 解空间很大，往往不能被提前完整枚举；
2. 方案之间具有复杂的上下文依赖；
3. 评价器可以判断结果，但通常不能直接提供梯度；
4. 搜索过程中会持续产生新的假设、变量和表示方式。

---

## 1.2 当前两种主要范式

### 范式一：Model-Driven Loop

基本假设是：

> 随着基础模型能力增强，只要提供足够好的工具、环境和反馈，模型便可以自主发现有效改进。

形式上可以表示为：

$$
x_{t+1} = \pi_\theta(x_t, H_t, R_t)
$$

其中：

* $x_t$ 是当前 solution；
* $H_t$ 是搜索历史；
* $R_t$ 是评价反馈；
* $\pi_\theta$ 是模型隐式承担的搜索策略。

这种方法的优势是开放和通用，不需要人为提前定义完整搜索空间。

但它通常将多个职责同时交给同一个模型：

* 生成候选；
* 解释结果；
* 判断失败原因；
* 决定下一步；
* 判断是否应该换方向。

模型能力增强会提高这些行为的平均质量，但不意味着模型自然拥有良好的全局搜索动力学。

---

### 范式二：Algorithm-Guided Agent Search

这类方法将 LLM 放入显式搜索算法中：

$$
x_{t+1} =
\operatorname{SearchAlgorithm}
\left(
\mathcal X_t,
\operatorname{LLMProposal}
\right)
$$

LLM 负责：

* mutation；
* crossover；
* hypothesis generation；
* 局部修改；
* 语义重组。

外部算法负责：

* parent selection；
* exploration/exploitation；
* population management；
* tree expansion；
* budget allocation；
* diversity preservation。

这种方案能够人为施加探索结构，但代价是必须先回答：

* 搜索对象如何表示？
* 什么算同一个节点？
* 什么算不同路径？
* mutation 修改什么？
* 两个 candidate 的距离是什么？
* 哪些维度用于维护 diversity？
* 什么时候应该拆分或合并区域？

因此，它在显式配置空间中容易实现，在开放式代码、研究和系统优化任务中则高度依赖专家设计。

---

## 1.3 当前项目所处的位置

当前 `agentic-any-search-mcp` 已经形成了一个适合承载该研究问题的基础运行时：

* runtime 持有 durable state、candidate workspace、verifier、score history、report 和 promotion artifact；
* host agent 负责启动和管理前台 worker；
* 不同搜索策略通过统一 runtime 接口产生 plan 和 candidate；
* 每个 candidate 在隔离 workspace 中执行；
* verifier 是官方结果来源。

当前架构有意不把 runtime 设计成 worker supervisor。Host adapter 只负责将统一的 session 概念转换为 OpenCode、Codex 或 Claude Code 的前台 Agent 调用，workspace、评分、预算和报告仍保持 host-neutral。

这条边界是合理的，也应当继续保留。

当前缺失的不是更多 lifecycle API，而是：

> runtime 当前主要维护的是 candidate history，而不是 search-space history。

具体来说：

* `SearchPlan` 是一次 batch 的快照；
* `IterationRecord` 记录 score、failure、changed files 和 metrics；
* 当前没有独立的 proposal admission 或 evidence submit 阶段；
* `max_parallel` 只是 planning hint，并不是并发冲突控制。

同时，subagent 当前无法直接读取其他 candidate 的代码，只能看到候选摘要、分数、指标和 changed-file 名称；subagent 之间也没有直接通信渠道。跨 candidate 的学习主要依赖下一次 `plan_next`。

因此，当前系统已经拥有：

* 可验证执行；
* 可持久化候选；
* workspace 隔离；
* 策略扩展边界；

但尚未拥有：

* 显式共享的空间表示；
* 方案的原子准入；
* 并发搜索范围预留；
* 可修订的 coverage state；
* 经过验证的 evidence commit。

---

# 2. 核心矛盾

## 2.1 Model Intelligence 不等于 Search Intelligence

这里不是说当前模型能力没有增强。

更准确的说法是：

> **模型平均解决问题的能力越来越强，但把模型直接重复调用，并不保证搜索空间覆盖能力同比增强。**

一个强模型通常拥有更好的：

* 代码生成能力；
* 分析能力；
* 局部修改能力；
* 结果解释能力；
* 错误修复能力。

但多个调用共享同一个模型先验。

当它们看到：

* 相同 baseline；
* 相同 prompt；
* 相同工具；
* 相同 verifier；
* 相似历史；

它们产生的搜索方向往往具有较高相关性。

因此：

$$
N \times \text{rollout}
\not\Rightarrow
N \times \text{effective coverage}
$$

增加的可能只是采样数量，而不是新增信息。

---

## 2.2 并发数量与边际覆盖之间的矛盾

理想情况下，启动 $N$ 个并发 worker，应当让它们探索 $N$ 个不同且有价值的区域。

但在没有共享搜索状态时：

$$
p_i
\sim
\pi_\theta(\cdot \mid x, H)
$$

多个 worker 都从相同的 $x$、$H$ 和模型 prior 出发，因此可能同时尝试：

* 相同参数的不同数值；
* 同一机制的不同代码表达；
* 同一假设下的小变体；
* 语义上不同、实际效果相同的修改。

这会产生两类碰撞：

### Spatial Collision

多个并发 rollout 同时探索相似区域。

### Temporal Collision

单个 loop 在不同时间反复尝试语义近似的方向。

因此，并发和单链不是两个不同的问题。

> **并发重复是在空间上发生的碰撞；单链重复是在时间上发生的碰撞。**

两者的根因都是：系统没有一个持续更新的共享 Search State。

---

## 2.3 开放性与可表示性之间的矛盾

Model-driven loop 的优势是无需预先定义空间。

但没有空间表示，系统便难以判断：

* 当前方案和之前方案是否重复；
* 某个方向是否已经饱和；
* 哪些区域完全没有被探索；
* 哪些失败已经反驳了某个假设；
* 新增计算到底贡献了什么。

传统搜索算法解决了这个问题，但采用的是人工结构：

* MCTS 使用树；
* Evolution 使用 population 和 lineage；
* Bayesian Optimization 使用 surrogate posterior；
* MAP-Elites 使用预定义行为网格。

这些结构稳定，但其有效性依赖于人是否正确选择了表示。

当前项目中的 OpenEvolve 风格策略已经能够维护 parent、archive 和 inspiration，但刻意没有实现 MAP-Elites feature cells、novelty judging 和完整 population database。

这恰好说明：

> 真正困难的不是写一个 archive，而是定义 archive 中“不同”究竟意味着什么。

---

## 2.4 自然语言表达能力与空间可比性之间的矛盾

Agent 可以说：

> “基于当前实现，将参数 B 修改为 128。”

但这句话隐藏了大量上下文：

* B 原来是多少；
* B 属于什么组件；
* 为什么修改它；
* 是否同时改变了其他逻辑；
* 当前硬件、shape 和 dtype 是什么；
* 预期解决什么瓶颈；
* 实际执行行为发生了什么变化。

因此，方案的真实身份不是一段文本，而是：

$$
e_i =
\left(
\text{base},
\text{actual intervention},
\text{context},
\text{execution trace},
\text{outcome}
\right)
$$

自然语言只是对它的有损描述。

更困难的是，搜索过程会改变对空间的理解。

例如，早期可以把一组方案统称为：

```text
matmul tiling
```

随着研究深入，可能必须拆分为：

```text
shape regime × tile size × memory level × pipeline × thread mapping
```

所以不存在一个稳定的：

$$
\text{text}
\rightarrow
\text{unique permanent node}
$$

映射。

空间节点必须允许被重新解释。

---

## 2.5 Meta-Agent 与递归偏置之间的矛盾

一种直觉方案是：

> 让一个更高层的 Agent 决定使用 EA、MCTS、UCB 还是其他搜索策略。

但 Meta-Agent 本身仍来自某个模型 prior。

它可能反复偏好：

* reflection；
* debate；
* tree search；
* evolutionary search；
* critic；
* multi-agent decomposition。

因此，Meta-Agent 不会自动消除偏置，只会把偏置从：

```text
solution space
```

移动到：

```text
search-strategy space
```

真正的问题不是如何找到一个“无偏搜索器”。

任何有效搜索都必须利用某种归纳偏置。

更合理的目标是：

> **让偏置被外化、被证据检验、可以修订，而不是永远隐含在模型参数或人工算法中。**

---

## 2.6 Solution State 与 Search State 之间的矛盾

当前很多 loop 只更新当前最优解：

$$
x_{t+1} =
\begin{cases}
x'_t, & R(x'_t) > R(x_t), \\
x_t, & \text{otherwise}.
\end{cases}
$$

失败方案被 reset 后，solution 回到了原处。

但失败实验通常仍然提供信息：

* 某个机制可能无效；
* 某个参数范围已经测试；
* 某个 hypothesis 被反驳；
* 某个实现方式导致资源溢出；
* 某个方向只有在特定 context 下有效。

因此应该有两个状态：

$$
X_t=\text{Solution State}
$$

$$
S_t=\text{Search State}
$$

即使：

$$
X_{t+1}=X_t
$$

也应当有：

$$
S_{t+1}\neq S_t
$$

核心原则是：

> **Solution state 可以回退，Search state 不应该发生认知回退。**

---

# 3. 我们的方案设想

## 3.1 核心判断

我们不是要设计一种新的固定搜索算法，也不是要让 Agent 自由生成一个不可审计的搜索空间。

我们的核心设想是：

> **系统从实际干预及其结果中，在线诱导一个任务特定、版本化、可修订、对下一步搜索决策足够充分的空间表示。**

每次搜索应当同时产生两个结果：

$$
\text{better or rejected solution}
$$

以及：

$$
\text{better model of the search space}
$$

因此，“可积累的搜索智能”在第一阶段不需要训练模型权重。

它表现为：

> 一个任务内、非参数化、由证据持续更新的 Search State。

---

## 3.2 Search State 的定义

共享 Search State 可以表示为：

$$
S_t =
\left(
I_t,
\mathcal E_t,
\Omega_t,
C_t,
A_t,
H_t
\right)
$$

其中：

* $I_t$：当前 incumbent 或优秀 candidate 集合；
* $\mathcal E_t$：不可修改的 Search Event 账本；
* $\Omega_t$：当前版本的 Search Schema；
* $C_t$：已完成的 coverage；
* $A_t$：当前 active reservations；
* $H_t$：当前假设、支持证据、反证和不确定性。

Search State 不是：

* 完整聊天历史；
* 原始 rollout transcript；
* 一份不断增长的 `plans.md`；
* 当前 Agent 的自由反思。

它应当是：

> **对完整搜索历史的有界、结构化、可证伪压缩。**

---

## 3.3 Search Schema Induction

### 3.3.1 一套通用 Meta-Grammar

不同场景不应各自拥有完全不同的 meta-template。

跨场景应共享一套通用的干预语法：

$$
P =
\left(
B,T,I,C,H,O
\right)
$$

其中：

| 字段                     | 含义                  |
| ---------------------- | ------------------- |
| Base                   | 方案基于哪个状态或 candidate |
| Target                 | 准备改变哪个对象            |
| Intervention           | 具体实施什么改变            |
| Context                | 改变在哪些环境和条件下成立       |
| Hypothesis / Mechanism | 为什么认为它可能有效          |
| Expected Observation   | 什么结果会支持或反驳该判断       |

执行完成后再补充：

* Actual Intervention；
* Execution Trace；
* Verified Outcome。

这套结构在 kernel optimization、模型训练优化、RAG、算法发现和系统调优中都可以保持一致。

---

### 3.3.2 Domain Ontology 是场景相关的

不同领域变化的是 ontology，而不是最上层元语法。

Kernel optimization 可能包含：

```text
target:
tiling / memory layout / thread mapping / pipeline / vectorization

context:
hardware / dtype / shape regime / compiler

mechanism:
data reuse / occupancy / memory coalescing / utilization
```

模型训练优化可能包含：

```text
target:
architecture / optimizer / schedule / objective / data

context:
model scale / dataset / compute budget / training stage

mechanism:
optimization stability / generalization / throughput / capacity
```

Scenario template 可以作为 ontology induction 的 warm-start prior。

但它不应成为固定空间。

因此整体关系是：

$$
\begin{aligned}
&\text{Universal Meta-Grammar} \\
&\quad + \text{Optional Scenario Prior} \\
&\quad + \text{Run-Time Schema Induction}
\end{aligned}
$$

---

### 3.3.3 Run-Specific Schema 是动态的

即使在同一个领域，不同任务的重要维度也不同。

系统应当根据：

* 当前 artifact；
* evaluator；
* 历史 intervention；
* execution trace；
* outcome 分布；

形成当前 run 的空间图式。

初期可能只有：

```text
tiling
```

后续发现相同 tiling 在不同 shape 上表现完全不同，便需要引入：

```text
shape regime × tiling
```

如果进一步发现 pipeline stage 才是真正决定因素，则继续拆分。

因此：

> **LLM 不是生成一个永久空间，而是提出一个关于空间结构的当前假设。**

---

## 3.4 Event 是事实，Schema 是解释

整个方案中最重要的边界是：

# Events are permanent; abstractions are provisional.

中文即：

> **搜索事件是永久事实，搜索空间是当前最有用的解释。**

一次 Search Event 应当由 runtime、artifact parser 和 verifier 尽可能客观地构造：

$$
e_i =
\left(
\text{parent snapshot},
\Delta_{\text{actual}},
\text{context},
\text{trace},
\text{verified outcome}
\right)
$$

其中：

* parent hash 由 runtime 提供；
* actual diff 由 workspace 提取；
* changed entity 由 parser 或 IR analyzer 提取；
* environment 由 verifier 提供；
* metric 和 error 由 verifier 提供；
* Agent 只提供 hypothesis 和 intended mechanism。

每个字段还应携带 provenance：

```text
observed
agent_declared
system_inferred
experimentally_supported
```

Agent 的描述不能直接成为 verified fact。

---

## 3.5 一个事件可以位于多个空间视图中

开放式搜索不适合强制：

> 一个尝试只能落入一个唯一节点。

一个 kernel 实验可能同时属于：

* configuration view：`tile_n = 256`；
* artifact view：修改 `schedule()`；
* mechanism view：增加 data reuse；
* context view：large-square / FP16；
* epistemic view：测试瓶颈是否为 memory latency；
* behavior view：memory traffic 下降但 register pressure 上升。

因此，空间更适合被表示成：

* typed property graph；
* multi-view index；
* hypergraph；
* faceted schema；

而不是一棵单一分类树。

形式上，一次尝试的 footprint 是：

$$
F(p)=
\left\{
\begin{aligned}
&\phi_{\text{artifact}}(p),
\phi_{\text{config}}(p),
\phi_{\text{mechanism}}(p), \\
&\phi_{\text{context}}(p),
\phi_{\text{epistemic}}(p),
\phi_{\text{behavior}}(p)
\end{aligned}
\right\}
$$

---

## 3.6 Declared Footprint 与 Realized Footprint

执行前，Agent 只能声明自己打算做什么：

```text
Declared Footprint
```

执行后，系统才能知道实际上做了什么：

```text
Realized Footprint
```

例如，Agent 声称只修改 tiling，但实际 diff 同时改变了：

* tile size；
* pipeline stage；
* thread mapping；
* buffer allocation。

因此，AtomicPlan 的 admission 使用 Declared Footprint；EvidenceCommit 则使用 Realized Footprint 修正官方 Search State。

两者之间可以记录：

```text
aligned
partially_drifted
materially_drifted
unclassifiable
```

这样不要求 Agent 在执行前准确描述所有隐藏变量。

---

## 3.7 正交性的重新定义

“正交”不能被定义为：

* 文本 embedding 距离大；
* 修改了不同文件；
* 配置向量不同；
* LLM 说它们属于不同类别。

更准确的定义是：

# Schema-Relative Marginal Non-Redundancy

# 相对于当前图式的边际非冗余性

两个方案的重合关系应写成：

$$
O(p_i,p_j\mid\Omega_t)
$$

而不是一个绝对的：

$$
O(p_i,p_j)
$$

它可以是一个多视图向量：

$$
O=
\left[
O_{\text{artifact}},
O_{\text{mechanism}},
O_{\text{context}},
O_{\text{epistemic}},
O_{\text{behavior}}
\right]
$$

一个新 Plan 不必完全不同。

它可以声明自己是：

| 关系                           | 含义                  |
| ---------------------------- | ------------------- |
| `new_axis`                   | 探索此前未覆盖的维度          |
| `refinement`                 | 在一个已证明有价值的方向上继续局部改进 |
| `replication`                | 重复实验以估计噪声或验证稳定性     |
| `interaction_test`           | 测试两个已知变量之间的交互       |
| `alternative_implementation` | 用不同实现验证同一机制         |
| `representation_change`      | 改变问题或候选的表示方式        |

真正需要拒绝的是：

> 高度重合、没有新增信息、没有 replication 目的，也没有明确 exploitation 理由的无意识重复。

---

## 3.8 Search-Time Learning

这里的“学习”首先不是模型训练。

基础模型参数 $\theta$ 可以保持冻结：

$$
\theta_{t+1}=\theta_t
$$

变化的是共享状态：

$$
S_{t+1}=U(S_t,e_t)
$$

它可能学习到：

* 当前主要瓶颈是什么；
* 哪些变量值得区分；
* 哪些方向已经饱和；
* 哪种 context 会改变结论；
* 哪些失败是实现错误；
* 哪些失败真正反驳 hypothesis；
* 下一步哪个实验信息价值最高。

这可以称为：

# Within-Task Search-Time Learning

它比 `plans.md` 更严格，因为 plan 只是接下来做什么，而 Search State 需要表达：

* 为什么；
* 依据是什么；
* 预测是什么；
* 哪些证据支持或反驳；
* 哪些不确定性仍未解决。

长期看，这些 state 可以进一步被压缩成 skill、workflow 或模型训练数据；但跨任务迁移不是第一阶段必须解决的问题。

---

# 4. 框架设计

## 4.1 总体架构

```text
                    SearchSpec + Artifact + Verifier
                                  │
                                  ▼
                    Universal Search Meta-Grammar
                                  │
                         LLM Schema Induction
                                  │
                                  ▼
                  Versioned Run-Specific Schema Ωv
                                  │
          ┌───────────────────────┴────────────────────────┐
          │                                                │
   Existing Events / Coverage                       Agent Proposal
          │                                                │
          └──────────────► AtomicPlan Admission ◄──────────┘
                                  │
                    review + atomic reservation
                                  │
                                  ▼
                       Committed AtomicPlan
                                  │
                        isolated execution
                                  │
                                  ▼
                  Artifact Diff + Trace + Verifier
                                  │
                                  ▼
                       Immutable SearchEvent
                                  │
                       atomic EvidenceCommit
                                  │
                                  ▼
          update incumbent / coverage / hypotheses / schema
                                  │
                                  ▼
                         SearchState version v+1
```

---

## 4.2 核心数据对象

### 1. `SearchEvent`

不可修改的执行事实：

```text
event_id
plan_id
base_candidate
actual artifact delta
execution context
trace
verifier outcome
failure class
provenance
```

---

### 2. `SearchSchema`

当前任务对空间的解释：

```text
schema_version
domain ontology
active dimensions
multi-view descriptors
split / merge history
confidence and evidence
```

Schema 可以变化，Event 不变。

---

### 3. `SearchState`

共享的官方状态：

```text
state_version
incumbents
immutable events
current schema
completed coverage
active reservations
hypotheses and evidence
remaining uncertainty
budget
```

---

### 4. `AtomicPlan`

一个基于特定 state version 的搜索事务：

```text
plan_id
base_state_version
base_candidate
target
intervention
context
hypothesis
expected observation
declared footprint
relation to existing coverage
budget request
```

---

### 5. `Reservation`

AtomicPlan 被接受后，对其搜索区域进行临时占位：

```text
plan_id
schema_version
reserved footprint
status
expiry or lifecycle reference
```

---

### 6. `EvidenceCommit`

执行结束后提交：

```text
plan_id
realized footprint
artifact evidence
verifier result
hypothesis update
reservation release
schema revision proposal
```

---

## 4.3 AtomicPlan 的事务语义

Agent 首先读取：

$$
S_v
$$

并生成：

$$
P_i=\operatorname{Propose}(S_v)
$$

系统对其进行审核：

$$
\operatorname{Admit}(P_i,S_v)
$$

可能返回：

```text
accepted
rejected
needs_rebase
accepted_as_replication
accepted_with_reclassification
```

当 Plan 被接受时，以下操作必须原子完成：

1. 登记 plan；
2. 写入 declared footprint；
3. 建立 reservation；
4. 分配 candidate/workspace；
5. 扣除预算；
6. 增加 state version。

要么全部发生，要么全部不发生。

这就是 `AtomicPlan` 中 atomic 的含义。

它不是说整个长时实验瞬间完成，而是说：

> **“该 rollout 获准探索什么”这一事实被不可分割地登记。**

---

## 4.4 系统审核的内容

Plan admission 至少检查四类问题。

### Freshness

Plan 是否基于仍然兼容的 Search State？

如果 Agent 基于 version 10 生成方案，而其他 Plan 已将状态推进到 version 12，系统需要判断：

* 仍然有效；
* 需要 rebase；
* 已被其他 Plan 覆盖。

---

### Admissibility

由确定性 runtime 检查：

* budget；
* parent 是否存在；
* edit surface；
* frozen artifact；
* verifier；
* candidate 限额；
* plan schema 完整性。

---

### Conflict and Overlap

根据当前 Search Schema 判断：

* 是否与 active reservation 冲突；
* 是否与 completed coverage 高度重复；
* 是否是合理 replication；
* 是否增加新的 context 或 interaction。

---

### Marginal Contribution

一个 Plan 至少应当明确带来一种价值：

$$
V(P\mid S)
=
\operatorname{ExpectedImprovement}
+ \lambda\operatorname{InformationGain}
- \mu\operatorname{Redundancy}
$$

这里不要求精确计算真实数值，但要求 Plan 明确说明自己属于：

* exploitation；
* exploration；
* replication；
* falsification；
* interaction；
* representation change。

---

## 4.5 LLM 与 Runtime 的职责边界

### LLM 负责

* 将自然语言 proposal 编译成 typed Search IR；
* 初始化 domain ontology；
* 解释可能的机制；
* 判断潜在语义 overlap；
* 提议 schema split、merge 或新维度；
* 根据 Search State 生成下一步 hypothesis。

### Runtime 负责

* state version；
* durable storage；
* AtomicPlan commit；
* reservation；
* workspace；
* actual diff；
* verifier；
* evidence provenance；
* budget；
* official state transition。

原则是：

> **模型负责提出空间解释，系统负责维护官方事实。**

LLM 的输出可以参与审核，但不能直接将自己的解释写成 verified truth。

---

## 4.6 可见性规则

并发 Agent 不需要看到彼此完整的 chain-of-thought、草稿或临时代码。

这些信息可能产生强烈 priming，反而使多个 Agent 更快趋同。

更合理的规则是：

### 私有

* 未提交 proposal；
* rollout 内部推理；
* 临时代码；
* 未验证结论。

### 共享

* 已提交 AtomicPlan；
* active reservation；
* verified SearchEvent；
* completed coverage；
* 当前 Search Schema；
* 当前 incumbent；
* unresolved uncertainty。

即：

> **共享已提交的搜索事实，而不是共享未经验证的思考过程。**

---

## 4.7 EvidenceCommit

执行完成后，worker 不能直接修改官方 Search State。

它只能提交：

* actual artifact；
* actual diff；
* verifier output；
* profiler/trace；
* 对 hypothesis 的解释。

系统从中构造 SearchEvent，并原子完成：

1. verifier 确认；
2. 生成 realized footprint；
3. 比较 declared 与 realized footprint；
4. 写入 immutable event；
5. 释放 reservation；
6. 更新 coverage；
7. 更新 hypothesis；
8. 必要时更新 incumbent；
9. 必要时修订 schema；
10. 增加 state version。

因此，Plan Commit 和 EvidenceCommit 是两个不同的事务边界。

---

## 4.8 并发与单链的统一

### 并发模式

多个 Agent 可以并行起草 proposal，但它们必须逐个提交 AtomicPlan。

```text
Agent A/B/C parallel draft
        ↓
AtomicPlan commits serialize
        ↓
A/B/C execute in parallel
        ↓
Evidence commits serialize
```

因此：

> **Agents execute in parallel, but the official search space advances through atomic commits.**

---

### 单链模式

当 `max_parallel = 1` 时，同一个协议仍然成立：

```text
read SearchState
→ submit AtomicPlan
→ check against historical coverage
→ execute
→ EvidenceCommit
→ next iteration
```

系统不再只问：

> 这个方案是否比当前 solution 更好？

还会问：

> 这个方案相对于已经搜索过的区域，新增了什么？

因此，同一框架同时处理：

* 并发空间碰撞；
* 单链时间重复。

---

## 4.9 Schema 的动态拆分、合并与重索引

Search Schema 不能无限膨胀，也不能永远保持初始粒度。

### 需要拆分的信号

* 同一节点内部 outcome 高度多峰；
* 相同标签下出现相反结果；
* 当前节点不能预测新实验；
* 一个新的 context 变量显著改变结果；
* 该节点内部仍存在大量搜索碰撞。

### 可以合并的信号

* 两个节点 outcome 分布接近；
* 区分它们不能改变下一步决策；
* 两个节点长期总是共同出现；
* 区分只增加复杂度，没有预测价值。

LLM 可以提出：

> “当前 tiling 节点应按 shape regime 拆分。”

但是否采用这一拆分，应由历史 evidence 判断其是否提高：

1. 重复识别能力；
2. outcome 预测能力；
3. 下一步规划质量。

因此，图式优化的标准不是“语义上看起来更漂亮”，而是：

# Decision Sufficiency

# 对搜索决策是否足够有用

---

# 5. 与当前 Runtime 的衔接

当前 runtime 的边界不需要被推翻。

仍然保持：

```text
Host:
worker lifecycle

Runtime:
search state / workspace / verifier / score / report
```

建议的变化集中在 control plane。

## 当前流程

```text
plan_next
→ start_batch
→ start_agent_session
→ worker
→ run_verifier
```

## 建议流程

```text
read_search_state
→ draft AtomicPlan
→ submit AtomicPlan
→ review / reject / rebase / commit
→ materialize candidate
→ start_agent_session
→ worker execution
→ run verifier
→ EvidenceCommit
```

现有 `search_run_verifier` 可以继续保留，但 verifier 结果不再只产生 `IterationRecord`，还应驱动正式的 EvidenceCommit。

当前 `get_agent_context` 主要暴露 candidate task、top candidate summaries 和本 candidate 的 iterations；未来应当增加：

* current schema version；
* completed footprints；
* active reservations；
* supported/refuted hypotheses；
* unresolved uncertainties；
* Plan 与 existing coverage 的关系。

不需要增加：

* runtime wait loop；
* peer-to-peer Agent channel；
* runtime process supervisor；
* worker chain-of-thought 共享。

这与当前“host 管生命周期、runtime 管搜索事实”的架构保持一致。

---

# 6. 研究范围与第一阶段目标

第一阶段建议明确收窄为：

## Within-Task Evaluator-Guided Search

暂时不要求：

* 跨任务迁移；
* 模型参数训练；
* 自动发明任意搜索算法；
* 保证找到全局最优；
* 建立完全客观的搜索空间。

只研究：

> 在同一个任务、同一个模型、同一个 verifier 和固定计算预算下，一个持续诱导并事务化维护 Search State 的系统，是否比 stateless loop、raw history 和无协调并发获得更高的搜索效率？

---

## 6.1 关键 Baseline

可以比较：

| 方法                            | 状态表示                          |
| ----------------------------- | ----------------------------- |
| Stateless Loop                | 仅当前 best solution             |
| Raw History                   | 最近的实验日志                       |
| Reflection / `plans.md`       | 滚动总结和下一步计划                    |
| Parallel Independent Rollouts | 多个互不可见 rollout                |
| Embedding Dedup               | 基于文本或 diff 相似度去重              |
| Fixed Space Algorithm         | 人工 config / tree / population |
| Induced Schema + AtomicPlan   | 本文方案                          |

---

## 6.2 关键指标

### 搜索效果

* best score；
* best-score AUC；
* post-stagnation improvement；
* escape rate。

### 搜索效率

* token / evaluator call；
* 单位计算的 score improvement；
* 单位计算的 information gain；
* marginal coverage per rollout。

### 重复与覆盖

* declared overlap；
* realized overlap；
* 无意识重复率；
* active plan collision rate；
* serial repeated-attempt rate。

### Schema 质量

* 对 outcome 的预测能力；
* 对 plan collision 的预测能力；
* split/merge 后的决策增益；
* declared footprint 与 realized footprint 的一致性；
* schema complexity。

### 事务化开销

* Plan rejection rate；
* rebase rate；
* reservation conflict rate；
* admission overhead；
* 并发吞吐损失。

---

# 7. 已知边界与开放问题

## 7.1 Schema Induction 本身仍有模型偏置

LLM 可能：

* 错误归因；
* 忽视隐藏变量；
* 过早形成 ontology；
* 将两个不同机制错误合并；
* 将同一机制过度拆分。

本方案不消除这种偏置。

它做的是：

> 将偏置从模型内部外化为可检查、可证伪、可修订的 Schema。

---

## 7.2 正交性不是客观真理

两个方案是否冗余依赖：

* 当前任务；
* 当前 evaluator；
* 当前 schema；
* 当前搜索阶段；
* 当前 context。

所以系统只能维护：

> 当前证据下最有用的 overlap 判断。

---

## 7.3 过度去重可能破坏 exploitation

一个有效方向通常值得重复优化。

因此系统不能简单规定：

> 相似方案一律拒绝。

它必须区分：

* 有意识的 refinement；
* 有价值的 replication；
* interaction test；
* 无意识重复。

---

## 7.4 评价器决定了可学习边界

如果 verifier：

* 噪声很大；
* 指标不完整；
* 被 candidate exploit；
* 只覆盖部分输入；
* 无法反映真实目标；

那么 Search State 可能学习到错误结构。

因此，框架仍然依赖可验证、可重复的外部反馈。

---

## 7.5 跨任务迁移暂不作为必要条件

Run-specific Search Schema 首先服务当前任务。

未来可以从多个 run 中抽取：

* domain ontology；
* recurring mechanism；
* diagnostic workflow；
* search operators；
* schema revision rule。

但这可以作为第二阶段。

第一阶段只要证明：

> 当前任务中的搜索经验不会随 solution reset 或 rollout 结束而消失。

就已经形成明确贡献。

---

# 8. 最终定位

整个方案可以被概括为两个相互依赖、但逻辑上分离的模块。

## Search Schema Induction

解决：

> 开放式任务中，“搜索空间是什么”以及“哪些差异值得被表示”如何在线形成。

## Transactional Search Coordination

解决：

> 在这个不断变化的空间表示上，多个 Agent 如何并行工作而不产生大量无意识重复。

前者是表示问题，后者是协调问题。

没有前者，AtomicPlan 无法判断自己占据了什么。

没有后者，Schema 只能用于事后分析，无法约束并发搜索。

---

## 最凝练的核心论点

> 当前 Agentic Search 通常只维护 solution history，而没有维护一个显式、共享、可修订的 search-space model。因此，由同一模型和 harness 产生的 rollout 容易在并发上重复、在单链上停滞。我们提出搜索图式诱导：使用统一干预元语法、LLM 诱导的领域 ontology 和运行时动态 schema，将每次尝试表示为基于事实事件的多视图 Search Footprint；空间节点不是永久事实，而是可以随证据拆分、合并和重索引的决策抽象。在此基础上，我们提出事务化搜索协调：Agent 可以并行生成方案，但 AtomicPlan 只有在针对版本化共享 Search State 完成审核、原子准入和 footprint reservation 后才能执行；验证后的结果再通过 EvidenceCommit 原子写入状态。Solution 可以回退，但 Search State 持续积累。

最后可以压缩成四句话：

> **模型负责提出可能性。**
> **图式负责表示已经理解的空间。**
> **事务负责协调新增搜索计算。**
> **证据负责修正模型和图式的偏置。**

以及一句最适合作为项目定位的话：

> **Search intelligence is not the number of rollouts a system can generate, but its ability to make each additional rollout contribute information that previous search has not already produced.**
