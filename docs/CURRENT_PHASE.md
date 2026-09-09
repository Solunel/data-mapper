# CURRENT_PHASE.md

## Phase 2 — 指标在行 Curated → 可解释观测 Mapping 最小闭环

**阶段状态：实施及最终收口验收已完成；Phase 2 冻结。**

## 阶段目标

在 Phase 1 已经能够稳定形成指标在行 `CuratedDataset` 的基础上，建立第一条只读、可解释、可重放的观测 Mapping 闭环：

```text
Curated Dataset（指标在行）
+ Mapping Request
+ 只读 Ontology Catalog
        ↓
表级 Mapping Plan
        ↓
结构状态：READY | NEEDS_BINDING | BLOCKED
        ↓
Row / Metric Subject Extraction
        ↓
MetricDecision（可以独立于实际值存在）
        ↓
逐行展开 0～N 条 ObservationCandidate
        ↓
Metric 状态：MATCHED | AMBIGUOUS | UNMATCHED | ONTOLOGY_GAP
```

本阶段只解决：

> **如何先看懂一张指标在行的 Curated 表，再把其中不同业务范围、不同期间口径的有效数值展开为可追踪的观测候选，并基于现有 Definition / Knowledge 给出可解释的 Metric Mapping 结果。**

Phase 2 不修改 Curated，不修改正式本体，不生成正式 `ActualObservation` 实例，也不写入 Neo4j。

---

## 1. 对旧版 Phase 2 规划的审查结论

旧版规划来自 Git 提交 `713d728`，较新的调整版本位于 `b93119a`。其中“只读本体、四态 Metric 结果、证据与稳定 ID、禁止自动写本体”等方向仍然正确，但必须根据已经冻结的 Phase 1 和最新 Definition 收缩、修正。

### 1.1 保留的设计

- Mapping 是独立、显式、可重放的阶段产物；
- 先形成表级 Mapping Plan，再逐行投影观测候选；
- `MetricDecision` 与 `ObservationCandidate` 分离；
- Metric Mapping 使用 `MATCHED / AMBIGUOUS / UNMATCHED / ONTOLOGY_GAP` 四态；
- 自动未匹配不能直接升级为正式本体缺口；
- 规则优先，所有自动结论和人工 override 都必须留下证据；
- Definition / Knowledge 只读，Mapping 不产生下游写入副作用。

### 1.2 根据当前事实调整的设计

1. 删除“指标在列”验收范围，只处理 Phase 1 已收敛的指标在行数据；
2. 不建设通用表形分类器，只处理指标在行内部的结构角色绑定；
3. 累计数、期初数和期末数不再机械排除，而是通过 `Period.period_basis` 区分；
4. 多个数值列可以表示同一 Metric 在不同 `business_scope`、`period_basis` 下的值，不能把这些列头机械当成不同 Metric；
5. 表结构状态与 Metric 匹配状态分离，二者不共用 `AMBIGUOUS`；
6. `MetricDecision` 不依赖实际值非空；`ObservationCandidate` 只在数值有效且必要结构角色明确时生成；
7. `period_basis` 进入 `candidate_id` 和候选去重依据，也是未来正式观测业务身份的必要语义；`candidate_id` 与正式实例 ID 不得混用；
8. 当前 Phase 1 契约使用 `confidence`，不沿用旧分支中的 `type_consistency`；
9. Definition / Knowledge JSON 是当前本体加载实现，不是 Mapping Core 的固定输入契约；Mapping Core 只依赖 `OntologyCatalog` 表达的本体语义；
10. 每次 Mapping 必须绑定一个 `ontology_revision`；当前 Metric ID 只是在该 revision 内的概念引用，不承诺跨版本永久稳定；
11. Mapping 核心产物保持存储无关，不为未来 Neo4j 预建 provider / adapter / repository 框架；
12. 当前不接入真实 LLM，不为了提高命中率引入模糊猜测。

---

## 2. 当前输入与本体事实

### 2.1 Phase 1 已冻结的输入能力

Phase 2 直接消费 Phase 1 的 `CuratedDataset`，不重新读取或解析 Excel / CSV。可依赖的信息包括：

- `curated_id`、`raw_dataset_id`、`raw_version_id`；
- `source_file`、`sheet_name`；
- `header_row`、`header_rows`、`context_cells`；
- 原始列名、唯一 Curated 键和原始列位置；
- Data Schema 类型、nullable、样例、推断证据和 `confidence`；
- 类型化数据行及 `source_row`；
- Pipeline 步骤、质量报告和 issues。

Phase 1 保持原始业务行列布局，不判断哪一列是指标、期间、组织、单位或金额。Phase 2 不得要求 Phase 1 执行转置、宽转长或业务语义改写。

现有模拟报表证明了以下输入结构，但不能据此声称已经覆盖全部真实业务数据：

- 利润表、现金流量表：`项目 | 行次 | 本月数 | 本年累计数`；
- 资产负债表：`项目 | 行次 | 年初数 | 期末数 | 同期数 | 增减额/率`；
- 成本费用表：指标在行，多个数值列表达成本归属等业务范围。

### 2.2 最新 Definition

当前 `ActualObservation` 的必填属性包括：

- `id`；
- `organization_id`；
- `metric_id`；
- `business_scope`；
- `source`；
- `period`；
- `actual_value`；
- `unit`；
- `status`。

`Period` 当前包含三个必填字段：

- `period_type`；
- `period_key`；
- `period_basis`。

`PeriodBasis` 当前定义：

- `PERIOD_VALUE`：该期间本身的值；
- `YEAR_TO_DATE`：从所在年度年初累计至该期间的值；
- `PERIOD_BEGIN`：期间起点值；
- `PERIOD_END`：期间终点值。

`business_scope` 在没有额外业务细分时可以使用“公司整体”。存在发电成本、购电成本等细分时，必须绑定真实业务范围，不能仍然机械填“公司整体”。

`BudgetTarget` 也已经具有 `business_scope` 和带 `period_basis` 的 `Period`，但 BudgetTarget Mapping 不属于本阶段。

实施时 loader 必须以实际 Definition 为准校验以上结构，文档不能代替本体真值。

### 2.3 最新 Knowledge

当前 `ontology/Knowledge.json` 包含：

- 161 个 `Metric`；
- 163 条 `CALCULATION`；
- 所有 Metric 当前均为 `DRAFT`；
- 只有 7 个 Metric 配置了非空 aliases；
- 当前没有 `Organization` 实例。

Phase 2 不逐条审查全部业务知识，不使用 `CALCULATION` 做公式推理。Metric 的 `DRAFT` 状态必须进入结果和证据，但 Phase 2 不自行增加“DRAFT Metric 禁止生成候选或禁止后续实例化”的治理规则。

当前 `Unit` 枚举也不能完整表达样例中的所有原始单位，例如“元”。Phase 2 应保留原始单位和约束未满足情况，不得静默改成“万元”，也不得自动修改 Definition。

---

## 3. nano-ontoprompt 借鉴边界

Phase 2 继续以 `docs/NANO_REUSE_ANALYSIS.md` 的真实代码分析为依据，不机械复制 nano Mapping。

### ADAPT

- Mapping 是独立、显式、可重放的操作和产物；
- Mapping 结果记录目标、候选、证据、理由和输入版本；
- 稳定 ID 与幂等思路用于 `mapping_run_id` 和 `candidate_id`。

### REFERENCE

- nano 的人工 Mapping 配置与 review 状态只作为交互设计线索；
- `AutoMapper` 的候选表达可以参考，但候选目标只能来自当前只读 `OntologyCatalog`；
- 正式实例的业务身份和 upsert 只作为后续实例化阶段的参考，不与本阶段的 `candidate_id` 混用。

### IGNORE

- 根据数据集名称、列名或 LLM 输出发明实体类、属性或 Metric ID；
- 未查询目标本体就把所有列映射为同名属性；
- Mapping 后自动创建概念、实例、关系、Logic 或 Action；
- 自动写入 Neo4j / ChromaDB；
- LLM 或规则失败后强行选择候选。

当前仍没有 nano Mapping 模块可以直接 `REUSE`。

---

## 4. 本阶段范围

### 4.1 输入与质量门禁

Phase 2 输入由三部分组成：

1. 一个 Phase 1 `CuratedDataset`；
2. 一个轻量、显式的 `MappingRequest`；
3. 从当前 Definition / Knowledge 只读加载的 `OntologyCatalog`。

默认要求 `quality_report.passed == true`。质量未通过时，表结构状态为 `BLOCKED`，不得继续生成看似成功的 ObservationCandidate；Phase 1 warning 进入 Mapping Report，但不一律阻断。

`MappingRequest` 只包含最小闭环需要的显式信息：

- `curated_id`；
- 可选的指标名称字段 override；
- 可选的一个或多个数值字段 binding；
- 可选的组织、期间、单位和 `business_scope` 常量或来源 binding；
- 可选的已确认 Metric override；
- 可选的本体缺口人工确认；
- Mapping 规则版本。

每个数值字段 binding 至少能够表达：

- `value_field`；
- `business_scope`；
- `period_type`；
- `period_key` 或其来源；
- `period_basis`；
- `unit` 或其来源。

所有 override 和显式 binding 必须进入证据。Metric override 必须指向当前 `ontology_revision` 的 `OntologyCatalog` 中真实存在的 `current_metric_id`；本体缺口确认不能通过虚构 Metric ID 表达。

### 4.2 表级 Mapping Plan

Phase 2 必须先对整张表形成 `TableMappingPlan`，再逐行处理。至少明确：

- 指标名称字段；
- 组织字段、上下文或显式常量；
- 每个数值字段；
- 每个数值字段的 `business_scope`；
- 每个数值字段的 `period_type`、`period_key` 和 `period_basis`；
- 单位字段、上下文或显式常量；
- `source` 的文件、Sheet 和报表上下文来源；
- 未参与本次 Mapping 的字段及原因；
- 使用的自动规则、显式 binding 和仍未解决的问题。

确定性自动绑定只处理少量、明确且唯一的结构。出现多个合理字段、无法解释的多层表头、语义不明确的值列或证据冲突时，必须进入 `NEEDS_BINDING`，不得猜测。

### 4.3 表结构状态

表结构状态只描述“这张表是否已经看懂”，不描述 Metric 是否匹配：

- `READY`：当前 Mapping 范围内的所有必要角色和准备参与 Mapping 的值字段均已完成 binding；其他字段也已明确标记为 ignored 或 out-of-scope，并记录原因；
- `NEEDS_BINDING`：可以识别基本表形，但准备参与 Mapping 的字段中仍有一个或多个必要语义无法唯一确定，需要显式 binding；
- `BLOCKED`：Curated 质量不通过、输入契约无效、`OntologyCatalog` 无法加载或校验失败，或缺少无法继续处理的基础结构。

表结构 `NEEDS_BINDING` 不能借用 Metric Mapping 的 `AMBIGUOUS`。反过来，表结构为 `READY` 也不表示所有 Metric 都能匹配。

只看懂部分值字段不能把整张表标为 `READY`。字段必须在当前 Mapping 范围内完成 binding，或者被明确排除并留下原因，不能静默遗漏。

只要指标名称字段已经明确，即使表结构仍有其他未决 binding，也可以生成对应的 `MetricDecision`；未解决的必要观测角色会阻止 ObservationCandidate 生成。

### 4.4 period_basis 绑定

确定性规则至少覆盖以下明确名称：

| 业务字段 | period_basis | period_type / period_key 处理 |
|---|---|---|
| 本月数、本期数 | `PERIOD_VALUE` | 使用报表所属期间 |
| 本年累计数、明确累计口径 | `YEAR_TO_DATE` | `period_key` 锚定累计截止期间 |
| 期初数 | `PERIOD_BEGIN` | 使用该字段明确指向的期间 |
| 期末数 | `PERIOD_END` | 使用报表所属期间 |
| 年初数 | `PERIOD_BEGIN` | 必须锚定年度起点；不能机械沿用月报月份 |

例如 2025 年 7 月月报中的“本年累计数”可以表示为：

```text
period_type  = MONTH
period_key   = 2025-07
period_basis = YEAR_TO_DATE
```

同一张表中的“年初数”虽然属于 `PERIOD_BEGIN`，但不能直接写成 `MONTH / 2025-07 / PERIOD_BEGIN`，否则会被理解为 7 月期初。只有能够从报表期间可靠得到年度锚点时，才能形成类似：

```text
period_type  = YEAR
period_key   = 2025
period_basis = PERIOD_BEGIN
```

无法可靠确定锚点时进入 `NEEDS_BINDING`。

“本年金额”等语义不明确字段不能仅凭名称决定是年度期间值还是年内累计值，必须显式 binding 或保持未决。

本阶段不解释上年同期、同比、环比、增减额、增减率、差异率等后续比较语义；相关字段进入 ignored / unresolved 列表并保留来源。

### 4.5 business_scope 与一行 0～N 条候选

本阶段支持两种指标在行投影方式。

#### A. 单业务范围、多期间口径

```text
项目 | 行次 | 本月数 | 本年累计数
```

指标名称来自项目字段。本月数和本年累计数可以分别生成：

```text
同一 Metric + 公司整体 + 2025-07 + PERIOD_VALUE
同一 Metric + 公司整体 + 2025-07 + YEAR_TO_DATE
```

它们是两个不同业务事实，不能互相覆盖或在去重时合并。

#### B. 多业务范围

```text
成本项目 | 行次 | 发电成本 | 购电成本 | 管理费用
```

同一成本项目可以按已确认的值字段 binding 生成多个 ObservationCandidate。Metric 仍来自成本项目所在行；数值列分别进入 `business_scope`，不能被当成不同 Metric。

如果多层表头同时包含期间口径和业务范围，例如：

```text
本月金额 / 发电成本
本月金额 / 购电成本
```

每个值字段必须同时绑定：

```text
business_scope = 发电成本 或 购电成本
period_basis   = PERIOD_VALUE
```

多层表头不能仅凭字符串分隔符自动认定业务含义。自动规则不能唯一解释时，由显式 binding 确认。

### 4.6 MetricDecision 与 ObservationCandidate

在 Metric Matcher 之前，Phase 2 先形成逐行语义主体，严格分开：

- `raw_label`：Curated 中的报表展示文本原值，全程保留；
- `comparison_name`：只供当前确定性匹配使用，不回写 Curated；
- `row_role`：`METRIC | GROUP | NOTE | UNKNOWN`；
- extraction / classification evidence：记录每条实际使用的规则、转换前后文本和来源。

第一版主体提取只处理可以明确解释的展示结构：

- `（一）`、`（1）`、`1.` 等报表编号；
- `其中：`、`加：`、`减：` 等明确层级前缀；
- 当前样例注释明确说明的 `* / △ / ▲` 展示标记；
- “损失以负号填列”“净亏损以负号填列”等明确正负号填列说明。

它不删业务词，不做同义词扩写、包含匹配、fuzzy、embedding 或 LLM
判断。明确以“注:”开头的说明行识别为 `NOTE`；明确以“分类/类别/分组:”
结尾的标题识别为 `GROUP`，二者不进入 Metric Matcher。像“其他”这样无法
可靠确认语义主体的宽泛标签保留为 `UNKNOWN` 并继续保守匹配，不通过排除它
人为降低 `UNMATCHED`。显式 Metric override 或本体缺口确认可以把对应源行确认为
`METRIC`，并留下证据。

识别出的最近 `GROUP` 可以作为后续主体的最小分组上下文，但本阶段不建设通用
层级分类器。

`MetricDecision` 针对“指标语义主体（metric subject）”形成，而不是仅按规范化后的指标名称字符串合并。指标语义主体是本阶段能够识别的一处待映射业务指标表达，不要求实际值非空。它至少保留：

- 原始指标名称、比较名称和比较键；
- `source_file`、`sheet_name`、`source_row` 和指标名称的原始列位置；
- 当前可获得的表级、分组和行级上下文；
- Metric Mapping 四态；
- 选中 Metric 或有序候选；
- 使用、拒绝和冲突的证据；
- `ontology_revision`；
- 选中或候选 Metric 的 `current_metric_id`、`name_cn`、status 和 version。

`current_metric_id` 对应当前 `ontology_revision` 中的 `Metric.id`，可以正常参与当前 Mapping，但不被声明为经过长期治理的永久业务 ID。MetricDecision 表达的是“在这个本体 revision 中匹配到了这个 Metric”。本阶段不新造 canonical ID，也不使用 Neo4j internal id、elementId 或其他存储内部标识。

相同显示名称出现在不同 Sheet、不同源行或不同上层业务结构中时，默认分别形成 MetricDecision。只有名称和相关上下文足以证明它们属于同一个指标语义主体时，才可以复用同一个决策。第一版不建设复杂层级语义模型，但不能因名称相同而提前合并“其他”“其中：其他”“利息收入”“管理费用”等真实报表项目。

`ObservationCandidate` 只在以下条件满足时生成：

- 表结构和对应值字段 binding 已经明确；
- 实际值非空且为可接受数值；
- `business_scope` 非空；
- `period_type`、`period_key`、`period_basis` 明确；
- 组织原始值、单位原始值和 source 已被提取或明确报告缺失。

ObservationCandidate 至少包含：

- `metric_name` 原值及其 MetricDecision 引用；
- `actual_value`；
- `business_scope`；
- `organization_value` 和可选的已确认 ID；
- `period_type`、`period_key`、`period_basis`；
- 原始及规范化单位；
- `source_file`、`sheet_name`、`source_row`；
- 指标名称源列和值源列的原始位置；
- Curated / Raw 版本；
- 各角色绑定方式和证据；
- 是否满足当前 Definition 约束，以及未满足原因。

标题行、分组行或所有已绑定数值均为空的行不会生成 ObservationCandidate，但其指标名称仍可产生 MetricDecision，行本身进入空值/未投影统计。

ObservationCandidate 不是正式 `ActualObservation`：

- 不写入 Knowledge 或 Neo4j；
- 不伪造 `organization_id`、`metric_id`、正式实例 ID 或 status；
- Definition 约束未满足时仍可作为候选保留，但必须明确标记不可直接实例化的原因。

### 4.7 稳定身份与去重

`candidate_id` 表示“本次 Mapping 从哪个来源位置产生了哪个候选”，是来源候选身份。它的稳定生成和候选重复判断至少必须区分：

```text
Curated / Raw 版本
+ source_file / sheet_name / source_row / value source column
+ organization
+ ontology_revision
+ metric subject / current_metric_id（存在匹配目标时）
+ business_scope
+ period_type / period_key / period_basis
+ unit
+ Mapping 规则版本
```

`period_basis` 是必要身份字段。同一个 Metric、Organization、business_scope 和 period_key 下，`PERIOD_VALUE` 与 `YEAR_TO_DATE` 是两个不同事实，不得生成相同 `candidate_id`，也不得被候选去重合并。

`candidate_id` 不等于未来正式 `ActualObservation.id`，不得直接复用为正式业务实例 ID。未来正式观测的业务身份应主要由 Metric、Organization、business_scope、period_type、period_key、period_basis 等业务语义决定；不能因为文件名、Sheet、源行或 Mapping 规则变化就自动成为另一个业务事实。Phase 2 不设计正式实例 ID 的算法，只明确二者的边界。

### 4.8 OntologyCatalog

实现一个轻量只读 JSON loader，把当前 Definition / Knowledge 资产转换为 Mapping 所需的 `OntologyCatalog`。JSON 文件读取、路径访问、结构解析和源格式校验全部收敛在 loader 边界内；Mapping Core 不直接读取 JSON，也不理解两个文件的存储结构。

`OntologyCatalog` 至少包含：

- `ontology_revision`；
- `ActualObservation`、`Period`、`PeriodBasis` 的实际约束；
- Metric 的 `current_metric_id`、`name_cn`、`aliases`、`definition_cn`、`business_labels`、`value_semantics`、`status` 和 `version`；
- 重复 Metric ID、正式名称或 alias 冲突检查。

当前 JSON loader 可以结合 Definition / Knowledge 的内容校验值和可用版本信息形成 `ontology_revision`。它是 Mapping Core 使用的不透明本体版本标识；未来来源如何计算 revision 不属于核心匹配逻辑。同一次 Mapping Run 从开始到结束必须使用同一个 revision，不能混用不同本体快照。

Mapping Core 只消费 `OntologyCatalog` 值对象。未来若迁移本体来源，只需由新的读取方式提供等价目录；但本阶段不实现 Neo4j adapter，也不定义通用 provider、repository、plugin 或迁移框架。

### 4.9 Metric 候选匹配与四态

最小确定性顺序：

```text
显式已确认 override
→ 唯一 current_metric_id / 正式名称精确匹配
→ 唯一 alias 精确匹配
→ 受限比较规范化后的唯一匹配
→ value_semantics 明显冲突检查
→ Metric 四态决策
```

比较规范化只用于索引和证据，可以处理首尾/连续空白、全角半角、大小写和明确允许的标点形式差异。它不得改写 Curated 业务名称，不进行删词、同义词扩写、包含匹配或未经确认的模糊匹配。

四态只用于 Metric Mapping：

- `MATCHED`：唯一已有 Metric 满足确定性证据，或存在有效且可追踪的人工 override；
- `AMBIGUOUS`：存在多个合理 Metric 候选或 Metric 证据冲突；
- `UNMATCHED`：没有可靠已有 Metric 候选，或当前项不应映射为 Metric；
- `ONTOLOGY_GAP`：明确存在业务指标概念、当前本体无法表达，并已由显式输入确认。

自动未匹配默认是 `UNMATCHED`，且仅有确定性匹配失败时
`ontology_gap_candidate = false`。报表格式噪声、`GROUP / NOTE / UNKNOWN`、
“其他”等宽泛表达不得自动成为本体缺口候选。只有额外、明确且可追溯的证据才
允许开启候选标记；当前最小实现只接受显式本体缺口确认，并由该确认产生正式
`ONTOLOGY_GAP`，不会虚构 Metric ID。

当前 Metric 没有完整单位知识，因此单位只能作为观测完整性信息和约束提示，不能伪装成严格的 Metric 单位校验。`value_semantics` 可以阻止普通数值与比率之间的明显错误命中。

本阶段不接入真实 LLM。无法确定的 Metric 结果保持 `AMBIGUOUS` 或 `UNMATCHED`。

### 4.10 输出

`TableMappingPlan`、`MetricDecision`、`ObservationCandidate`、`MappingPlan` 和 `MappingReport` 都是纯 Mapping 数据契约，不绑定持久化技术。当前要求它们可以序列化为 JSON，便于测试和检查，但不规定必须保存为 JSON 文件。

这些契约不得包含 JSON path、Neo4j node id / elementId、Cypher、数据库表名等存储实现细节。`MappingPlan` 和 `MappingReport` 至少包含：

- 稳定 `mapping_run_id`；
- Curated / Raw 版本；
- `ontology_revision` 和不含存储内部标识的 OntologyCatalog 摘要；
- MappingRequest 与规则版本；
- TableMappingPlan 和结构状态；
- 按指标语义主体组织的 MetricDecision 明细及逐项证据；
- ObservationCandidate 汇总及明细；
- ignored fields、空值行、未投影值和 unresolved binding 及原因；
- Metric 四态统计；
- Definition 约束满足情况及后续实例化缺失项。

相同 Curated 内容、`ontology_revision`、规则版本和显式输入必须得到相同 `mapping_run_id`、结构状态、`candidate_id`、候选顺序和 Metric 决策。运行时间可以不同，但不能影响结果。

---

## 5. 实施任务

1. 从 Phase 1 synthetic fixtures 构造质量通过的指标在行 Curated 输入；
2. 定义 `MappingRequest`、`TableMappingPlan`、结构状态、`OntologyCatalog`、指标语义主体、`MetricDecision`、`ObservationCandidate`、证据、Metric 四态、`MappingPlan` 和 `MappingReport` 的最小契约；
3. 实现轻量只读 Definition / Knowledge JSON loader，生成带 `ontology_revision` 的 `OntologyCatalog`，并校验 Mapping 必需结构、`PeriodBasis` 和 Metric 索引冲突；
4. 实现 Curated 质量门禁、表结构状态和最小确定性角色绑定；
5. 实现 `PERIOD_VALUE / YEAR_TO_DATE / PERIOD_BEGIN / PERIOD_END` 的值字段 binding；
6. 实现单业务范围及显式多业务范围的一行 0～N 条候选投影；
7. 实现保留 `raw_label`、生成 `comparison_name` 和 `METRIC / GROUP / NOTE / UNKNOWN` 的最小行主体提取；
8. 实现 Metric 正式名、aliases、受限比较键和 override 匹配；
9. 按指标语义主体实现 MetricDecision、四态证据、未决项和 gap candidate，避免仅按名称合并；
10. 实现包含 `period_basis` 的稳定 `candidate_id` 和候选重复判断，并与正式实例 ID 保持明确边界；
11. 生成稳定、可序列化、可重放且存储无关的 Mapping Plan / Report；
12. 完成单元、失败和端到端测试，并运行全部 Phase 1 回归测试；
13. 检查 Curated、Definition、Knowledge 和 nano 参考源码未被 Phase 2 执行路径修改。

---

## 6. 交付物

- Phase 2 最小数据契约与进程内调用说明；
- 只读 JSON loader 与存储无关的 OntologyCatalog 边界；
- `ontology_revision` 及 revision 内的 `current_metric_id` 引用；
- 表级 Mapping Plan 与独立结构状态；
- 带 `raw_label / comparison_name / row_role / evidence` 的逐行主体提取；
- 基于指标语义主体的 MetricDecision 与 Metric 四态结果；
- 单业务范围及显式多业务范围 ObservationCandidate 投影；
- 四种 PeriodBasis binding；
- 包含 `period_basis` 的稳定 `candidate_id`，以及它与正式实例 ID 的边界说明；
- 空值、比较字段和 unresolved binding 报告；
- MappingPlan / MappingReport；
- synthetic 端到端 fixture；
- 质量门禁、结构状态、business_scope、period_basis、Metric 四态和稳定性测试；
- 全部 Phase 1 回归测试结果。

本阶段不要求 Web API、数据库或 UI。最小闭环通过进程内 Python 接口和测试调用。

---

## 7. 验收条件

只有同时满足以下条件，Phase 2 才算完成：

1. 每个输入先形成表级 Mapping Plan，并明确返回 `READY / NEEDS_BINDING / BLOCKED` 之一；只有当前 Mapping 范围内所有必要角色和值字段均已完成 binding，且其他字段均已标记为 ignored 或 out-of-scope 并记录原因时，才能返回 `READY`；
2. 准备参与 Mapping 的字段仍有必要语义无法唯一确定时返回 `NEEDS_BINDING`；输入质量、契约、`OntologyCatalog` 或基础结构导致无法继续时返回 `BLOCKED`；
3. 表结构状态与 Metric 四态完全分离，不使用同一个 `AMBIGUOUS` 表达两类问题；
4. `项目 | 本月数 | 本年累计数` 可以把同一指标的值分别投影为 `PERIOD_VALUE` 和 `YEAR_TO_DATE` 候选；
5. `项目 | 年初数 | 期末数` 可以在期间锚点明确时分别投影为 `PERIOD_BEGIN` 和 `PERIOD_END`，且年初数不会机械沿用错误的月度锚点；
6. “本年金额”等无法唯一判断期间口径的字段进入 `NEEDS_BINDING`，不会靠名称猜测；
7. 同一行指标的多个已确认值字段可以按 binding 生成不同 `business_scope` 的候选，业务范围列头不会被当成 Metric；
8. 同一源行可以根据非空有效值生成 0～N 条候选；空值不生成候选，但指标名称仍可产生 MetricDecision；
9. 同名指标出现在不同来源位置或上下文时，不会仅因比较名称相同而共用 MetricDecision；只有名称和上下文证据足以证明属于同一指标语义主体时才可复用；
10. 每个候选都有明确的 `business_scope`、`period_type`、`period_key` 和 `period_basis`；只有没有额外细分时才能有证据地使用“公司整体”；
11. `period_basis` 进入稳定 `candidate_id` 和候选重复判断；同一期间的本期值与本年累计值不会互相覆盖；
12. `candidate_id` 表达来源候选身份，不被当作未来正式 `ActualObservation.id`；来源位置或 Mapping 规则变化不会被定义为正式业务事实自动变化的依据；
13. Mapping Plan 明确组织、期间、单位、来源、指标名称、实际值和业务范围角色，不是只有“名称 → Metric”的字典；
14. 唯一正式名、唯一 alias 和有效 override 可以产生可解释 `MATCHED`；多 Metric 候选产生 `AMBIGUOUS`；无可靠候选产生 `UNMATCHED`；
15. 明确的报表编号、`其中：`、`加：/减：` 和已确认展示标记只影响 comparison name，不修改 Curated 原值，并记录逐步 evidence；
16. 明确的 `GROUP / NOTE` 不进入 Metric Matcher；无法可靠判定的行保留为 `UNKNOWN`，不会被强行当作非指标排除；
17. 普通 `UNMATCHED`、`GROUP / NOTE / UNKNOWN` 和“其他”等宽泛标签不会自动设置 `ontology_gap_candidate`；
18. 自动未匹配不会成为正式 `ONTOLOGY_GAP`，只有显式确认可以产生该状态；
19. 每个候选和 MetricDecision 可追溯到 Curated / Raw 版本、文件、Sheet、源行、原始列位置、`ontology_revision` 和规则版本；
20. 每次 Mapping Run 只使用一个明确且一致的 `ontology_revision`；Metric 结果以 `ontology_revision + current_metric_id + name_cn + match_evidence` 表达当前版本内的匹配；
21. Mapping Core 可以直接消费内存中的 `OntologyCatalog`，不读取 JSON 路径或理解 JSON 存储结构；
22. 核心 Mapping 契约不包含 JSON path、Neo4j 内部 ID、Cypher、数据库表名等存储实现细节；
23. 当前 Metric 的 `DRAFT` 状态原样记录，但不会被 Phase 2 自行解释为禁止生成候选或禁止实例化；
24. 当前没有 Organization 实例、单位枚举可能不完整等约束缺口被诚实报告，不伪造 ID、单位或本体合法性；
25. 数值类型冲突、质量不通过或 required 结构角色缺失时不会生成成功候选假象；
26. 相同输入、`ontology_revision`、规则版本和 override 得到相同结构状态、稳定 `candidate_id`、候选顺序与 Metric 决策；
27. Mapping 不修改 Curated、Definition 或 Knowledge，也不产生任何图存储写入；
28. Phase 2 新增测试及全部 Phase 1 回归测试通过；
29. `references/nano-ontoprompt-master/` 未修改；
30. 未实现指标在列、正式实例化、Neo4j、真实 LLM、本体演化、同比/环比、BudgetTarget、异常检测、根因定位或前端。

验收报告必须列出实际 fixture、运行命令、测试结果、三种结构状态、Metric 四态样例、business_scope 与 period_basis binding、稳定身份结果、nano 借鉴情况、已知本体限制和未解决项。

---

## 8. 停止条件

当实现和测试足以证明：

1. Phase 1 的代表性指标在行 Curated 数据能够形成可解释的表级 Mapping Plan，且只有全部范围内字段均已绑定或明确排除时才进入 `READY`；
2. 表结构问题与 Metric 匹配问题通过独立状态清楚表达；
3. 同一行的有效数值能够按 `business_scope` 和 `period_basis` 展开为 0～N 条统一 ObservationCandidate；
4. 即使实际值为空，指标语义主体仍能形成独立、可追踪的 MetricDecision，同名但上下文不同的主体不会被提前合并；
5. Metric 四态不会把名称相似、普通未匹配或未确认建议伪装成可靠命中或正式本体缺口；
6. 报表展示文本与 comparison name 分离，明确非指标行不会污染 MetricDecision，普通 `UNMATCHED` 不会自动开启本体缺口候选；
7. `candidate_id` 稳定、可重放，并且与未来正式 `ActualObservation.id` 的业务身份边界清楚；
8. JSON 读取被限制在轻量 loader 中，Mapping Core 只依赖带 `ontology_revision` 的 `OntologyCatalog`；
9. 当前 Metric 引用明确属于某个 `ontology_revision`，核心 Mapping 契约不依赖具体存储实现；
10. Mapping 结果没有本体或下游写入副作用；
11. 产物足以作为后续人工确认、Organization 主数据接入、本体演化或正式实例化的明确输入；

即停止 Phase 2。

不要为了覆盖全部 161 个 Metric、任意表形、复杂比较语义、真实 LLM、图存储或审核平台而扩大范围。

---

## 9. 本阶段不做

- 指标在列或业务宽表 Mapping；
- 同比、环比、上年同期、增减额、增减率、差异率等比较语义；
- BudgetTarget、TargetConstraint 正式 Mapping；
- 自动解释所有多层表头或任意矩阵表；
- 修改、补全、发布或审批 Definition / Knowledge；
- 生成、持久化或发布正式 Ontology Instance Data；
- Organization 主数据建设、实体合并或跨表消歧；
- CALCULATION 公式推理、关系 Mapping、Logic 或 Action；
- 真实 LLM provider、prompt 管理、embedding 或向量数据库；
- Neo4j adapter、查询、写入或迁移；
- 自动修改 Curated 或重新执行 Phase 1 清洗；
- 人工审核 UI、Web API、数据库 CRUD、权限或发布工作流；
- 异常检测、根因定位和前端；
- 为未来多租户、大规模并发或任意本体来源预建平台抽象。

本阶段只完成：

> **把质量可用的指标在行 Curated Dataset 先形成可解释的表级 Mapping Plan，再把有效值按 business_scope 和 period_basis 展开为稳定、可追踪且不会强行猜测的 ObservationCandidate，并给出独立的 Metric Mapping 决策。**

---

## 10. Phase 2.5 方向（仅记录，不实施）

Phase 2 冻结后，确定性规则仍无法可靠匹配的指标可以进入下一阶段：

```text
Phase 2 确定性匹配失败
→ fuzzy candidate retrieval
→ LLM 结合名称、报表上下文、本体定义、alias、计算关系进行语义判断
→ MATCHED / AMBIGUOUS / UNMATCHED
```

其中 fuzzy 只负责候选召回，不直接等于可靠匹配；LLM 判断必须保留输入上下文、
候选、理由、证据和不确定性，失败时仍保持 `UNMATCHED / AMBIGUOUS`。Phase 2.5
不得回写 Curated，也不得静默修改 Definition / Knowledge。

本记录不构成 Phase 2.5 的接口冻结或实施授权。本次 Phase 2 不包含 fuzzy、LLM、
embedding、向量数据库、Neo4j 或新持久化架构。
