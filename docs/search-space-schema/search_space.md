# Search Intelligence Scaling：基于证据累积的搜索空间持续建模与协同探索

> 背景、核心矛盾、方案设想与框架设计

## 0. 核心摘要

当前 Agentic Search 大致存在两种范式：

1. **模型主导的循环搜索**：依赖强模型在工具和验证器支持下反复提出、执行和改进方案。
2. **算法引导的智能体搜索**：将 LLM 作为 proposal generator，使用进化、MCTS、UCB、模拟退火或 Quality-Diversity 等算法组织探索。

前者开放、通用，但多个 rollout 在相同模型、prompt、harness 和初始状态下容易高度相关；增加并发往往增加的是尝试数量，而不是有效覆盖。后者已经能够通过 LLM proposal、islands、MAP-Elites 和 tree search 改善候选生成、多样性与预算分配，但 population、lineage、search tree 和 feature grid 主要是搜索控制结构，不等于一个显式、共享、可修订的搜索空间表达。

二者背后存在一个共同缺失：

> **系统没有持续构造一个显式、共享、可修订的模型，用来表示已经搜索过什么、当前如何理解搜索空间，以及哪些区别对下一步决策真正重要。**

本文将搜索空间中的认知对象明确分成两层：

| 层 | 表达什么 | 如何变化 |
| --- | --- | --- |
| `Search Evidence` | 已经发生的 intervention、执行上下文、trace 和 verifier outcome | 只追加，不改写 |
| `Search Schema` | 当前如何组织、区分和解释这些证据 | 随证据版本化修订 |

candidate 文件、workspace 和 artifact rollback 属于执行层，由 git worktree、sandbox 和 runtime 管理；它们可以为 Evidence 提供 provenance，但不是第三种搜索空间认知状态。

本文所说的 **Search Intelligence Scaling** 不是简单增加 rollout 数量，而是：随着搜索计算增加，系统能够利用累积 Evidence 和持续修订的 Schema，让新增 rollout 继续贡献未被已有搜索覆盖的决策相关信息，并将这些信息转化为更有效的后续搜索。

在这两层认知模型之上，我们设想的第三种路径不是再设计一个更复杂的搜索控制器，而是引入两个核心机制：

### 0.1 Continual Search-Space Modeling：搜索空间持续建模

使用一套跨场景通用的干预元语法，由 LLM 根据具体任务归纳初始领域概念、关系和 Search Schema；每次搜索尝试先形成带 provenance 的 Search Evidence，再由当前 Schema 投影为结构化、多视图的 Search Footprint。系统持续利用新 Evidence 修订概念、维度、关系和 footprint 映射，而不是一次性生成一个永久空间。

### 0.2 Transactional Search Coordination：基于原子提交的协同探索

多个 Agent 可以并行生成方案，但方案不能直接执行。每个方案必须以 `AtomicPlan` 的形式，针对某一版本的 Evidence、Schema 和协调元数据快照提交；只有经过审核、冲突检测和原子预留后才能进入执行。执行结果经 verifier 验证后，再通过独立的 `EvidenceCommit` 追加到 Evidence 账本，并驱动 Schema 修订。

整体原则可以压缩为：

> **Agents speculate; plans commit; evidence accumulates; schemas revise.**

中文即：

> **Agent 可以自由猜想，计划必须原子提交，证据持续积累，空间模型不断修订。**

---

## 1. 背景

### 1.1 问题范围

这里讨论的对象可以统一称为：

> **Evaluator-Guided Agentic Optimization**
>
> **评价器引导的智能体优化**

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

### 1.2 当前两种主要范式

#### 范式一：Model-Driven Loop

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

这种范式没有独立的外部搜索控制器。模型通过上下文或 memory 承载搜索状态，并直接决定下一候选、是否换方向以及何时停止。

---

#### 范式二：Algorithm-Guided Agent Search

AlphaEvolve、OpenEvolve 和 Agent + MCTS 等方法将 LLM 放入显式搜索算法中：

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

其基本方式是：外部算法从 population、tree 或 database 中选择 parent、节点和预算，LLM 在给定上下文中生成候选，再由 evaluator 结果更新外部搜索状态。

---

### 1.3 两种范式的本质差异

两种范式的本质差异不是是否使用 LLM 或 evaluator，而是 **谁拥有搜索控制权，以及持久搜索状态位于哪里**。

| 维度 | Model-Driven Loop | Algorithm-Guided Agent Search |
| --- | --- | --- |
| 搜索控制权 | 模型直接决定下一次尝试 | 外部算法决定 parent、分支和预算 |
| 持久搜索状态 | context、history 或 memory | population、tree、archive 或 database |
| 空间表达 | 隐含在模型的语义与上下文中 | 由 candidate、lineage、action 或 feature 等操作结构近似 |
| 主要优势 | 开放、灵活，可以跨抽象层级提出新方向 | 可维持多分支、回溯并显式分配预算 |
| 主要缺陷 | rollout 相关、遗忘、局部循环，缺少稳定的全局覆盖判断 | 受 seed、早期候选、分支估值和预定义维度影响，结构多样不等于语义覆盖 |

可以将这种对偶关系概括为：

> **Model-Driven Loop 具有语义灵活性，但缺少显式的全局搜索结构；Algorithm-Guided Agent Search 具有显式的全局结构，但缺少与开放语义空间匹配的表达。**

传统 Algorithm-Guided Search 通常要求 candidate、state 和 action 能够被编码为显式 config。引入 LLM 后，proposal 可以跨参数、模块和抽象层级，扩大了可探索范围；但空间也从可比较的配置空间变成由模型、prompt 和历史共同定义的隐式语义空间。此时，population、lineage 和 search tree 仍能记录生成关系，却不能可靠判断不同尝试是否重复、正交或覆盖了新的区域。

因此，两种范式最终面临同一个缺失：系统没有持续构造一个显式、共享、可修订的搜索空间模型，用来表示已经搜索过什么、不同尝试之间有什么关系，以及哪些区别对后续决策真正重要。更完整的对比见 [两种 Agentic Search 范式的差异与共同缺口](./agentic-search-paradigms.md)。

---

## 2. 核心矛盾

### 2.1 Model Intelligence 不等于 Search Intelligence

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

它们产生的搜索方向往往具有较高相关性。甚至强化学习后的模型会对数据集里的“已知最优解”存在明显偏向。

因此：

$$
N \times \text{rollout}
\not\Rightarrow
N \times \text{effective coverage}
$$

增加的可能只是采样数量，而不是新增信息。

---

### 2.2 并发数量与边际覆盖之间的矛盾

理想情况下，启动 $N$ 个并发 worker，应当让它们探索 $N$ 个不同且有价值的区域。

但在没有共享 Evidence 账本和当前 Schema 时：

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

#### Spatial Collision

多个并发 rollout 同时探索相似区域。

#### Temporal Collision

单个 loop 在不同时间反复尝试语义近似的方向。

因此，并发和单链不是两个不同的问题。

> **并发重复是在空间上发生的碰撞；单链重复是在时间上发生的碰撞。**

两者的根因都是：系统没有共享、持续积累的 Search Evidence，也没有基于这些 Evidence 持续修订的 Search Schema。

---

### 2.3 开放性与可表示性之间的矛盾

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

### 2.4 自然语言表达能力与空间可比性之间的矛盾

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

因此，一次搜索尝试的可比较记录不是一段 proposal 文本，而是一条由实际执行和结果锚定的 Evidence：

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

### 2.5 Meta-Agent 与递归偏置之间的矛盾

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

### 2.6 Search Evidence 与 Search Schema 之间的边界

当前很多 loop 只保留当前 candidate、best score 或一份滚动总结。一次尝试没有成为新 incumbent，并不意味着这次尝试没有产生搜索信息。

失败或被拒绝的实验通常仍然提供 Evidence：

* 某个机制可能无效；
* 某个参数范围已经测试；
* 某个 hypothesis 被反驳；
* 某个实现方式导致资源溢出；
* 某个方向只有在特定 context 下有效。

这些事实应进入只追加的 Search Evidence 账本：

$$
\mathcal E_{t+1}
=
\operatorname{Append}(\mathcal E_t,e_{t+1})
$$

其中，$e_{t+1}$ 是一次经过 provenance 标注和 verifier 确认的 Search Event。后续即使发现该实验环境无效、测量有误或结论被推翻，也应追加 invalidation、correction 或 supersession 关系，而不是改写原始事件。

另一方面，系统对这些事实的组织和解释必须允许改变：

$$
\Omega_{t+1}
=
\operatorname{Revise}(\Omega_t,\mathcal E_{t+1})
$$

早期 Schema 可能只把一组实验描述为：

```text
tiling
```

随着 Evidence 增加，更合适的 Schema 可能变成：

```text
shape regime × tile size × memory level × pipeline stage
```

这里变化的是描述粒度、关系和解释，不是已经发生过的事实。

核心原则是：

> **Search Evidence 只追加、不改写；Search Schema 随证据持续修订。**

artifact、candidate workspace 的创建、恢复和删除仍由执行层负责。执行层状态是否回退，不决定搜索知识是否保留。

---

## 3. 我们的方案设想

### 3.1 核心判断

我们不是要设计一种新的固定搜索算法，也不是要让 Agent 自由生成一个不可审计的搜索空间。

我们的核心设想是：

> **系统从实际干预及其结果中，持续构建并修订一个任务特定、版本化、对下一步搜索决策足够充分的空间模型。**

从搜索空间建模的角度，每次搜索应当同时产生两个结果：

$$
\text{new committed search evidence}
$$

以及：

$$
\text{a retained or revised search schema}
$$

因此，“可积累的搜索智能”在第一阶段不需要训练模型权重。

它表现为一个任务内、非参数化的两层结构：只追加的 Evidence 账本，以及由 Evidence 持续修订的 Search Schema。

---

### 3.2 两层模型：Search Evidence 与 Search Schema

Search Evidence 可以表示为按提交顺序持久化的事件账本：

$$
\mathcal E_t=(e_1,e_2,\ldots,e_t)
$$

每个 $e_i$ 都记录实际 intervention、执行上下文、trace、verifier outcome 和 provenance。Evidence 的不可变性针对“当时观察并提交了什么”；它不保证当时的测量永远有效，也不把 Agent 的解释提升为事实。纠错通过追加关系表达，而不是原地改写。

Search Schema 是对 Evidence 的当前解释：

$$
\Omega_t
=
\operatorname{Revise}(\Omega_{t-1},\mathcal E_t)
$$

它包括：

* 当前领域概念、关系和有效维度；
* Event 到多视图 footprint 的映射；
* coverage、overlap 和 saturation 的当前判断；
* hypotheses、支持证据、反证和不确定性；
* split、merge、rename 和 reindex 历史。

Schema 是可修订、可证伪的模型，不是不可修改的事实。相同的 Evidence 账本可以在不同 Schema 版本下得到不同的节点、边界和覆盖视图。

事务执行需要同时读取这两层对象和协调元数据。可以把这个运行时视图记为 `CoordinationSnapshot`：

$$
Q_t=(\mathcal E_t,\Omega_t,M_t)
$$

其中 $M_t$ 是 active reservations、budget、snapshot version 等协调元数据。`CoordinationSnapshot` 只是读取和提交事务时的组合视图，不是 Evidence 和 Schema 之外的第三种认知对象。如果现有实现或 API 继续使用 `SearchState` 这个名称，也应将其理解为这一兼容性容器，而不是独立的认知状态。

这套两层模型不是：

* 完整聊天历史；
* 原始 rollout transcript；
* 一份不断增长的 `plans.md`；
* 当前 Agent 的自由反思。

它应当是：

> **只追加的事实账本，以及建立在该账本之上的有界、结构化、可证伪解释。**

---

### 3.3 Continual Search-Space Modeling

#### 一套通用 Meta-Grammar

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
| Base                   | 方案基于哪个 baseline 或 candidate |
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

#### 领域概念模型随任务变化

不同任务变化的是领域概念、概念之间的关系和真正影响搜索决策的区分，而不是最上层元语法。这些内容共同构成 Search Schema 中的领域概念模型。

这里不使用 `Domain Ontology`：严格的 ontology 通常还要求明确的实体类型、关系语义、公理或约束；本文当前需要的只是足以描述 Plan、投影 Evidence 和判断 overlap 的任务内概念模型。

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

Scenario template 可以作为领域概念建模和 Schema 初始化的 warm-start prior。

但它不应成为固定空间。

因此整体关系是：

$$
\begin{aligned}
&\text{Universal Meta-Grammar} \\
&\quad + \text{Optional Scenario Prior} \\
&\quad + \text{Continual Schema Modeling}
\end{aligned}
$$

---

#### Run-Specific Schema 是动态的

即使在同一个领域，不同任务的重要维度也不同。

系统应当根据：

* 当前 artifact；
* evaluator；
* 历史 intervention；
* execution trace；
* outcome 分布；

形成当前 run 的搜索空间模型。

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

### 3.4 Evidence 是事实，Schema 是解释

整个方案中最重要的边界是：

> **Evidence is append-only; schemas are provisional.**

中文即：

> **Search Evidence 记录已经发生的事实，Search Schema 提供当前最有用的解释。**

Search Evidence 账本由一条条不可原地修改的 `SearchEvent` 组成。每条 Event 应当由 runtime、artifact parser 和 verifier 尽可能客观地构造：

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

Agent 的描述不能直接成为 verified fact。它可以作为带 provenance 的声明进入 Event，或作为 Schema hypothesis 等待 Evidence 支持，但不能覆盖系统观察。

---

### 3.5 一条 Evidence 可以位于多个空间视图中

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

形式上，一条 Evidence 在 Schema $\Omega_t$ 下的 footprint 是：

$$
F_{\Omega_t}(e)=
\left\{
\begin{aligned}
&\phi_{\text{artifact}}(e),
\phi_{\text{config}}(e),
\phi_{\text{mechanism}}(e), \\
&\phi_{\text{context}}(e),
\phi_{\text{epistemic}}(e),
\phi_{\text{behavior}}(e)
\end{aligned}
\right\}
$$

这里的 $F_{\Omega_t}(e)$ 是 Schema-relative projection，而不是 Evidence 本身。同一条 $e$ 在新的 Schema 下可以得到不同 footprint。

---

### 3.6 Declared Footprint、Observed Evidence 与 Realized Footprint

执行前，Agent 只能声明自己打算做什么：

```text
Declared Footprint
```

执行后，runtime、parser 和 verifier 先构造不依赖分类结论的 Observed Evidence：

```text
actual delta + execution context + trace + verifier outcome
```

然后，系统才在当前 Schema $\Omega_v$ 下将这条 Evidence 投影为 Realized Footprint：

$$
F_{\mathrm{realized}}^{(v)}(e_i)
=
\operatorname{Project}(e_i,\Omega_v)
$$

例如，Agent 声称只修改 tiling，但实际 diff 同时改变了：

* tile size；
* pipeline stage；
* thread mapping；
* buffer allocation。

这里的 actual diff 属于 Evidence；“tile size”“pipeline stage”等分类属于当前 Schema 下的解释。未来 Schema 如果进一步拆分 memory level 或 shape regime，同一条 Evidence 可以被重新投影，而不需要修改原 Event。

因此，AtomicPlan admission 使用当前 Schema 下的 Declared Footprint；EvidenceCommit 先追加 Observed Evidence，再保存带 `schema_version` 的 Realized Footprint 映射，并据此刷新 coverage 视图。

两者之间可以记录：

```text
aligned
partially_drifted
materially_drifted
unclassifiable
```

这样不要求 Agent 在执行前准确描述所有隐藏变量。

---

### 3.7 从“正交方案”到边际非冗余

在并行 Agentic Search 中，人们常说：

> 让不同 Agent 提出彼此正交的方向。

这个说法想解决的是计算重复：如果两个 rollout 测试的是同一机制、同一上下文和同一假设，那么增加并发并没有带来相应的信息增量。

但这里的“正交”只是借用的比喻，并不是严格的数学正交。开放式搜索没有预先固定的坐标轴、内积或独立性判据；两个 Plan 也可能在代码改动上重合，却在 context、hypothesis 或预期信息上不同。

因此，本文真正关心的问题不是：

> 两个方案是否绝对正交？

而是：

> 在已有 Evidence、当前 Schema 和 active reservations 下，新 Plan 是否仍能带来不会被已有搜索完全覆盖的决策相关信息？

这不能简单定义为：

* 文本 embedding 距离大；
* 修改了不同文件；
* 配置向量不同；
* LLM 说它们属于不同类别。

本文将这一目标称为：

> **Schema-Relative Marginal Non-Redundancy**
>
> **相对于当前空间模型的边际非冗余性**

“边际”强调判断对象是新增加的这一次计算；“非冗余”强调它不必与其他 Plan 完全不同，只需要有明确的新增信息、验证价值或 exploitation 价值。

两个方案的重合程度应相对于当前 Schema 表达为：

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

因此，低 overlap 不是唯一目标，一个新 Plan 也不必与已有 Plan 完全不同。

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

因此，“正交方案”只用于描述问题来源；AtomicPlan admission 真正审核的是 schema-relative overlap、边际信息贡献和计划意图，而不是一个二元的正交/不正交标签。

---

### 3.8 Search-Time Learning

这里的“学习”首先不是模型训练。

基础模型参数 $\theta$ 可以保持冻结：

$$
\theta_{t+1}=\theta_t
$$

变化的是 Evidence 账本和当前 Schema：

$$
\mathcal E_{t+1}
=
\operatorname{Append}(\mathcal E_t,e_{t+1})
$$

$$
\Omega_{t+1}
=
\operatorname{Revise}(\Omega_t,\mathcal E_{t+1})
$$

Schema 可能从累积 Evidence 中归纳出：

* 当前主要瓶颈是什么；
* 哪些变量值得区分；
* 哪些方向已经饱和；
* 哪种 context 会改变结论；
* 哪些失败是实现错误；
* 哪些失败真正反驳 hypothesis；
* 下一步哪个实验信息价值最高。

这可以称为：

> **Within-Task Search-Time Learning**

它比 `plans.md` 更严格，因为 plan 只是接下来做什么，而 Evidence 与 Schema 的组合需要表达：

* 为什么；
* 依据是什么；
* 预测是什么；
* 哪些证据支持或反驳；
* 哪些不确定性仍未解决。

长期看，这些 Evidence 和 Schema 可以进一步被压缩成 skill、workflow 或模型训练数据；但跨任务迁移不是第一阶段必须解决的问题。

---

## 4. 框架设计

### 4.1 总体架构

这一层只展示 `/goal-plus`、并发搜索 loop、空间控制面和最终结果之间的关系。Evidence、Schema、AtomicPlan 和 EvidenceCommit 的内部结构在后续小节展开。

```text
                         /goal-plus <goal>
                                  │
                                  ▼
                          Goal Plus Main
                                  │
             ┌────────────────────┼────────────────────┐
             │                    │                    │
             ▼                    ▼                    ▼
          Loop A               Loop B               Loop C
             │                    │                    │
        local search          local search          local search
             │                    │                    │
       submit Plan A         submit Plan B         submit Plan C
             │                    │                    │
┌────────────┴────────────────────┴────────────────────┴────────────┐
│                             SpaceAgent                            │
│    shared space · AtomicPlan review · atomic admission/commit     │
└────────────┬────────────────────┬────────────────────┬────────────┘
             │                    │                    │
  accept: continue     accept: continue     accept: continue
  reject: revise ↺     reject: revise ↺     reject: revise ↺
             │                    │                    │
             ▼                    ▼                    ▼
      execute / verify     execute / verify     execute / verify
             │                    │                    │
        Candidate A          Candidate B          Candidate C
             │                    │                    │
             └────────────────────┼────────────────────┘
                                  ▼
                      Validated Candidate Set
                                  │
                         select best candidate
                                  ▼
                             Final Result
```

三条竖向泳道表示 loop subagent 始终并发运行。横向的 `SpaceAgent` 是它们共同经过的方案准入关卡：它只串行化 AtomicPlan 的审核和原子提交，不会串行化各个 loop 的本地搜索与 candidate 执行。

每个 loop subagent 独立探索，但在执行新方向前必须向 `SpaceAgent` 提交方案。`SpaceAgent` 对外只返回 accept 或 reject；被接受的 loop 执行 candidate，由 runtime 运行 verifier，然后提交结果并继续下一轮；被拒绝的 loop 根据反馈修改方向后重新提交。

这里的 `SpaceAgent` 是空间控制面的逻辑角色。它封装本章后续描述的 Evidence/Schema 读取、overlap 与边际贡献审核、AtomicPlan admission、reservation、EvidenceCommit 和空间模型修订；它可以使用模型完成分析，但官方状态变更仍由 runtime 通过原子事务提交。`SpaceAgent` 不负责 subagent 的启动、停止或生命周期。

当预算耗尽或满足全局停止条件后，系统从已经验证的 candidate 中按预先声明的选择规则选出 best candidate，作为 `/goal-plus` 的最终结果。

---

### 4.2 核心数据对象

#### `SearchEvidence` / `SearchEvent`

`SearchEvidence` 是只追加的账本，`SearchEvent` 是其中一条不可原地修改的执行记录：

```text
event_id
plan_id
base_candidate
actual artifact delta
execution context
trace
verifier outcome
provenance
```

---

#### `SearchSchema`

当前任务对空间的解释：

```text
schema_version
domain concepts and relations
active dimensions
multi-view descriptors
event-to-footprint mappings
derived classifications
split / merge history
confidence and evidence references
hypotheses and uncertainty
```

Schema 可以变化，原始 Event 不变。Schema 对旧 Event 的新解释通过新版本和映射关系表达。

---

#### `CoordinationSnapshot`

为事务读取提供的版本化运行时快照：

```text
snapshot_version
evidence ledger reference
current schema
derived coverage view
active reservations
budget
```

它只是 Evidence、Schema 与协调元数据的组合视图，不是第三种搜索空间认知对象。candidate、workspace 和 incumbent 由现有执行/runtime 数据结构管理，可以被 Plan 引用，但不定义 Evidence 或 Schema 的演化语义。

---

#### `AtomicPlan`

一个基于特定 snapshot version 的搜索事务：

```text
plan_id
base_snapshot_version
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

#### `Reservation`

AtomicPlan 被接受后，对其搜索区域进行临时占位：

```text
plan_id
schema_version
reserved footprint
status
expiry or lifecycle reference
```

---

#### `EvidenceCommit`

执行结束后，事务接收：

```text
plan_id
candidate/workspace reference
observed evidence payload
verifier result
hypothesis interpretation
schema revision proposal
```

并原子产生：

```text
SearchEvent
schema version
realized footprint mapping
reservation release
snapshot version
```

---

### 4.3 AtomicPlan 的事务语义

Agent 首先读取：

$$
Q_v=(\mathcal E_v,\Omega_v,M_v)
$$

这里 $Q_v$ 只是同一版本的 Evidence、Schema 和协调元数据快照。

并生成：

$$
P_i=\operatorname{Propose}(Q_v)
$$

系统对其进行审核：

$$
\operatorname{Admit}(P_i,Q_v)
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
6. 增加 snapshot version。

要么全部发生，要么全部不发生。

这就是 `AtomicPlan` 中 atomic 的含义。

它不是说整个长时实验瞬间完成，而是说：

> **“该 rollout 获准探索什么”这一事实被不可分割地登记。**

---

### 4.4 系统审核的内容

Plan admission 至少检查四类问题。

#### Freshness

Plan 是否基于仍然兼容的 `CoordinationSnapshot`？

如果 Agent 基于 version 10 生成方案，而其他 Plan 已将 snapshot 推进到 version 12，系统需要判断：

* 仍然有效；
* 需要 rebase；
* 已被其他 Plan 覆盖。

---

#### Admissibility

由确定性 runtime 检查：

* budget；
* parent 是否存在；
* edit surface；
* frozen artifact；
* verifier；
* candidate 限额；
* plan schema 完整性。

---

#### Conflict and Overlap

根据当前 Search Schema 判断：

* 是否与 active reservation 冲突；
* 是否与 completed coverage 高度重复；
* 是否是合理 replication；
* 是否增加新的 context 或 interaction。

---

#### Marginal Contribution

一个 Plan 至少应当明确带来一种价值：

$$
V(P\mid \mathcal E,\Omega,M)
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

### 4.5 LLM 与 Runtime 的职责边界

#### LLM 负责

* 将自然语言 proposal 编译成 typed Search IR；
* 提出初始领域概念与关系；
* 解释可能的机制；
* 判断潜在语义 overlap；
* 提议 schema split、merge 或新维度；
* 根据累积 Evidence 和当前 Schema 生成下一步 hypothesis。

#### Runtime 负责

* snapshot version；
* durable storage；
* AtomicPlan commit；
* reservation；
* workspace；
* actual diff；
* verifier；
* evidence provenance；
* budget；
* official Evidence append 和 Schema transition。

原则是：

> **模型负责提出空间解释，系统负责维护官方事实。**

LLM 的输出可以参与审核，但不能直接将自己的解释写成 verified truth。

---

### 4.6 可见性规则

并发 Agent 不需要看到彼此完整的 chain-of-thought、草稿或临时代码。

这些信息可能产生强烈 priming，反而使多个 Agent 更快趋同。

更合理的规则是：

#### 私有

* 未提交 proposal；
* rollout 内部推理；
* 临时代码；
* 未验证结论。

#### 共享

* 已提交 AtomicPlan；
* active reservation；
* 已提交的 Search Evidence；
* completed coverage；
* 当前 Search Schema；
* unresolved uncertainty。

当前 candidate 或 incumbent 的引用可以作为执行 proposal 的输入共享，但由 workspace/runtime 层管理，不属于 Search Evidence 或 Search Schema。

即：

> **共享已提交的搜索事实，而不是共享未经验证的思考过程。**

---

### 4.7 EvidenceCommit

执行完成后，worker 不能直接修改 Evidence 账本或官方 Search Schema。

worker 只能提交或暴露：

* plan 和 candidate/workspace 引用；
* 对 intended/actual intervention 的声明；
* 对 hypothesis 的解释；
* schema revision proposal。

actual diff 由 runtime 从 workspace 提取，trace 和环境信息由执行系统记录，verifier outcome 由 verifier 产生。它们与 Agent 声明使用不同 provenance，不能相互覆盖。

系统从中构造新的 SearchEvent，并原子完成：

1. verifier 确认；
2. 从 actual diff、context、trace 和 verifier outcome 构造 Observed Evidence；
3. 将 Event 追加到 Evidence 账本；
4. 在当前 Schema 下投影 realized footprint，并记录 `schema_version`；
5. 比较 declared 与 realized footprint；
6. 释放 reservation；
7. 根据累积 Evidence 更新 hypothesis；
8. 必要时修订 Schema，并重新投影受影响的历史 Event；
9. 刷新 derived coverage；
10. 增加 snapshot version。

因此，Plan Commit 和 EvidenceCommit 是两个不同的事务边界。

---

### 4.8 并发与单链的统一

#### 并发模式

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

> **Agents execute in parallel, while Evidence and Schema advance through atomic commits.**

---

#### 单链模式

当 `max_parallel = 1` 时，同一个协议仍然成立：

```text
read Evidence + Schema + coordination snapshot
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

### 4.9 Schema 的动态拆分、合并与重索引

Search Schema 不能无限膨胀，也不能永远保持初始粒度。

#### 需要拆分的信号

* 同一节点内部 outcome 高度多峰；
* 相同标签下出现相反结果；
* 当前节点不能预测新实验；
* 一个新的 context 变量显著改变结果；
* 该节点内部仍存在大量搜索碰撞。

#### 可以合并的信号

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

因此，空间模型修订的标准不是“语义上看起来更漂亮”，而是：

> **Decision Sufficiency**
>
> **对搜索决策是否足够有用**

---

## 5. 与当前 Runtime 的衔接

当前 runtime 的边界不需要被推翻。

仍然保持：

```text
Host:
worker lifecycle

Runtime:
evidence ledger / schema / coordination
workspace / verifier / score / report
```

这里 workspace、candidate 和 artifact 操作仍属于执行存储；Evidence 账本和 Schema 才构成搜索空间的认知记录。两者由同一个 runtime 持久化，不代表它们是同一类状态。

建议的变化集中在 control plane。

### 5.1 当前流程

```text
plan_next
→ start_batch
→ start_agent_session
→ worker
→ run_verifier
```

### 5.2 建议流程

```text
read_search_context
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

## 6. 研究范围与第一阶段目标

第一阶段建议明确收窄为：

> **Within-Task Evaluator-Guided Search**

暂时不要求：

* 跨任务迁移；
* 模型参数训练；
* 自动发明任意搜索算法；
* 保证找到全局最优；
* 建立完全客观的搜索空间。

只研究：

> 在同一个任务、同一个模型、同一个 verifier 和固定计算预算下，一个只追加 Search Evidence、并基于 Evidence 持续修订 Search Schema 的系统，是否比 stateless loop、raw history 和无协调并发获得更高的搜索效率？

---

### 6.1 关键 Baseline

可以比较：

| 方法                            | 状态表示                          |
| ----------------------------- | ----------------------------- |
| Stateless Loop                | 仅当前 best solution             |
| Raw History                   | 最近的实验日志                       |
| Reflection / `plans.md`       | 滚动总结和下一步计划                    |
| Parallel Independent Rollouts | 多个互不可见 rollout                |
| Embedding Dedup               | 基于文本或 diff 相似度去重              |
| Fixed Space Algorithm         | 人工 config / tree / population |
| Evidence Ledger + Revisable Schema + AtomicPlan | 本文方案              |

---

### 6.2 关键指标

#### 搜索效果

* best score；
* best-score AUC；
* post-stagnation improvement；
* escape rate。

#### 搜索效率

* token / evaluator call；
* 单位计算的 score improvement；
* 单位计算的 information gain；
* marginal coverage per rollout。

#### 重复与覆盖

* declared overlap；
* realized overlap；
* 无意识重复率；
* active plan collision rate；
* serial repeated-attempt rate。

#### Schema 质量

* 对 outcome 的预测能力；
* 对 plan collision 的预测能力；
* split/merge 后的决策增益；
* declared footprint 与 realized footprint 的一致性；
* schema complexity。

#### 事务化开销

* Plan rejection rate；
* rebase rate；
* reservation conflict rate；
* admission overhead；
* 并发吞吐损失。

---

## 7. 已知边界与开放问题

### 7.1 搜索空间持续建模仍有模型偏置

LLM 可能：

* 错误归因；
* 忽视隐藏变量；
* 过早固定领域概念和关系；
* 将两个不同机制错误合并；
* 将同一机制过度拆分。

本方案不消除这种偏置。

它做的是：

> 将偏置从模型内部外化为可检查、可证伪、可修订的 Schema。

---

### 7.2 边际非冗余性依赖当前 Schema

两个 Plan 是否重合、一个新 Plan 能否带来边际信息，不是脱离任务后仍然成立的客观“正交关系”。它依赖：

* 当前任务；
* 当前 evaluator；
* 当前 schema；
* 当前搜索阶段；
* 当前 context。

所以系统只能维护：

> 当前 Evidence 和 Schema 下，对下一步搜索决策最有用的 overlap 与边际贡献判断。

---

### 7.3 过度去重可能破坏 exploitation

一个有效方向通常值得重复优化。

因此系统不能简单规定：

> 相似方案一律拒绝。

它必须区分：

* 有意识的 refinement；
* 有价值的 replication；
* interaction test；
* 无意识重复。

---

### 7.4 评价器决定了可学习边界

如果 verifier：

* 噪声很大；
* 指标不完整；
* 被 candidate exploit；
* 只覆盖部分输入；
* 无法反映真实目标；

那么 Search Schema 可能从不完整或失真的 Evidence 中归纳出错误结构。

因此，框架仍然依赖可验证、可重复的外部反馈。

---

### 7.5 跨任务迁移暂不作为必要条件

Run-specific Search Schema 首先服务当前任务。

未来可以从多个 run 中抽取：

* 领域概念模型；
* recurring mechanism；
* diagnostic workflow；
* search operators；
* schema revision rule。

但这可以作为第二阶段。

第一阶段只要证明：

> 已提交的 Search Evidence 不会随 candidate workspace 被恢复、删除或 rollout 结束而消失，并且仍可被后续 Schema 重新解释。

就已经形成明确贡献。

---

## 8. 最终定位

整个方案可以被概括为两个相互依赖、但逻辑上分离的模块。

### 8.1 Continual Search-Space Modeling

解决：

> 开放式任务中，“搜索空间是什么”以及“哪些差异值得被表示”如何在线形成。

### 8.2 Transactional Search Coordination：基于原子提交的协同探索

解决：

> 在这个不断变化的空间表示上，多个 Agent 如何并行工作而不产生大量无意识重复。

前者是表示问题，后者是协调问题。

没有前者，AtomicPlan 无法判断自己占据了什么。

没有后者，Schema 只能用于事后分析，无法约束并发搜索。

---

### 8.3 最凝练的核心论点

> 当前 Agentic Search 通常只保留 candidate 结果或非结构化历史，没有把“已经发生的事实”与“当前如何理解这些事实”明确分离。因此，由同一模型和 harness 产生的 rollout 容易在并发上重复、在单链上停滞。我们提出证据驱动的搜索空间持续建模：使用统一干预元语法归纳任务内的领域概念和关系，将每次尝试记录为具有 provenance 的 Search Evidence，再由可修订的 Search Schema 将 Evidence 投影为多视图 footprint。Evidence 只追加、不改写，空间节点、覆盖关系和 hypothesis 则可以随新证据拆分、合并和重索引。在此基础上，Agent 可以并行生成方案，但 AtomicPlan 只有在针对同一版本的 Evidence、Schema 和协调元数据快照完成审核、原子准入和 footprint reservation 后才能执行；验证后的结果再通过 EvidenceCommit 追加到 Evidence 账本，并驱动下一版 Schema。candidate 文件和 workspace 的生命周期仍由现有 runtime、git worktree 或 sandbox 管理，不参与这两个认知对象的语义定义。

最后可以压缩成四句话：

> **模型负责提出可能性。**
> **证据负责记录已经发生的事实。**
> **空间模型负责提供可修订的解释。**
> **事务负责协调新增搜索计算。**

以及一句最适合作为项目定位的话：

> **Search intelligence is not the number of rollouts a system can generate, but its ability to make each additional rollout contribute information that previous search has not already produced.**
