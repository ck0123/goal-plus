# 搜索图式诱导：面向开放式智能体优化的非冗余探索与事务化协同

## Search Schema Induction: Non-Redundant Exploration and Transactional Coordination for Open-Ended Agentic Optimization

> **匿名学术论文草稿（中文，不含实验与结果部分）**
>
> 本稿聚焦问题定义、理论框架、表示方法、事务语义、系统边界、相关工作与局限性。作者、机构与实验章节待补充。

---

## 摘要

基于大语言模型的智能体搜索正逐渐形成两类代表性范式。第一类依赖通用模型在工具环境中执行持续的“提出—执行—评估—保留或回退”循环，将搜索能力主要寄托于模型自身的推理、编程与反思能力；第二类将大语言模型作为候选生成器，并借助进化算法、树搜索、多臂老虎机或质量—多样性方法显式组织探索。前者具有较强的开放性与任务泛化能力，但由相同模型、相同提示与相同 harness 产生的多次 rollout 往往高度相关，使并发计算退化为语义重复，并使单链搜索在时间上反复探索相似方向。后者可以通过人工定义的表示、种群和搜索规则维持探索，但其有效性依赖于可表示的优化空间及领域专家所设计的行为描述符、变异算子和状态结构。

本文提出一个统一观点：开放式智能体搜索的核心缺口并非缺少某一种更复杂的搜索算法，而是缺少一个显式、可修订且对后续决策充分的 **搜索状态**。为此，我们提出 **搜索图式诱导（Search Schema Induction, SSI）**：以跨任务通用的干预元 Schema 约束方案结构，由大语言模型根据任务、工件、执行环境与历史证据在线诱导领域本体和运行时搜索图式，并将每次尝试编译为带类型的多视图搜索足迹（search footprint）。搜索事件作为不可变事实被永久保存，而图式单元、等价关系和覆盖视图仅被视为可随证据拆分、合并和重索引的暂时抽象。基于该表示，我们进一步提出 **事务化搜索协同（Transactional Search Coordination, TSC）**：并行 Agent 可以独立生成方案，但每个方案必须以某一版本的共享搜索状态为基础，通过版本化方案审核、原子计划准入与足迹预留后方可执行；执行结果则通过独立的验证证据提交进入官方状态。由此，候选解可以回退，而搜索知识持续积累；并发 rollout 在空间上避免无意识碰撞，单链 loop 在时间上避免无意义重复。

本文将“正交性”重新定义为相对于当前图式的 **边际非冗余性**，而非文本、代码 diff 或配置向量之间的绝对距离。该框架不要求预先给出完整搜索空间，也不要求更新基础模型参数；其学习对象是任务内、非参数化、版本化且可证伪的搜索状态与搜索图式。本文给出问题形式化、图式诱导层次、AtomicPlan 原语、运行时图式修订原则及其理论边界，并讨论其与模型主导的单当前候选搜索、LLM 引导的算法搜索、质量—多样性优化、状态抽象和长期记忆方法的关系。

**关键词：** 评价器引导智能体优化；搜索图式诱导；共享搜索状态；搜索足迹；原子计划准入；事务化搜索协同；边际非冗余；状态抽象

---

## Abstract

Agentic search systems based on large language models broadly follow two paradigms. Model-driven loops rely on a capable general-purpose model to repeatedly propose, execute, evaluate, and revise solutions, while algorithm-guided systems use the model as a proposal generator inside evolutionary search, tree search, bandit allocation, or quality-diversity optimization. The former is open-ended but often produces highly correlated rollouts under the same model and harness, causing parallel duplication and serial stagnation. The latter enforces exploration but depends on human-designed representations, behavioral descriptors, operators, and search-state structures.

We argue that the central missing abstraction is not another search controller, but an explicit, revisable, and decision-sufficient representation of what has already been searched. We introduce **Search Schema Induction**, in which a universal intervention meta-schema is instantiated into a task-specific domain ontology and a run-specific search schema. Each attempt is compiled into a typed, multi-view search footprint grounded in immutable execution events. Events are permanent, whereas space nodes and equivalence classes remain provisional and may be split, merged, or re-indexed as evidence accumulates. On top of this representation, we define **Transactional Search Coordination**: agents may speculate in parallel, but an AtomicPlan becomes executable only after versioned review, atomic admission, and footprint reservation against the shared Search State; verified outcomes enter the state through a separate verified evidence commit.

This framework treats orthogonality as schema-relative marginal non-redundancy rather than absolute textual or structural distance. It unifies parallel and serial search, allows solution rollback without epistemic rollback, and supports search-time learning without updating model parameters. We present the problem formulation, representation hierarchy, transactional semantics, schema-revision principles, expected properties, and limitations of the proposed paradigm.

---

# 1. 引言

在代码优化、模型训练、算法发现、科学假设检验和系统调优等任务中，目标函数通常“易于评估但难以直接求解”：给定一个候选方案，系统可以通过测试、仿真、编译器、形式化验证器或实验指标判断其正确性与质量，但很难直接构造最优方案。大语言模型的代码生成、推理与工具使用能力，使这类任务逐渐从一次性生成转向长时程的智能体搜索。

一个典型系统维护当前候选工件，要求 Agent 提出修改、执行实验、读取反馈，并根据结果保留或回退。Karpathy AutoResearch 等具体系统以极简 harness 将这一循环运行数十至数百次；ReAct 和 Reflexion 等工作则表明，语言模型可以在与环境交互和保存语言反馈的过程中改善后续决策 [1,2,14]。另一条路线显式引入树搜索、种群、进化和程序数据库。Tree of Thoughts 将推理展开为可回溯的树结构 [3]；FunSearch 与 AlphaEvolve 将预训练模型、自动评价器和进化式候选管理结合起来，在数学与算法问题中搜索可验证程序 [4,5]。近期的 GEAR、Bilevel AutoResearch 和 Arbor 分别从种群前沿、可修改搜索机制和持久假设树的角度扩展长时程自主研究 [15–17]；Darwin Gödel Machine 与 Hyperagents 则进一步把智能体或其元级改进机制本身作为可修改工件 [20,21]。

这些工作揭示了一个共同事实：增加 inference-time compute 并不自动等价于增加 search intelligence。测试时计算扩展研究也表明，额外计算的收益高度依赖任务难度、验证信号与计算分配策略，简单 best-of-$N$ 并不是普遍最优的扩展方式 [19]。若多个 rollout 共享同一模型、同一 harness、同一 baseline 和相似上下文，则它们不是独立的搜索样本，而是由同一条件分布产生的高度相关样本。并发数量可以线性增加，但有效覆盖的搜索区域未必同比增长。相同问题也存在于单链 loop 中：即使每轮读取历史，系统仍可能以不同文字反复提出同一类机制或局部参数变体。

一种直接反应是引入更复杂的控制器：维护多个 Agent、加入 MCTS、UCB、遗传算法、岛模型或 meta-agent。然而，这种做法并未触及更深层问题。任何控制器都携带归纳偏置；将搜索决策交给 meta-agent，只会把偏置从解空间转移到搜索策略空间。No Free Lunch 结果提醒我们，不存在对所有问题均占优的无偏搜索器 [18]。真正需要解决的不是如何消除偏置，而是如何将隐式、固定且难以检查的偏置，转化为显式、可证伪、可修订并受环境证据约束的搜索状态。

本文的核心论点是：

> **开放式智能体搜索的关键瓶颈，是系统没有持续构造“已经搜索过什么、当前如何理解搜索空间、哪些区别对下一步决策重要”的显式模型。**

现有智能体循环通常只维护当前最优解、原始日志和自然语言反思；传统算法则预先规定搜索状态，例如 MCTS 的树、进化算法的种群、贝叶斯优化的后验或 MAP-Elites 的行为网格。前者开放但不稳定，后者稳定但依赖人工表示。本文提出第三种路径：不要求人预先定义完整空间，而让系统从实际干预及其结果中在线诱导一个任务特定、版本化且可修订的空间表示。

本文的主要贡献如下：

1. **重新定义问题。** 将并发重复与单链重复统一为 共享搜索状态的缺失导致的空间碰撞，并区分可回退的 Solution State 与应持续积累的共享搜索状态。
2. **提出搜索图式诱导。** 使用通用干预元 Schema、LLM 诱导的领域本体、运行时搜索图式和多视图搜索足迹，替代不可靠的“自然语言方案到唯一空间节点”映射。
3. **提出事务化搜索语义。** 通过版本化共享搜索状态、AtomicPlan、原子计划准入、验证证据提交和足迹预留，使并发 Agent 成为对共享状态的 speculative workers，而非互不可见的独立循环。
4. **重新定义正交性。** 将其定义为相对于当前图式、任务和已有证据的边际非冗余性，并允许显式的 refinement、replication、interaction test 与 representation change。
5. **给出可积累搜索智能的非参数化形式。** 基础模型权重可以保持冻结；“学习”发生在有界、证据化、可预测且可版本化的共享搜索状态中。

---

# 2. 术语、范围与相关工作

## 2.1 术语与问题范围

本文研究的任务设置称为 **评价器引导的智能体优化（evaluator-guided agentic optimization）**：系统允许语言模型或代码智能体修改代码、配置、算法、研究工件或其他可执行对象，并通过自动评价器获得可复现反馈。本文不把 `AutoResearch` 用作方法类别；该词仅指 Karpathy AutoResearch 等明确命名的具体系统。对方法范式，本文采用以下术语。

| 术语 | 本文含义 | 与常见说法的关系 |
|---|---|---|
| 模型主导的单当前候选搜索 | 模型从当前候选与历史直接提出下一项修改，系统通常只正式保留一个 incumbent | 替代 `pure-agentic-loop`、`AutoResearch-style loop` 作为类别名 |
| 显式控制器式 LLM 搜索 | LLM 负责生成候选，进化、树搜索、bandit 或其他控制器负责拓扑、选择和预算分配 | 替代 `agent with algorithm` |
| 搜索账本（Search Ledger） | append-only 的干预、工件变化、执行上下文和结果事实 | 不等同于自然语言 memory |
| 搜索图式（Search Schema） | 当前任务中用于组织对象、干预、上下文、机制、假设与行为的可修订表示 | 不宣称枚举完整客观搜索空间 |
| 共享搜索状态（Shared Search State） | 由账本、图式、覆盖、活动预留、候选状态与预算构成的权威决策状态 | 不等同于 `plans.md` 或原始日志 |
| 搜索足迹（Search Footprint） | 一项方案或已执行事件在当前图式下的多视图投影 | 用于判断覆盖与重合 |
| 边际非冗余性（Marginal Non-Redundancy） | 新增计算相对于已完成和正在执行的搜索提供的额外解覆盖或信息价值 | 是本文对“正交”的正式替代 |
| 原子计划准入（Atomic Plan Admission） | 在执行前，版本检查、审核、预算扣除与足迹预留的一体化状态转换 | 工程原语可命名为 `AtomicPlan` |
| 验证证据提交（Verified Evidence Commit） | 执行后将真实工件变化与评价结果写入权威状态的原子转换 | 候选失败仍可产生有效证据 |
| 评价器（Evaluator） | 产生目标分数或多维性能结果 | 上位概念；可以包含多个 verifier 与 benchmark |
| 验证器（Verifier） | 检查正确性、合法性、安全或不可违反的硬约束 | 不应与优化目标本身混同 |
| 基础模型能力（Base-Model Capability） | 冻结模型在生成、推理、代码修改和判断上的能力 | 不等同于完整搜索系统的覆盖、相关性和计算效率 |

本文使用 **搜索图式诱导（Search Schema Induction, SSI）** 指代从任务描述、工件、干预与结果中构造并持续修订上述表示的过程；使用 **事务化搜索协同（Transactional Search Coordination, TSC）** 指代多个串行或并行 worker 围绕版本化共享状态提交计划与证据的协议。二者合起来构成本文提出的概念框架，但 SSI 是更根本的表示问题，TSC 是建立在该表示之上的并发与一致性机制。

## 2.2 模型主导的单当前候选搜索

ReAct 将语言推理与环境动作交替组织，使模型可以根据外部观测更新计划 [1]。Reflexion 进一步将失败反馈压缩成语言记忆，在不更新模型权重的条件下改善后续尝试 [2]。这类方法的共同特征是：搜索策略主要隐含在模型参数、提示和当前上下文中，外部系统只提供工具、记忆和评价。

Karpathy AutoResearch 将这种哲学推向极简形式：Agent 修改训练代码，在固定墙钟预算下运行实验，根据指标保留或丢弃修改，然后重复循环 [14]。该设计证明了强模型与清晰 evaluator 的组合可以在非常薄的 harness 中持续产生改进，但其外循环本质上仍接近单当前候选的局部爬山。失败的代码可以被回退，而失败所覆盖的搜索区域通常只以简短日志或模型上下文存在。

这类系统的优势是无需预先枚举变量、算子或完整空间，Agent 可以同时修改算法、实现、超参数和表示方式。其缺点不是“模型能力弱”，而是模型作为搜索过程时具有稳定的条件生成偏置：给定相似的 baseline、反馈和上下文，多个 rollout 倾向于生成相似的局部改进。模型能力可以持续增强，但更强的 proposal generation 并不自动提供覆盖未知区域、控制相关性和维护全局搜索记忆的机制。

## 2.3 显式控制器式 LLM 搜索

Tree of Thoughts 通过显式维护多个中间思路、评价分支并允许回溯，扩展了单向语言生成 [3]。FunSearch 将 LLM 作为程序生成器，以自动 evaluator 和岛模型维护高质量且多样的程序种群 [4]。AlphaEvolve 使用多个 LLM、进化式候选管理与一个或多个评价器迭代修改代码，展示了在算法发现与计算基础设施优化中的广泛适用性 [5]。

近期工作进一步探索如何超越模型主导的单当前候选基线。GEAR 将单当前候选替换为有界种群和研究状态前沿，并比较了提示内种群管理、固定遗传控制器和可演化控制器 [15]。Bilevel AutoResearch 使用外循环修改内循环的搜索机制，将搜索策略本身作为可生成代码 [16]。Arbor 使用持久的 Hypothesis Tree 连接假设、工件、证据与蒸馏知识 [17]。Darwin Gödel Machine 通过 archive 保留可自我修改的智能体变体，Hyperagents 则进一步允许元级改进过程本身被修改 [20,21]。这些工作说明“搜索策略也可以被搜索”，但也暴露出递归偏置：提升到 meta-level 不会自动得到无偏搜索器，而只是把偏置移动到新的表示层。

这些方法显著推进了长时程探索，但仍保留一个共同前提：系统需要以某种结构定义“不同路径”“同一节点”“新颖性”或“搜索状态”。树、种群、岛、控制器和假设图提供了组织形式，却未必解决开放式任务中空间语义如何产生的问题。当节点描述主要由自然语言生成，两个看似不同的分支可能实际修改同一机制；当结构固定为配置向量或人工行为描述符时，系统又回到依赖领域专家定义空间。

## 2.4 Quality-Diversity 与行为描述符

MAP-Elites 不只追求单一最优解，而是在用户指定的行为维度上保留多个局部精英，从而“照亮”高质量解在空间中的分布 [6]。Quality-Diversity（QD）方法说明，维护不同 niche 可以保留 stepping stones、提高鲁棒性并缓解局部极值问题。

然而，QD 的关键瓶颈正是行为描述符。人工描述符准确但依赖先验知识；AURORA 等工作尝试从原始行为数据中无监督学习描述符，并在表示更新后重新映射 archive [7,8]。这一方向与本文高度相关：真正困难的并非维护 archive，而是确定哪些差异值得被系统当作空间维度。

与 QD 不同，本文关注的对象不一定是最终行为或策略，也可能是代码干预、研究假设、系统修改和执行证据。我们同时要求空间表示可在执行前支持并发 reservation，在执行后根据真实 artifact 和 outcome 被修正。因此，仅使用无监督 latent embedding 仍不足以构成官方共享搜索状态。

## 2.5 搜索、学习与状态抽象

Expert Iteration 与 AlphaZero 展示了搜索与学习之间的互补：昂贵搜索产生比当前策略更强的行为，学习将搜索结果摊销进可泛化模型，更新后的模型再指导下一轮搜索 [9,10]。本文不要求模型训练，但继承了其核心思想：搜索历史不应只是一次性计算，而应被压缩为后续决策能力。

状态抽象研究关注如何从高维观测中学习一个对未来决策足够的低维状态。HOMER 等方法在未知潜在状态空间中交替进行表示学习与战略探索 [11]；bisimulation 类方法则以未来奖励、转移或下游控制行为的等价性定义可合并状态 [12,22]。本文将这一思想迁移到开放式搜索：两个搜索事件是否属于同一区域，不由文字相似度决定，而由区分它们是否会改变后续搜索决策决定。

Voyager 通过可执行技能库在不微调模型的情况下积累长期能力 [13]。本文同样采用非参数化学习视角，但积累对象不是任务技能本身，而是“当前任务的搜索图式模型”：哪些干预已经发生、哪些机制被支持或反驳、哪些维度需要拆分，以及下一步新增计算应覆盖什么。

## 2.6 本文定位

本文不提出另一种固定树搜索、进化算法或 meta-controller，而是试图提供这些方法共同缺少的底层表示层：


| 方法类别 | 搜索状态从何而来 | 主要优势 | 核心限制 |
|---|---|---|---|
| 模型主导的单当前候选搜索 | 模型上下文、自然语言反思、当前工件 | 开放、通用、低工程约束 | 覆盖不可见，重复难以判断 |
| 显式控制器式 LLM 搜索 | 人工定义的树、种群、配置或行为空间 | 探索可控、易于度量 | 表示与算法依赖领域设计 |
| 本文：搜索图式诱导（SSI） | 从实际干预、上下文和结果中在线诱导 | 开放且可审计，可随证据修订 | 图式诱导与验证本身具有挑战 |

---

# 3. 问题定义

## 3.1 可验证的开放式优化

设任务由以下元素构成：

- 初始工件或解 $x_0 \in \mathcal{X}$；
- 可执行环境与上下文 $c \in \mathcal{C}$；
- 验证器 $V$，用于检查合法性、正确性与安全约束；
- 评价器 $E$，产生可复现的标量或多维结果 $R(x,c)$；
- 预算 $B$，限制候选数、时间、token、设备使用或实验成本。

在第 $t$ 轮，Agent 根据当前可见状态提出一个计划 $p_t$，执行后产生候选 $x'_t$、轨迹 $\tau_t$ 和结果 $y_t$：

$$
p_t \sim \pi_\theta(\cdot \mid x_t, S_t),
$$

$$
(x'_t,\tau_t,y_t)=\operatorname{Execute}(p_t,x_t,c),
$$

其中 $\pi_\theta$ 是由基础模型、提示、工具与 harness 共同定义的 proposal policy，$S_t$ 是系统维护的搜索状态。

传统单链系统经常只更新当前最优解：

$$
x_{t+1}=
\begin{cases}
 x'_t, & V(x'_t)=1 \land R(x'_t)>R(x_t),\\
 x_t, & \text{otherwise}.
\end{cases}
$$

若实验失败，候选代码被回退，系统在工件层面恢复到旧状态。然而，一次实验即使未改善分数，也可能排除一个假设、暴露一个交互项、揭示一个 evaluator 缺陷或表明某个区域已接近饱和。因此，完整系统还应更新：

$$
S_{t+1}=U(S_t,p_t,x'_t,\tau_t,y_t).
$$

本文研究的核心正是 $S_t$ 的表示与更新。

## 3.2 相关 rollout 与无效并发

设 $N$ 个并发 rollout 在近似相同的条件下生成：

$$
p_t^{(i)} \sim \pi_\theta(\cdot \mid x_t,S_t), \qquad i=1,\ldots,N.
$$

当模型、提示、baseline 和 harness 相同，$p_t^{(i)}$ 之间通常存在显著相关性。即使采样温度不同，模型也可能围绕相同高概率机制生成不同措辞或局部变体。于是：

$$
\operatorname{EffectiveCoverage}
\bigl(\{p_t^{(i)}\}_{i=1}^{N}\bigr)
\ll N.
$$

增加并发只是增加 rollout 数量，并不保证增加边际覆盖。本文称并发 rollout 对相同搜索区域的无意识占用为 **空间碰撞（spatial collision）**。

单链 loop 中也存在同构问题。不同时间生成的计划 $p_t$ 与 $p_{t+k}$ 可能在自然语言上不同，却针对相同对象、相同机制和相同假设做近似干预。本文称之为 **时间碰撞（temporal collision）**。

空间碰撞与时间碰撞的共同根因是：系统无法以稳定、结构化的方式回答“已经搜索过什么”。因此，并发与串行不是两个独立问题；单链可以被视为多个 rollout 沿时间顺序到达的特殊并发。

## 3.3 解状态与搜索状态

本文区分两个状态：

- **解状态（Solution State）**：当前被保留的候选、代码、参数和最优分数；
- **搜索状态（Search State）**：对已发生搜索事件、当前空间结构、活动方向、证据与不确定性的表示。

核心不变量为：

> **Solution State 可以回退，共享搜索状态不应随之回退。**

当一次修改失败并被 `git reset` 时，工件可以恢复，但“该干预在该上下文中失败”“该机制已被测试”“该方向与某个变量存在交互”的证据应继续存在。否则系统会重复支付同一份搜索成本。

## 3.4 显式空间与开放式空间

在传统超参数优化中，空间通常预先给定：

$$
z=[z_1,z_2,\ldots,z_d], \qquad z_i \in \mathcal{Z}_i.
$$

此时距离、覆盖、重复和正交性都容易定义。然而，开放式 Agent Search 允许修改：

- 参数与配置；
- 代码结构与算法逻辑；
- 数据流与系统接口；
- 表示方式与问题分解；
- evaluator、运行策略与实验流程；
- 假设本身。

这时完整空间在搜索开始前并不可知，甚至会因新发现而改变。本文因此不假设一个静态的 $\mathcal{Z}$，而研究如何从搜索事件中在线诱导一个动态表示 $\Omega_t$。

## 3.5 基础模型能力与搜索过程属性

“模型能力越来越强”与“搜索过程自动获得有效探索”并不矛盾。基础模型能力描述冻结模型在生成、推理、代码修改与局部判断上的水平；完整搜索行为则由模型、提示与工具脚手架、共享状态、评价信号、接受规则和计算分配共同决定。即使模型对单次方案的质量持续提高，由同一条件分布生成的多个 rollout 仍可能高度相关。

因此，本文并不主张当前模型的 exploration capability 随规模下降，也不把“模型能力”拆成彼此独立且可直接排序的标量。更准确的命题是：

> **更强的基础模型通常提高 proposal quality，但并不保证新增 rollout 具有同比增长的边际覆盖。**

本文关注的正是后一个系统属性。搜索图式和共享搜索状态不替代基础模型能力，而是改变模型每次提案所条件化的信息，使新增计算显式面向尚未覆盖或尚未判明的区域。

---

# 4. 搜索图式诱导（Search Schema Induction）

## 4.1 为什么“文本到空间节点”是病态映射

自然语言计划通常只表达局部意图。例如：

> 基于当前版本，将参数 B 改为 128。

该描述隐藏了大量决定其搜索意义的上下文：B 的原值、所属模块、父候选、同时发生的其他修改、硬件与 shape、编译器版本、预期机制、实际执行路径以及结果分布。一个真实搜索事件更接近：

$$
e_i=(b_i,\Delta_i,c_i,\tau_i,y_i,q_i),
$$

其中：

- $b_i$：父工件或 baseline；
- $\Delta_i$：实际 artifact delta；
- $c_i$：环境、任务实例与约束；
- $\tau_i$：执行、编译、测试或 profiler 轨迹；
- $y_i$：验证结果与多维 outcome；
- $q_i$：字段来源与可信度 provenance。

自然语言仅是对 $e_i$ 的有损描述：

$$
d_i=\operatorname{Describe}(e_i).
$$

因此，不应将 Agent 生成的文本当作空间事实，也不应假设存在唯一稳定的映射 $d_i \mapsto z_i$。

## 4.2 Plan 是干预边，而不是静态点

在配置优化中，一个候选可以被视为空间中的点。开放式搜索中的一次计划则更像一条有方向的干预边：

$$
p_i:(b_i,c_i)
\xrightarrow[\text{hypothesis}]{\text{intervention}}
 x'_i.
$$

相同修改在不同 parent 或 context 下可能具有不同意义；不同代码修改也可能通过相同机制产生近似行为。因此，计划的身份必须包含基准、干预、上下文和假设，而不能只看最终候选或文字摘要。

## 4.3 通用干预元 Schema（Intervention Meta-Schema）

本文提出一个跨任务通用的干预元 Schema：

$$
P=(B,T,I,C,H,O),
$$

其中：

- **Base ($B$)**：方案基于哪个已提交状态或候选；
- **Target ($T$)**：修改哪个对象、模块、变量或概念；
- **Intervention ($I$)**：实施什么可执行变化；
- **Context ($C$)**：变化在哪些环境、输入、资源与约束下成立；
- **Hypothesis ($H$)**：为何预期该干预有效，依赖什么机制或瓶颈判断；
- **Expected Observation ($O$)**：什么结果将支持、反驳或区分相关假设。

该元语法不包含 kernel、训练、RAG 或编译器等领域词汇，因此可以跨场景保持稳定。它规定“一个搜索计划必须说明什么”，但不预先规定“任务中有哪些合法对象和机制”。

## 4.4 领域本体与运行时 Schema

本文区分四个层次：

| 层次 | 含义 | 生命周期 |
|---|---|---|
| 干预元 Schema | 干预、上下文、假设与观测的通用语法 | 跨任务固定 |
| Domain Search Ontology | 领域中的对象、机制、上下文与关系词汇 | 场景级，可由 LLM 初始化 |
| 运行时 Search Schema | 当前运行中实际重要的维度、层级与等价关系 | 随证据动态修订 |
| Search Footprint | 单个 AtomicPlan 在当前图式下的投影 | 每次计划与执行生成 |

例如，在 kernel 优化中，领域本体可以包含 tiling、layout、thread mapping、pipeline、vectorization、memory hierarchy、shape regime 和 occupancy；在模型训练中则包含 architecture、optimizer、schedule、regularization、data、throughput、stability 与 generalization。

这些不是两套不同的“元模板”。二者共享同一个干预元 Schema，只是拥有不同的 ontology。更进一步，场景级本体 只应作为 warm-start prior，而不是不可修改的完整空间。

## 4.5 Typed Search IR

Agent 的自由语言方案不应直接写入官方共享搜索状态。系统应将其编译为带类型的 Search IR：

```json
{
  "base_state_version": 42,
  "base_candidate_id": "c017",
  "targets": [
    {
      "entity": "matmul.schedule.tile_n",
      "operation": "set",
      "before": 128,
      "after": 256
    }
  ],
  "context": {
    "backend": "ascend_910b",
    "dtype": "fp16",
    "shape_regime": "large_square"
  },
  "hypothesis": {
    "bottleneck": "memory_latency",
    "mechanism": "increase_data_reuse"
  },
  "expected_observation": {
    "latency": "decrease",
    "memory_traffic": "decrease",
    "register_pressure": "not_materially_increase"
  }
}
```

其中可以由系统确定的字段——例如 parent hash、实际 diff、changed symbols、运行环境、verifier 版本和指标——不得仅依赖 Agent 自述。LLM 的角色是解释、补全和提出 schema 候选；runtime、parser、compiler 与 verifier 负责生成或验证可观测事实。

## 4.6 多视图空间

开放式搜索不适合被压缩成一棵单一分类树。一个事件可以同时属于多个视图：

$$
\phi_{\text{artifact}}(e),\quad
\phi_{\text{config}}(e),\quad
\phi_{\text{mechanism}}(e),\quad
\phi_{\text{context}}(e),\quad
\phi_{\text{epistemic}}(e),\quad
\phi_{\text{behavior}}(e).
$$

各视图分别回答：

- **Artifact view**：实际修改了哪些文件、符号、AST/IR 子图；
- **Configuration view**：哪些显式参数发生了什么变化；
- **Mechanism view**：方案试图通过什么因果机制改善；
- **Context view**：在哪些输入、硬件、预算或运行 regime 下适用；
- **Epistemic view**：该实验试图回答哪个问题或区分哪些假设；
- **Behavior view**：实际产生了什么 profiler、错误或 outcome signature。

同一事件可以在 config view 中与另一事件重复，但在 context view 中不同；也可能在代码结构上不同，却在 mechanism 和 epistemic view 中高度重合。

## 4.7 形式化定义：图式、足迹与决策等价

**定义 1（搜索图式）。** 在时刻 $t$，搜索图式写作

$$
\Omega_t=(\mathcal{O}_t,\Phi_t,\mathcal{R}_t,\mathcal{Q}_t),
$$

其中 $\mathcal{O}_t$ 是对象与概念本体，$\Phi_t$ 是从计划或事件到多视图描述的投影族，$\mathcal{R}_t$ 是类型与关系约束，$\mathcal{Q}_t$ 记录字段来源、置信度和版本。图式不是完整解空间，而是当前系统用于比较计划和压缩历史的表示假设。

**定义 2（搜索足迹）。** 给定计划或事件 $u$，其在图式 $\Omega_t$ 下的足迹为

$$
F_{\Omega_t}(u)=\{\phi_k(u)\mid \phi_k\in\Phi_t\}.
$$

足迹可以是稀疏、多标签和部分未知的；同一事件不要求落入唯一节点。

**定义 3（决策等价）。** 若在当前证据与预算下，将事件 $e_i$ 与 $e_j$ 区分开不会显著改变结果预测、重复判断或下一步方案排序，则称二者在 $\Omega_t$ 下近似决策等价，记作 $e_i\sim_{\Omega_t}e_j$。该关系随新证据变化，因此不是事件的永久身份。

**定义 4（图式质量）。** 图式质量不由语言上的完整性决定，而由其压缩后的状态对未来搜索决策的损失决定。一个有效图式应在受限复杂度下同时降低结果预测误差、重复判断误差和方案排序损失。

## 4.8 事件永久，抽象暂定

本文采用事件溯源原则：

> **Events are permanent; abstractions are provisional.**
>
> 搜索事件是永久事实，搜索空间只是当前最有用的解释。

所有执行过的计划都产生不可变 SearchEvent。空间节点不是独立事实，而是对事件集合的查询、聚类或等价类。设截至时刻 $t$ 的事件集合为：

$$
\mathcal{E}_t=\{e_1,e_2,\ldots,e_t\}.
$$

当前图式 $\Omega_t$ 定义一个等价关系：

$$
e_i \sim_{\Omega_t} e_j,
$$

表示在当前任务、证据与决策粒度下，系统认为区分两者不会改变下一步搜索决策。当前空间可被视为商空间：

$$
\mathcal{Z}_t=\mathcal{E}_t / {\sim_{\Omega_t}}.
$$

随着新证据到来，关系可以变化：

$$
\sim_{\Omega_t} \neq \sim_{\Omega_{t+1}}.
$$

原先属于同一宽泛节点的事件可以被拆分，原先分开的节点也可以被合并。历史事件无需修改，只需在新 schema 下重新索引。

---

# 5. 版本化共享搜索状态

## 5.1 状态定义

第 $v$ 个版本的官方共享搜索状态定义为：

$$
S_v=
\bigl(
 x_v^\star,
 \mathcal{E}_v,
 \Omega_v,
 \mathcal{C}_v,
 \mathcal{A}_v,
 \mathcal{B}_v
\bigr),
$$

其中：

- $x_v^\star$：当前 incumbent 或 Pareto/frontier 状态；
- $\mathcal{E}_v$：不可变 SearchEvent 账本；
- $\Omega_v$：当前版本的领域本体 与 运行时搜索图式；
- $\mathcal{C}_v$：由事件和 schema 派生的 coverage state；
- $\mathcal{A}_v$：已提交但尚未完成的 active reservations；
- $\mathcal{B}_v$：剩余预算、硬约束和运行政策。

这里必须区分三类对象：搜索账本回答“发生过什么”，搜索图式回答“当前用什么结构解释这些事件”，共享搜索状态回答“在当前版本下，下一步决策需要看到什么”。三者若被混成一个自由文本文件，事实、解释与计划会相互覆盖，系统也无法支持可靠的重索引或并发准入。

搜索状态不是全部原始日志的简单拼接，而是一个有界、结构化、带 provenance 的决策表示。原始日志和工件保存在 ledger 中；$S_v$ 只暴露对规划必要的压缩视图。

## 5.2 字段可信度与 Provenance

每个描述字段必须标记其来源：

- `observed`：由实际 artifact、运行环境或 verifier 直接得到；
- `agent_declared`：Agent 在执行前提出的意图；
- `system_inferred`：parser、静态分析或 LLM 从证据中推断；
- `experimentally_supported`：与预注册预测和 outcome 一致；
- `experimentally_contradicted`：被结果反驳；
- `unknown`：当前无法可靠分类。

例如“降低 memory traffic”如果只来自 Agent 的解释，就不能被当作 verified behavior。它可以作为 mechanism hypothesis，但需要 profiler 或其他结果支持。

## 5.3 Declared Footprint 与 Realized Footprint

执行前，系统只能获得计划声明的 footprint：

$$
F_i^{d}=\operatorname{Project}(P_i,\Omega_v).
$$

它用于并发冲突检查和 reservation。执行后，系统根据实际 diff、轨迹与 outcome 生成：

$$
F_i^{r}=\operatorname{Project}(e_i,\Omega_{v'}).
$$

两者可能不一致。一个声称只调 tile 的计划可能同时改动 pipeline 与 thread mapping；一个声称测试 memory bottleneck 的实验可能实际被编译错误主导。Verified Evidence Commit 应显式记录：

- `aligned`；
- `partially_drifted`；
- `materially_drifted`；
- `unclassifiable`。

Declared Footprint 是有不确定性的执行前 reservation，Realized Footprint 才是更新 coverage 的主要事实依据。

## 5.4 搜索状态不是 `plans.md`

滚动计划文件通常只保存“下一步准备做什么”，容易随当前模型偏好被重写，也无法稳定区分事实、解释与猜测。一个有效搜索状态至少需要满足：

1. **证据落地。** 重要 claim 必须关联具体 SearchEvent；
2. **版本化。** 任何 schema、coverage 或 reservation 更新都产生新版本；
3. **有界压缩。** 决策视图必须在固定预算内总结长期历史；
4. **可预测。** 状态应帮助预测 outcome、重复度或下一步价值；
5. **可证伪。** Agent 必须在执行前记录 hypothesis 与 expected observation；
6. **可重索引。** schema 修订时历史事件可以重新投影。

Memory 只回答“发生过什么”；共享搜索状态还必须支持“新的计划可能发生什么，以及它相对已有工作新增了什么”。

## 5.5 决策充分性

理想 共享搜索状态是完整历史 $H_t$ 的近似 sufficient statistic。给定新计划 $p$，希望：

$$
P(Y, D^\star \mid H_t,p)
\approx
P(Y,D^\star \mid S_t,p),
$$

其中 $Y$ 表示计划 outcome，$D^\star$ 表示后续最优或高价值决策。该条件不要求搜索状态重建全部历史，只要求保留对未来搜索有用的信息。

因此，一个好的 schema 不是语义上最漂亮或最细致的分类，而是能够：

- 识别无意识重复；
- 预测不同 context 下的 outcome 差异；
- 指示哪些维度已饱和、哪些交互尚未测试；
- 支持并发任务分配；
- 在固定状态预算下减少未来决策损失。

## 5.6 Search-Time Learning

本文中的“学习”不等价于更新模型权重。基础模型参数 $\theta$ 可以保持冻结，变化的是外部状态：

$$
S_{t+1}=U_\theta(S_t,e_{t+1}).
$$

这是一种任务内、非参数化的 Search-Time Learning。可以用一句话概括：

> **模型是跨任务搜索经验的参数化压缩；共享搜索状态是当前任务搜索经验的非参数化压缩。**

每次搜索应留下两个产物：一个可能更好的 solution，以及一个更好的 search-space model。后续可以将稳定的跨任务规律编译成 skill、workflow、程序算子，甚至用于模型训练，但这些不是本文方法成立的前提。

---

# 6. “正交性”的严格化：从直观比喻到边际非冗余

## 6.1 正交性依赖 Schema

在开放式搜索中，不能定义一个与任务无关的二值函数：

$$
\operatorname{Orthogonal}(p_i,p_j)\in\{0,1\}.
$$

更合理的是：

$$
\operatorname{Overlap}(p_i,p_j\mid\Omega_v).
$$

两个计划是否重复，取决于当前图式认为哪些区别重要。例如：

- 改变 `tile_n` 与改变 `pipeline_stage` 在 configuration view 中不同；
- 若二者都用于验证“性能是否受 memory latency 主导”，则在 epistemic view 中可能重合；
- 相同 tile 配置在不同 shape regime 下可能属于不同 context-conditioned 区域；
- 两种完全不同的代码实现若产生相同执行 signature，则在 behavior view 中可能近似。

因此，本文将“正交”定义为：

> **一个新增计划相对于已完成和正在执行的搜索，在当前图式下提供了可解释的边际信息或解覆盖。**

这更准确地称为 **Marginal Non-Redundancy**。

## 6.2 多视图 Overlap Vector

定义两个计划的重合向量：

$$
O_{\Omega_v}(p_i,p_j)=
[
 o_{\text{artifact}},
 o_{\text{config}},
 o_{\text{mechanism}},
 o_{\text{context}},
 o_{\text{epistemic}},
 o_{\text{behavior}}
].
$$

在 Atomic Plan Admission 之前，系统主要能够估计前五项，其中 artifact 也只是预计修改范围；执行后，Verified Evidence Commit 使用实际 diff 与运行结果更新 artifact 和 behavior overlap。

该向量不必由单一模型给出。结构重合可以通过 symbol、AST、IR 和配置字段确定；语义与 epistemic 重合可由 LLM 归一化并附带置信度；behavior overlap 则来自 profiler、指标向量和失败 signature。

## 6.3 合法的重合关系

系统不应机械拒绝所有 overlap。计划需要显式声明其与已有覆盖的关系：

- `new_axis`：探索新的对象、机制、context 或问题；
- `refinement`：在已证明有价值的方向上做局部利用；
- `replication`：处理 evaluator 噪声或复现实验；
- `interaction_test`：测试多个已知维度的组合；
- `alternative_implementation`：以不同结构实现相同机制；
- `representation_change`：改变候选、问题或搜索状态的表示。

真正应被拒绝或要求 rebase 的，是高重合且没有新增信息价值的隐式重复。

## 6.4 计划价值

可以将计划价值抽象为：

$$
U(p\mid S_v)=
\operatorname{EI}(p)
+\lambda\operatorname{IG}(p)
-\mu\operatorname{Redundancy}(p,S_v)
-\nu\operatorname{Cost}(p),
$$

其中：

- $\operatorname{EI}$：预期目标改进；
- $\operatorname{IG}$：对关键假设、空间边界或 schema 的信息增益；
- $\operatorname{Redundancy}$：与 completed 和 active footprint 的重合；
- $\operatorname{Cost}$：token、时间、设备和机会成本。

该表达式是设计原则而非要求精确估计的统一标量。系统可以使用规则、LLM 判断、统计模型或多目标排序实现。其关键是将“新增覆盖”作为一等公民，而不是仅优化即时分数。

## 6.5 串行与并行的统一

对于并发搜索，active reservations 表示其他 worker 正在探索的区域；对于单链搜索，completed coverage 表示过去的自己已探索过的区域。二者共享同一套 overlap 与准入规则：

$$
\text{Parallel collision} \equiv
\text{overlap with active plans},
$$

$$
\text{Temporal collision} \equiv
\text{overlap with completed events}.
$$

因此，“正交化并发”和“避免单链重复”不是两个算法，而是同一搜索状态在不同时间结构下的应用。

---

# 7. 事务化搜索协同（Transactional Search Coordination）

## 7.1 核心思想

并发 Agent 不需要实时共享完整推理，也不应在彼此不可见的情况下自由执行。本文提出：

> **Agents explore in parallel, but the search space advances through atomic commits.**

所有 worker 围绕一个版本化、权威的共享搜索状态工作。Agent 可以私下生成 speculative plan，但只有通过系统审核并原子登记后，计划才成为可执行的官方搜索动作。

## 7.2 版本化搜索方案与 AtomicPlan 原语

AtomicPlan 是针对特定搜索状态版本提出的、带类型的搜索事务：

```json
{
  "plan_id": "p017",
  "base_state_version": 42,
  "base_candidate_id": "c008",
  "typed_ir": {
    "target": "matmul.thread_mapping",
    "intervention": "remap_n_dimension_and_increase_tile_n",
    "context": {
      "backend": "ascend_910b",
      "shape_regime": "large_square"
    },
    "hypothesis": {
      "bottleneck": "memory_latency",
      "mechanism": "coalescing_and_reuse"
    },
    "expected_observation": {
      "latency": "decrease",
      "memory_traffic": "decrease"
    }
  },
  "relation": "new_axis",
  "declared_footprint": {},
  "budget_request": {
    "max_iterations": 8,
    "worker_tier": "deep"
  }
}
```

Atomicity 不意味着整个长时程实验瞬间完成。原子的是 **准入与状态登记**：创建 reservation、扣除预算、分配候选身份、记录 parent 和推进 state version 必须要么全部成功，要么全部不发生。

## 7.3 原子计划准入（Atomic Plan Admission）

Agent 基于 $S_v$ 生成计划 $P_i$：

$$
P_i=\operatorname{Propose}(S_v).
$$

系统执行：

$$
\operatorname{AtomicPlanAdmission}(P_i,S_v)=
\begin{cases}
S_{v+1}, & \text{accepted},\\
S_v, & \text{rejected},\\
\operatorname{RebaseRequired}, & \text{stale or conflicting}.
\end{cases}
$$

审核至少包含：

1. **Version/Freshness**：计划是否基于当前状态，或能否在新版本下安全重放；
2. **Admissibility**：是否满足 edit surface、预算、父节点和安全约束；
3. **Normalization**：自然语言是否成功编译为当前 ontology 下的 Typed Search IR；
4. **Conflict**：是否与 active reservation 高度重合；
5. **Marginal Contribution**：相对 completed coverage 新增什么；
6. **Reservation**：审核通过后占用哪些 footprint。

若两个 Agent 同时读取 $S_{42}$，其中一个先提交并产生 $S_{43}$，另一个不能盲目以旧状态提交。系统应要求重新检查其计划在 $S_{43}$ 下是否仍有边际价值。这相当于对搜索计划使用 optimistic concurrency control。

## 7.4 验证证据提交（Verified Evidence Commit）

Worker 执行已提交计划后，不得直接修改官方共享搜索状态。它提交：

- 实际 artifact delta；
- 运行、编译和 verifier 轨迹；
- 多维指标与失败类型；
- declared 与 realized footprint 的差异；
- 对原 hypothesis 的支持或反驳。

系统验证后执行：

$$
\operatorname{VerifiedEvidenceCommit}(e_i,S_v)=S_{v+1}.
$$

该原子转换包括：

- 将不可变 SearchEvent 追加到 ledger；
- 释放或更新 active reservation；
- 更新 coverage；
- 更新 claim 的证据状态；
- 必要时更新 incumbent/frontier；
- 触发 schema split、merge 或 re-index proposal；
- 推进 state version。

计划可以执行失败，但只要产生可信证据，Verified Evidence Commit 就应更新 共享搜索状态。候选失败不等价于搜索失败。

## 7.5 可见性语义

系统采用类似 `read committed` 的规则。

**私有内容：**

- 未提交的草稿计划；
- worker 的完整内部推理；
- 尚未验证的临时结论；
- 中间代码与未登记的实验。

**全局可见内容：**

- 已提交 AtomicPlan；
- active reservation；
- 已验证 SearchEvent；
- 当前图式、coverage 和 state version；
- 当前 incumbent/frontier。

该设计避免所有 worker 被其他 Agent 的未经验证思路 priming。Agent 不必读取彼此完整轨迹；它只需知道哪些区域已被正式占用、哪些事实已被验证，以及自己的计划需要提供什么新增覆盖。

## 7.6 确定性审核与语义审核

为了避免用另一个 LLM 形成无限递归，审核被拆为：

**确定性部分：**

- state version；
- candidate/parent 存在性；
- budget 与 edit surface；
- reservation 生命周期；
- verifier 与 artifact hash；
- 事务原子性。

**语义部分：**

- 计划的 target、mechanism、hypothesis 和 context 归一化；
- 与已有 footprint 的语义重合；
- 计划属于 refinement、replication 还是 new axis；
- schema 是否过粗或缺少新维度。

LLM 可以提出语义判断，但其输出必须结构化、记录置信度，并受实际 artifact 与 evidence 修正。系统负责决定何时该判断能够进入官方状态。

## 7.7 一致性性质

在以下工程假设下：状态版本写入为线性化原子操作；每个已接受计划的 reservation 与预算扣除同一提交；每个证据事件具有唯一标识并幂等写入，则事务化协同具有两个基本性质。

**性质 1（冲突可串行化）。** 任意一组成功准入的并发计划，都可以按照其提交线性化点排列为一个串行顺序；在该顺序中，每个计划的冲突检查均针对其之前已提交的 reservation 与 coverage。物理执行仍可并行，但官方状态演化具有一致的串行解释。

**性质 2（认知状态不回退）。** 若验证证据提交仅向 ledger 追加事实，则候选工件的回退不会删除既有事件：

$$
\mathcal{E}_{v}\subseteq\mathcal{E}_{v+1}.
$$

结论、置信度和图式可以被修订，但导致这些修订的原始证据不会消失。

上述性质只保证状态一致性，不保证语义分类正确，也不保证被准入的计划在真实行为上完全不同。语义重合仍是需要以声明足迹、实际足迹和后验修订共同处理的近似问题。

## 7.8 统一流程

```text
Versioned SearchState@v
        |
        | read
        v
Agent drafts a private proposal
        |
        v
Compile to Typed Search IR
        |
        v
Plan review + conflict check
        |
        v
Atomic Plan Admission + footprint reservation
        |
        v
SearchState@v+1  -- visible to other agents
        |
        v
Worker executes in an isolated workspace
        |
        v
Verifier + realized footprint extraction
        |
        v
Verified Evidence Commit
        |
        v
SearchState@v+2
```

并发 worker 是 speculative executors；共享搜索状态的演化则通过可串行化提交形成一个一致、可审计的官方搜索历史。

**算法 1：基于搜索图式诱导的事务化智能体优化**

```text
输入：初始工件 x0，评价器 E，预算 B，干预元 Schema M
初始化：事件账本 L0，运行时图式 Ω0，共享状态 S0

while budget remains:
    worker 读取已提交状态 Sv
    worker 生成私有自然语言方案 p
    compiler 将 p 编译为 Typed Search IR，并生成声明足迹 Fd
    runtime 对 (version, admissibility, overlap, budget) 做原子准入
    if rejected or stale:
        worker 基于最新状态 rebase 或重新提案
        continue
    runtime 写入 reservation，推进状态版本
    host 在隔离工件中执行已准入方案
    evaluator 产生验证结果，runtime 提取实际变化与实际足迹 Fr
    runtime 原子追加 Intervention–Outcome Event，释放 reservation
    runtime 更新 coverage、incumbent、claim 与状态版本
    if 预测残差、节点多峰或重复误判触发修订：
        提议并验证 schema split / merge / ontology extension

输出：最佳或 Pareto 工件、不可变事件账本、最终搜索图式与共享状态
```

---

# 8. 运行时搜索图式的产生与修订

## 8.1 Bootstrap：由 LLM 初始化空间假设

搜索开始时，系统可根据 objective、工件结构、verifier、环境和 scenario prior，让 LLM 生成初始 ontology：

```text
Kernel optimization:
  target: tiling, layout, mapping, pipeline, vectorization
  context: backend, dtype, shape regime, compiler
  mechanism: reuse, occupancy, coalescing, utilization

Model training:
  target: data, architecture, objective, optimizer, schedule
  context: model scale, budget, dataset, training regime
  mechanism: stability, capacity, generalization, throughput
```

这些维度只是空间假设。系统不应将初始 ontology 视为完备定义，也不应禁止出现新的对象、机制或 context。

## 8.2 Projection：计划投影到当前 Schema

每个 AtomicPlan 被投影到多个视图，并生成 sparse footprint。Projection 是编译过程，而非自由摘要：

$$
F_i^d=
\{
\phi_k(P_i;\Omega_v)
\}_{k\in\mathcal{V}},
$$

其中 $\mathcal{V}$ 为当前启用的视图集合。无法映射的内容应被标记为 `unknown` 或触发 ontology extension proposal，而不是强行塞入最相近的旧节点。

## 8.3 Revision：用证据改变 Schema

设某一节点 $N$ 包含一组事件。若节点内部出现：

- outcome 的显著多峰；
- 相同描述对应稳定不同的行为；
- 预测误差持续较高；
- 不同 context 下的最优决策不同；
- 大量计划被判为重复但实际结果差异显著；

则当前节点可能过粗。LLM 可以提出潜在 split dimension，例如 `shape_regime × tiling`，但拆分是否进入官方 schema 应由其决策价值验证。

可定义一个抽象目标：

$$
\mathcal{L}(\Omega)=
\mathcal{L}_{\text{outcome}}
+\alpha\mathcal{L}_{\text{redundancy}}
+\beta\mathcal{L}_{\text{decision}}
+\lambda\operatorname{Complexity}(\Omega).
$$

其中：

- $\mathcal{L}_{\text{outcome}}$ 衡量 schema 对实验结果的预测损失；
- $\mathcal{L}_{\text{redundancy}}$ 衡量重复判断错误；
- $\mathcal{L}_{\text{decision}}$ 衡量基于压缩状态选择下一步的损失；
- Complexity 防止 schema 无限碎片化。

若一个 split 显著降低前三项且收益超过复杂度代价，则接受；若两个节点的区分不能改善预测、冲突判断或规划，则可以 merge。

## 8.4 Schema Versioning 与重索引

任何 ontology 或 partition 修改都生成新版本：

```text
schema_v3:
  tiling

schema_v4:
  tiling / large-square
  tiling / skinny-M
  tiling / skinny-N
```

SearchEvent 不变，但它们在 $\Omega_{v+1}$ 下重新投影。正在执行的 AtomicPlan 保留其提交时的 `schema_version` 和 declared footprint；Verified Evidence Commit 同时记录其在最新 schema 下的 realized footprint，确保历史可解释。

## 8.5 Learned Latent Space 的角色

系统可以从 diff、轨迹、profiler 和 outcome 学习 embedding，用于：

- 检索相似事件；
- 发现潜在重复；
- 聚类候选与提出 split；
- 为并发 batch 提供多样性候选；
- 识别人类 ontology 未覆盖的模式。

但 latent space 不应成为唯一官方事实层。无监督表示可能捕捉高方差而非高决策价值的维度；encoder 更新也会造成距离漂移。因此 embedding 必须带版本，支持历史重算，并作为辅助 view 而非硬性永久节点身份。

## 8.6 元 Schema 与场景先验

本文的最终结构不是“每个场景一套独立 meta-template”，而是：

$$
\boxed{
\text{Universal Intervention Meta-Schema}
+
\text{Optional Scenario Prior}
+
\text{Run-time Schema Induction}
}
$$

Scenario template 只用于 warm start，例如提醒 kernel 任务优先检查 layout、tiling 和 pipeline；运行过程中系统必须允许新增、拆分、合并和删除维度。否则，人只是从“手工定义搜索空间”退到“手工定义空间模板”，扩展性问题仍然存在。

---

# 9. 设计性质与理论讨论

## 9.1 无偏搜索不是目标

任何有效搜索都利用任务分布的结构。基础模型、提示、ontology、schema 和 evaluator 都会引入 bias。本文不追求 bias-free search，而追求：

- bias 被外化为 typed claim、schema 和 footprint；
- bias 对环境结果做出可记录预测；
- bias 可以被反例、split、merge 和 re-index 修正；
- 单个 Agent 的语言解释不能直接成为不可变事实。

因此，该框架将模型偏置从隐式、不可检查的生成倾向，转化为 **explicit, falsifiable, and revisable bias**。

## 9.2 Epistemic Monotonicity

目标函数意义上的性能不一定单调；共享搜索状态的证据积累应具有单调性。只要一个实验被正式执行并产生可信 evidence，其事件就不会因候选回退而消失：

$$
\mathcal{E}_{v}\subseteq\mathcal{E}_{v+1}.
$$

这不意味着所有结论永久正确。claim 可以被反驳，schema 可以被重构，但原始 event ledger 保持 append-only。该性质确保系统不会因 solution rollback 而发生 epistemic rollback。

## 9.3 可串行化的并发搜索

AtomicPlan 与版本检查使并发计划对共享搜索状态 的修改可被解释为某一串行 commit 顺序。worker 的物理执行仍可并行，但官方 reservation、证据与 coverage 的演化保持一致性。该性质不保证所有并发计划完全不同，却能保证重合是显式、可审计且经过准入的。

## 9.4 开放空间中的局部完备性

本文不要求在搜索开始前枚举完整空间。系统只需逐步构造“已看到部分”的局部模型：

$$
\Omega_0 \rightarrow \Omega_1 \rightarrow \cdots.
$$

这一点区别于固定 Config Space。系统允许未知维度存在，并通过无法解释的 event、预测残差和行为多峰触发 ontology 扩展。所谓 coverage 只相对于当前图式 成立，而不是宣称已经覆盖客观完整空间。

## 9.5 正交化不保证全局最优

搜索图式诱导主要缓解 proposal collapse 与重复计算。它不能单独解决所有局部极值问题。如果通往更优解需要先接受短期退化，而 selection rule 始终只保留即时改善，则系统仍可能无法跨越 fitness valley。解决该问题还需要：

- 多步计划；
- branch/frontier 保留；
- 暂时接受非改进状态；
- compound intervention；
- 更合适的长期 credit assignment。

本文的贡献是使这些决策可以建立在显式 coverage 与 hypothesis state 上，而非保证任意 landscape 的全局收敛。

## 9.6 可积累搜索智能

当前许多系统 scale 的是 search expenditure：更多 rollout、更长上下文和更多 evaluator 调用。本文关注 search accumulation：新增计算是否改变未来搜索者所能看到的空间。

模型训练是一种参数化积累方式，但不是唯一方式。本文通过 版本化搜索状态实现任务内积累：

$$
\text{Search}
\rightarrow
\text{Event + Evidence}
\rightarrow
\text{Schema/State Update}
\rightarrow
\text{Better Conditioned Search}.
$$

当相同模型在后续回合中以更好的 $S_t$ 为条件时，proposal policy 的参数没有改变，但有效搜索行为已经改变。

---

# 10. 系统实现边界

本文方法可以落在一个 host-neutral 的 Search Runtime 中。基础原则是：runtime 负责官方状态与验证，host 负责 Agent 生命周期，Agent 负责提出与执行方案。

## 10.1 最小数据对象

一个最小实现需要增加以下一等对象：

### SearchEvent

不可变记录：parent、artifact delta、context、trace、outcome、verifier 与 provenance。

### SpaceSchema

版本化记录：domain ontology、views、节点 predicate、split/merge lineage、embedding version。

### AtomicPlan

执行前事务：base state version、Typed Search IR、declared footprint、relation、预算请求。

### AtomicPlanAdmission

原子结果：accepted/rejected/rebase、reservation、candidate allocation、new state version。

### VerifiedEvidenceCommit

执行后事务：realized footprint、verified event、reservation release、coverage/schema/incumbent update。

## 10.2 不需要引入 Supervisor

事务化搜索状态不等价于 runtime 接管 worker 生命周期。runtime 无需等待、终止或实时观察 Agent；它只需要在 worker 启动前完成 Atomic Plan Admission，在 worker 返回后完成 Verified Evidence Commit。并发生命周期可以继续由 OpenCode、Codex、Claude Code 或其他 host 管理。

## 10.3 最小控制流

```text
read_search_state
→ draft_atomic_plan
→ normalize_to_typed_ir
→ submit_plan
→ review / reject / rebase / commit
→ materialize_candidate
→ host launches worker
→ verifier produces evidence
→ submit_evidence
→ evidence_commit
```

在单 worker 情况下，该流程仍然成立；此时 conflict 主要来自 completed history 而非 active reservations。

## 10.4 运行时的事实层与解释层

实现中必须严格分离：

- **事实层**：hash、diff、symbols、环境、指标、trace、verifier；
- **解释层**：mechanism、hypothesis、ontology、图式节点、因果说明。

事实层 append-only；解释层 versioned。任何自动分类都应保留来源和置信度，并允许后续重新解释同一事件。

---

# 11. 与现有 Search Runtime 原型的接口映射

本文框架不要求 runtime 变成生命周期 supervisor。一个合适的边界是：runtime 持有规范、候选工件、隔离 workspace、评价器、得分历史、报告与可提升工件；host 客户端持有 worker 的启动、等待、中断与返回；主协调 Agent 负责调用控制面并作出策略选择。现有原型已经具备 durable state、候选 workspace、verifier execution 和多 host adapter，且明确将 `max_parallel` 作为批次规划提示而非运行时进程监督。

在这一边界下，SSI/TSC 所需的核心增量不是增加 peer communication 或 wait loop，而是把当前以 candidate/iteration 为中心的记录提升为五个一等对象：

1. `InterventionOutcomeEvent`：不可变事实记录；
2. `SearchSchemaVersion`：领域本体、视图、split/merge lineage 与 provenance；
3. `VersionedSearchProposal`（API 可命名为 `AtomicPlan`）：基于特定状态版本的方案与声明足迹；
4. `AtomicPlanAdmission`：版本检查、审核、预算与 reservation 的原子提交；
5. `VerifiedEvidenceCommit`：真实 diff、评价结果、实际足迹和状态更新的原子提交。

当前的 `SearchPlan → start_batch → start_agent_session → host worker → run_verifier` 生命周期可以保持不变，只需在候选物化前增加方案准入边界，并在评价后增加证据提交边界。worker 不需要读取其他 worker 的完整思维或 workspace；它只读取已提交的 reservation、已验证事件、覆盖状态与当前图式。这样可以保持 host-neutral 架构，同时把并发计算从相互不可见的独立 rollout 转化为围绕同一权威搜索状态的 speculative execution。

---

# 12. 局限性与开放问题

## 12.1 搜索图式诱导仍受模型偏置影响

LLM 可能过早建立错误 ontology、忽略罕见维度，或将熟悉的领域概念强加给新任务。本文通过 evidence grounding、预测、版本化和重索引使这些错误可被修正，但不能保证模型一定提出正确抽象。

## 12.2 语义归一化的不确定性

同一方案可能存在多个合理机制解释。实际性能变化也常由多个交互因素共同导致。系统需要允许多标签、置信度和未分类状态，而不能强制单一节点。如何校准 LLM 语义判断仍是关键问题。

## 12.3 观测不足与归因错误

若 verifier 只返回单一分数，系统很难判断一个方案为何成功或失败。搜索图式诱导的质量受 outcome richness 限制。profiler、分 case 指标、失败类别和中间轨迹可以提高可辨识性，但也增加成本与系统复杂度。

## 12.4 Schema 漂移与历史可比性

频繁 split、merge 和 embedding 更新会使 coverage 指标跨版本不可直接比较。系统需要保留 schema lineage、历史重索引结果和版本间映射。如何在稳定性与适应性之间平衡仍未解决。

## 12.5 并发 Reservation 的误拒绝

执行前只有 declared footprint。系统可能因为语义判断过粗而错误拒绝两个实际互补的计划，也可能接受两个最终高度重合的计划。通过 soft conflict、置信度、alternative implementation 和 evidence-after-execution 可以缓解，但无法完全消除。

## 12.6 Verifier 与目标错配

所有证据最终都依赖 evaluator。若指标可被 exploit、噪声过高或与真实目标错配，搜索状态会系统性积累错误知识。冻结 verifier、保存多维指标和 held-out evaluation 是必要条件，但不是本文的核心解决对象。

## 12.7 跨任务迁移

本文首先关注 within-task Search-Time Learning。不同 run 的 schema 能否抽取为可复用 domain ontology、diagnostic skill 或搜索算子，是自然的后续方向。跨任务迁移可能通过外部 skill/workflow 实现，也可能最终用于模型训练，但需要解决 ontology 对齐、任务差异和负迁移。

## 12.8 理论保证

在开放式、可修改表示的空间中，严格的覆盖与收敛保证很难获得。本文提供的是一种系统与表示范式，而非对任意任务的最优性证明。更现实的理论目标包括：事务串行化、事件不丢失、在固定 schema 下的冲突一致性，以及 schema 压缩对决策损失的界。

---

# 13. 结论

本文从一个简单问题出发：为何增加 Agent rollout 数量，常常没有带来同比增长的搜索智能？答案不只是模型缺乏探索，也不只是缺少 MCTS、进化或 UCB。更深层的原因是，系统通常没有显式建模自己已经搜索过的空间。相同模型与 harness 产生的 rollout 因而高度相关；并发在空间上重复，单链在时间上重复。

本文提出搜索图式诱导（SSI）：不预先要求完整配置空间，也不把 Agent 文本直接当作空间节点，而是以通用干预元 Schema 组织计划，从实际 artifact、context、trajectory 和 outcome 中建立不可变事件账本；由 LLM 在线诱导领域本体 与运行时图式，并将每次计划和结果投影为多视图搜索足迹。图式单元是可修订的等价类，而非永久身份。

在此基础上，事务化搜索协同（TSC）使用版本化搜索状态和 AtomicPlan 协议协调并发。Agent 可以并行生成与执行方案，但共享搜索状态只通过原子计划准入和验证证据提交推进。已提交计划和已验证事实全局可见，未提交推理保持私有。候选解可以回退，共享搜索状态持续积累。

该框架最终将正交性从静态几何概念转化为相对于当前图式的边际非冗余，将“可积累的搜索智能”实现为一种无需模型训练的 Search-Time Learning。其核心原则可以压缩为三句话：

> **Plan is an intervention, not a point.**
>
> **Events are permanent; abstractions are provisional.**
>
> **Agents explore in parallel, but the search space advances through atomic commits.**

---

# 参考文献

[1] Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., and Cao, Y. **ReAct: Synergizing Reasoning and Acting in Language Models.** International Conference on Learning Representations, 2023.

[2] Shinn, N., Cassano, F., Berman, E., Gopinath, A., Narasimhan, K., and Yao, S. **Reflexion: Language Agents with Verbal Reinforcement Learning.** Advances in Neural Information Processing Systems, 2023.

[3] Yao, S., Yu, D., Zhao, J., Shafran, I., Griffiths, T. L., Cao, Y., and Narasimhan, K. **Tree of Thoughts: Deliberate Problem Solving with Large Language Models.** Advances in Neural Information Processing Systems, 2023.

[4] Romera-Paredes, B., Barekatain, M., Novikov, A., Balog, M., Kumar, M. P., Dupont, E., Ruiz, F. J. R., Ellenberg, J. S., Wang, P., Fawzi, O., Kohli, P., and Fawzi, A. **Mathematical Discoveries from Program Search with Large Language Models.** Nature, 625:468–475, 2024.

[5] Novikov, A., Vũ, N., Eisenberger, M., Dupont, E., Huang, P.-S., Wagner, A. Z., Shirobokov, S., Kozlovskii, B., Ruiz, F. J. R., Mehrabian, A., Kumar, M. P., See, A., Chaudhuri, S., Holland, G., Davies, A., Nowozin, S., Kohli, P., and Balog, M. **AlphaEvolve: A Coding Agent for Scientific and Algorithmic Discovery.** arXiv:2506.13131, 2025.

[6] Mouret, J.-B., and Clune, J. **Illuminating Search Spaces by Mapping Elites.** arXiv:1504.04909, 2015.

[7] Cully, A. **Autonomous Skill Discovery with Quality-Diversity and Unsupervised Descriptors.** Genetic and Evolutionary Computation Conference, 2019.

[8] Grillotti, L., and Cully, A. **Unsupervised Behaviour Discovery with Quality-Diversity Optimisation.** arXiv:2106.05648, 2021.

[9] Anthony, T., Tian, Z., and Barber, D. **Thinking Fast and Slow with Deep Learning and Tree Search.** Advances in Neural Information Processing Systems, 2017.

[10] Silver, D., Hubert, T., Schrittwieser, J., Antonoglou, I., Lai, M., Guez, A., Lanctot, M., Sifre, L., Kumaran, D., Graepel, T., Lillicrap, T., Simonyan, K., and Hassabis, D. **A General Reinforcement Learning Algorithm that Masters Chess, Shogi, and Go through Self-Play.** Science, 362(6419):1140–1144, 2018.

[11] Misra, D., Henaff, M., Krishnamurthy, A., and Langford, J. **Kinematic State Abstraction and Provably Efficient Rich-Observation Reinforcement Learning.** arXiv:1911.05815, 2019.

[12] Hansen-Estruch, P., Zhang, A., Nair, A., Yin, P., and Levine, S. **Bisimulation Makes Analogies in Goal-Conditioned Reinforcement Learning.** arXiv:2204.13060, 2022.

[13] Wang, G., Xie, Y., Jiang, Y., Mandlekar, A., Xiao, C., Zhu, Y., Fan, L., and Anandkumar, A. **Voyager: An Open-Ended Embodied Agent with Large Language Models.** arXiv:2305.16291, 2023.

[14] Karpathy, A. **AutoResearch: AI Agents Running Research on Single-GPU Nanochat Training Automatically.** Software repository, 2026.

[15] Jeddi, A., Le, M. N., Karaimer, H. C., Derpanis, K. G., and Taati, B. **GEAR: Genetic AutoResearch for Agentic Code Evolution.** arXiv:2605.13874, 2026.

[16] Qu, Y., and Lu, M. **Bilevel Autoresearch: Meta-Autoresearching Itself.** arXiv:2603.23420, 2026.

[17] Jin, J., Hu, Y., Qiu, K., Dai, Q., Luo, C., Dong, G., Li, X., Zhao, T., Ma, X., Zhang, G., Wu, Z., Liu, B., Yang, Z., Li, L., Wang, L., Qian, H., Zhu, Y., and Dou, Z. **Toward Generalist Autonomous Research via Hypothesis-Tree Refinement.** arXiv:2606.11926, 2026.

[18] Wolpert, D. H., and Macready, W. G. **No Free Lunch Theorems for Optimization.** IEEE Transactions on Evolutionary Computation, 1(1):67–82, 1997.

[19] Snell, C., Lee, J., Xu, K., and Kumar, A. **Scaling LLM Test-Time Compute Optimally Can Be More Effective than Scaling Model Parameters.** arXiv:2408.03314, 2024.

[20] Zhang, J., Hu, S., Lu, C., Lange, R., and Clune, J. **Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents.** arXiv:2505.22954, 2025.

[21] Zhang, J., et al. **Hyperagents.** arXiv:2603.19461, 2026.

[22] Zhang, A., McAllister, R., Calandra, R., Gal, Y., and Levine, S. **Learning Invariant Representations for Reinforcement Learning without Reconstruction.** International Conference on Learning Representations, 2021.
