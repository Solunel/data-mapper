# Data Mapper Clean Refactor Plan V1.0.2

**状态：V1.0.2 设计已审核冻结；实施尚未开始。**

**适用范围：** 当前仓库在 Phase 1、Phase 2、Phase 2.5 已验证能力之上的职责重构。

**实施原则：** 保留业务行为，不要求保留旧内部 API；按 `R0 → R1 → R2 → R3` 逐级迁移，每一级满足停止条件后才可继续。

本文是本次 Clean Refactor 的实施基线。若实施时发现本文与真实代码契约存在实质冲突，应停止扩大范围，记录事实和最小调整建议，经审核后再修改本文；不得在代码中静默改变冻结边界。

---

## 1. 审核结论

当前方案在 V1.0.1 基础上完成四项实施边界修订，并审核冻结为 V1.0.2。代码事实支持以下判断：

1. 当前 `mapping.py` 的 `map_curated_dataset()` 同时承担 Observation Structuring、Deterministic Metric Resolution 和组合投影，职责确实混合；
2. 当前 `MappingRequest` 同时包含表结构绑定与 Metric Resolution 配置；
3. 当前 `ObservationCandidate` 同时包含源观测、Metric 决策快照、ontology revision 和未来实例化完整性信息；
4. Phase 2.5 的生产能力与 Gold / Recall / Pilot 评测能力存在反向依赖，需要纠正为 `Evaluation → Production`；
5. Phase 1 已稳定，不需要因本次重构改变；
6. 当前项目没有已知外部稳定 API 消费者，允许在仓库内消费者迁移完成后进行 Clean Break。

此前审核提出的边界修订全部吸收，并冻结为硬边界：

- `ResolvedObservation` 只组合 `ObservationDraft` 与 `EffectiveMetricResolution`，不包含 `InstantiationReadiness`；
- `metric_row_hints` 必须由 `ObservationStructuringRequest` 显式提供，正式 workflow 不得从 `MetricResolutionRequest` 反向推导；
- `organization_id` 只允许作为调用方已知引用透传，Observation Structuring 不查询、猜测或验证 Organization，也不因此依赖完整 `OntologyCatalog`。
- Observation Structuring 必须参考 Definition 中 `ActualObservation` 的正式结构作为业务骨架，但只通过窄只读 `ObservationSchema` 使用；`ObservationDraft` 可以比正式实例包含更多来源、追踪和结构化证据信息。
- `ActualObservation` 的 `required` 表示未来正式实例化时必须满足，不是 Draft 生成门禁；Metric / Organization 等尚未解析的正式引用，以及当前尚不能可靠确定的 `status`，不得因此阻塞 Draft，也不得被伪造。
- 表级 `NEEDS_BINDING` 不否定已经完整绑定的局部值；满足逐值投影条件的值仍可生成 `ObservationDraft`，未决部分继续进入报告；
- Metric Resolution 只读消费完整 `ObservationStructuringResult`，在全部确定性决策形成后构造 NOTE、同表指标、顺序、scope、period 和 unit 等语义上下文；
- 只读 loader 将 Definition 中 `ActualObservation` 的窄结构投影装入 `OntologyCatalog.observation_schema`；Structuring 只消费该投影，R1 不新增 status 绑定或推断；
- 人工审核通过后的 `SemanticResolution` 通过纯函数式回放重新派生 `EffectiveMetricResolution` 与 `ResolvedObservation`，不重跑 Structuring、Retrieval 或 LLM，也不产生写入。

除为落实上述边界所必需的窄接口外，不再增加模块或平台抽象。V1.0.2 已审核冻结，下一步只能按本文进入 R0。

---

## 2. 当前架构事实

当前业务链路为：

```text
Excel / CSV
→ Phase 1：Raw / Curated
→ Phase 2：表结构 + 行主体 + 确定性 Metric + ObservationCandidate
→ Phase 2.5：Candidate Retrieval + Semantic Judge + 派生结果
```

主要事实如下：

- `src/data_mapper/contracts.py`、`parsers.py`、`table_structure.py`、`pipeline.py` 承担 Phase 1，当前边界清晰；
- `src/data_mapper/mapping.py::map_curated_dataset()` 一次完成表计划、主体提取、确定性匹配和候选投影；
- `src/data_mapper/mapping_contracts.py::MappingRequest` 混合结构化配置与 Metric Resolution 配置；
- `MetricSubject` 已能表达来源行中的待解析指标主体，可以保留；
- `MetricDecision` 已能表达基于特定 `ontology_revision` 的确定性决策，可以保留；
- `ObservationCandidate` 只有在携带 Metric 决策快照后才能成立，并包含实例化约束字段，不适合作为长期观测中间态；
- `phase25_retrieval.py`、`phase25_semantic.py`、`phase25_deepseek.py` 包含可复用的生产逻辑，但也包含或依赖 Gold / Pilot 评测逻辑；
- `phase25_semantic_contracts.py` 的生产契约反向依赖 `phase25_contracts.py` 中的 `_JsonContract`；
- `a.py` 手工拼装 Phase 2 与 Phase 2.5，应迁移为正式 workflow 的演示调用方；
- `README.md`、`__init__.py` 和 Phase 2 / Phase 2.5 测试直接消费旧 API；
- Definition / Knowledge 由 `ontology_catalog.py` 只读加载，这一边界继续保留。
- OntologyCatalog 是 Mapping Core 面向的只读本体视图，不应假设 Definition 与 Knowledge 来自同一种物理存储。当前可由 JSON 构造；未来允许 Definition 继续使用 JSON、Knowledge 切换为 Neo4j。Deterministic Resolution、Candidate Retrieval、Semantic Resolution 只依赖 Catalog 提供的稳定查询结果，不直接依赖 JSON 或 Neo4j 的具体存储实现。未来切换 Knowledge 存储时，应优先只调整 Catalog 的加载/查询边界，不改变核心匹配流程。

R0 的已知参考基线是 commit `01090b8`，当前历史验收结果为 64 个测试通过。实施 R0 时必须在实际工作分支上重新执行并保存基线，不能仅引用本文记录。

---

## 3. 当前主要职责混合点

### 3.1 `MappingRequest`

属于 Observation Structuring 的字段：

```text
curated_id
metric_name_field
value_bindings
organization
organization_current_id（重构后改为 organization_id 已知引用透传）
report_period_type
report_period_key
unit
ignored_fields
mapping_rule_version 中的 structuring 部分
```

属于 Metric Resolution 的字段：

```text
metric_overrides
ontology_gap_confirmations
mapping_rule_version 中的 matching 部分
```

其中 `metric_overrides` 和 `ontology_gap_confirmations` 目前还会影响行角色识别。这个效果在迁移期要显式保存，但新架构不得继续保留反向耦合。

### 3.2 `ObservationCandidate`

纯源观测事实：

```text
actual_value
business_scope
organization_value
period_type / period_key / period_basis
unit_raw / unit_normalized
source / provenance
value_field / source columns
role_bindings / binding_evidence
```

Metric Resolution 结果：

```text
metric_decision_id
metric_name
ontology_revision
current_metric_id
```

未来 Ontology Instantiation 关注点：

```text
definition_constraints_satisfied
instantiation_missing_fields
```

技术身份 `candidate_id` 还依赖当前 Metric / Mapping 语义，因此不能直接改名后继续充当独立观测草稿 ID。

### 3.3 `map_curated_dataset()`

当前编排大致为：

```text
_build_table_plan
→ _extract_metric_subjects
→ _build_metric_decisions
→ _project_candidates
→ MappingPlan / MappingReport
```

它把“源表中有什么观测”和“该指标映射到什么 Metric”绑在同一次运行中，导致 Phase 2.5 也只能依赖完整 `MappingPlan`。

### 3.4 Phase 2.5 生产与评测

当前存在以下不合理方向：

```text
Production Retrieval / Provider
→ phase25.py 中的 Gold 校验、ReviewError 等 Evaluation 能力
```

冻结后的依赖方向必须是：

```text
Evaluation → Production
Production ─X→ Evaluation
```

---

## 4. 重构目标

目标主链为：

```text
CuratedDataset
→ Observation Structuring
→ MetricSubject + ObservationDraft
→ Metric Resolution
   ├─ Deterministic Resolution
   └─ Semantic Resolution（可选 fallback）
→ ResolvedObservation
```

重构完成后应满足：

- 源观测在没有 `metric_id` 时仍可独立存在；
- Metric Resolution 只解析 Metric，不决定一行是否客观构成观测；
- 两种 Metric Resolution 模式通过正式入口可选；
- Phase 2 / Phase 2.5 已验证判断、证据、安全边界保持不变；
- 生产代码不依赖 Gold、Recall 或 Pilot；
- 新主链成为唯一正式路径，旧混合 DTO / API 在 R3 删除；
- 不提前实现 Organization Resolution、Ontology Instantiation 或 ActualObservation。

---

## 5. 冻结的目标架构

```text
Data Preparation
  CuratedDataset
        │
        ▼
Observation Structuring
  ObservationStructuringRequest
  TableMappingPlan
  MetricSubject
  ObservationDraft
        │
        ├──────────────────────┐
        ▼                      ▼
Deterministic Resolution   观测事实保持独立
  MetricDecision              │
        │                      │
        ▼                      │
Semantic Fallback（可选）     │
  MetricCandidateSet          │
  SemanticResolution          │
        │                      │
        ▼                      │
EffectiveMetricResolution ────┘
        │
        ▼
ResolvedObservation

未来且不属于本次：
Organization Resolution / Ontology Instantiation
→ ActualObservation
```

`ResolvedObservation` 是 Mapping 层终点，不是本体实例，也不声明是否可实例化。

---

## 6. 推荐代码模块结构

最终结构保持扁平，只增加职责确实独立的模块：

```text
src/data_mapper/
├─ __init__.py
├─ contracts.py
├─ errors.py
├─ parsers.py
├─ table_structure.py
├─ pipeline.py
├─ ontology_catalog.py
├─ observation_contracts.py
├─ observation_structuring.py
├─ metric_resolution_contracts.py
├─ deterministic_resolution.py
├─ candidate_retrieval.py
├─ semantic_resolution.py
├─ deepseek_judge.py
├─ metric_resolution.py
├─ workflow.py
└─ evaluation/
   ├─ __init__.py
   ├─ gold_contracts.py
   ├─ gold_review.py
   ├─ retrieval_evaluation.py
   └─ semantic_pilot.py
```

不新增 `services/`、`repositories/`、`providers/`、`adapters/`、`interfaces/`、`domain/`、registry 或 event bus。

`ObservationSchema` 是 `observation_contracts.py` 内的窄只读值对象，不是新模块或 provider。轻量只读 loader 从 Definition 投影 `ActualObservation` 的正式结构及 Structuring 确实需要的 Period / Unit 等相关结构或枚举元数据，并把它放入 `OntologyCatalog.observation_schema`。`observation_structuring.py` 只接收这个投影，不接收完整 `OntologyCatalog`；投影不包含 Metric / Organization 实例，也不承担实例化完整性判断。

---

## 7. 模块职责

| 模块 | 单一职责 | 明确不负责 |
| --- | --- | --- |
| `contracts.py` | Phase 1 Raw / Curated 契约 | Mapping 与本体判断 |
| `ontology_catalog.py` | 只读加载 Definition / Knowledge，构造带 revision 的内存目录及 `observation_schema` 窄投影 | Mapping 编排、本体写入 |
| `observation_contracts.py` | 表结构、主体、ObservationSchema、草稿、Structuring 请求与结果契约 | Metric 决策、正式实例化 |
| `observation_structuring.py` | 按 ObservationSchema 理解表绑定、行角色和正式观测业务槽位，并投影 0～N 条观测草稿及来源证据 | Metric 查询、Organization 解析、实例化完整性判断、LLM |
| `metric_resolution_contracts.py` | Resolution 请求、模式、结果、有效决议等生产契约 | Gold / Pilot 报告 |
| `deterministic_resolution.py` | override、正式名、alias、受限规范化、四态决策 | 候选召回、LLM |
| `candidate_retrieval.py` | 为 eligible 未决 Metric 构造上下文并确定性召回 Top-K | 直接判等、Gold 评分 |
| `semantic_resolution.py` | Judge 调用、语义决议、确认状态、有效决议、变更建议 | 自动确认、自动改本体 |
| `deepseek_judge.py` | DeepSeek OpenAI-compatible Judge 实现 | `.env` 加载、Pilot 编排 |
| `metric_resolution.py` | 组合确定性与可选语义 fallback；只读使用完整 Structuring 结果构造表级语义上下文 | 改变 Structuring 结果 |
| `workflow.py` | 依次编排 Structuring 与 Resolution，并按 subject 组合结果 | 文件读取、秘密加载、实例化、持久化 |
| `evaluation/*` | Gold、Recall、Pilot 和评测报告 | 真实业务生产必经流程 |

---

## 8. 现有主要类与函数迁移表

| 当前对象 | 当前职责 | 目标归属 | 动作 |
| --- | --- | --- | --- |
| `MappingRequest` | 结构配置 + Resolution 配置 | 两个显式 Request | R1 拆分，R3 删除 |
| `TableMappingPlan` | 表结构状态和 binding | `observation_contracts.py` | 保留语义，移动 |
| `MetricSubject` | 来源行指标主体 | `observation_contracts.py` | 保留字段和身份语义，移动 |
| `MetricDecision` | 确定性 Metric 决策 | `metric_resolution_contracts.py` | 保留语义，移动 |
| `ObservationCandidate` | 观测 + Resolution + 实例化检查 | `ObservationDraft` + Resolution | 拆分，R3 删除 |
| `MappingPlan` | 全阶段复合产物 | Structuring / Resolution / Workflow 各自结果 | 拆分，R3 删除 |
| `MappingReport` | 结构、Metric、候选和实例约束混合报告 | 分层报告 | 拆分，R3 删除 |
| `MappingResult` | 旧复合返回值 | `DataMappingResult` | 替换，R3 删除 |
| `map_curated_dataset()` | 一体化主编排 | `workflow.py::map_curated_observations()` | 迁移调用方后删除 |
| `_build_table_plan()` | 结构 plan，夹带本体校验 | `observation_structuring.py` | 拆出 Metric / Organization 校验后复用 |
| `_extract_metric_subjects()` | 主体提取 | `observation_structuring.py` | 复用规则；显式 hints |
| `_build_metric_decisions()` | 确定性决策 | `deterministic_resolution.py` | 原样迁移优先 |
| `_extract_comparison_name()` | 展示文本主体提取 | `observation_structuring.py` | 原样迁移优先 |
| `_classify_row_role()` | 行角色 | `observation_structuring.py` | 原样迁移优先；去除 Resolution 反向输入 |
| `_match_metric()` | 确定性 matcher | `deterministic_resolution.py` | 原样迁移优先 |
| `_project_candidates()` | 观测投影 + 约束检查 | `observation_structuring.py` | 改为投影 Draft；删除 Metric / 实例化字段 |
| `build_semantic_context()` | 生产语义上下文 | `candidate_retrieval.py` | 保留行为和证据 |
| `retrieve_*()` | 生产候选召回 | `candidate_retrieval.py` | 保留算法、排名和 ID |
| `evaluate_retrieval_on_gold()` | Recall 评测 | `evaluation/retrieval_evaluation.py` | 移动 |
| `run_semantic_judgment()` | 生产 Judge 边界 | `semantic_resolution.py` | 保留失败边界 |
| `review_resolution()` | 人工确认派生结果 | `semantic_resolution.py` | 保留显式确认边界 |
| `build_effective_mapping_view()` | 有效 Metric 映射 | `semantic_resolution.py` | 适配新 ResolutionResult |
| `build_ontology_change_proposal()` | 只读提案 | `semantic_resolution.py` | 保留，不自动执行 |
| `DeepSeekSemanticJudge` | 生产 provider | `deepseek_judge.py` | 保留协议与实现 |
| `run_semantic_pilot_on_gold()` | Gold Pilot | `evaluation/semantic_pilot.py` | 移动 |
| `a.py::build_mapping_request()` | 演示配置组装 | 两个显式 request builder | 迁移后删除旧 builder |
| `a.py::build_phase25_console_report()` | 手工 Phase 2.5 编排 | 正式 workflow 结果展示 | 只保留展示 |

Phase 1 文件保持不动；本次不得借移动代码改变其行为。

---

## 9. 新增、保留与退役契约

### 9.1 新增

- `ObservationSchema`：由只读 loader 从 Definition 的 `ActualObservation`、`Period`、`Unit` 等提取，并由 `OntologyCatalog.observation_schema` 提供的 Structuring 窄只读结构投影；
- `ObservationStructuringRequest`；
- `ObservationDraft`；
- `ObservationStructuringResult` 与分层报告；
- `MetricResolutionMode`；
- `MetricResolutionRequest`；
- `EffectiveMetricResolution`；
- `MetricResolutionResult`；
- `ResolvedObservation`；
- `DataMappingResult`。

### 9.2 保留语义

- Phase 1 Raw / Curated contracts；
- `OntologyCatalog` 及 `ontology_revision`；
- `TableMappingPlan`、`MetricSubject`、`MetricDecision`；
- `MetricCandidateSet`、`SemanticResolution`；
- `OntologyChangeProposal`；
- Evidence、结构三态和 Metric 四态；
- Candidate Retrieval 与 Judge 的版本、fingerprint、证据和失败边界。

允许为纠正依赖方向而移动这些契约，但不能顺手改变其业务语义。

### 9.3 R3 退役

- `MappingRequest`；
- `ObservationCandidate`；
- `MappingPlan`；
- `MappingReport`；
- `MappingResult`；
- `map_curated_dataset()`；
- 只为旧 Phase 编排服务的兼容 adapter / facade；
- 生产代码中的 `phase25_*` 阶段命名（在行为迁移且零消费者后）；
- 旧 `EffectiveMappingView` 在新结果完整覆盖后退役或改为 Evaluation 投影。

退役发生在所有仓库内消费者完成迁移且 parity 通过之后，不得提前删除。

---

## 10. Observation Structuring 设计

### 10.1 输入

`ObservationStructuringRequest` 显式包含：

```text
curated_id
metric_name_field
value_bindings
organization
organization_id（可选已知引用，仅透传）
report_period_type
report_period_key
unit
ignored_fields
metric_row_hints
structuring_rule_version
```

`metric_row_hints` 只表达“这些来源行按 Metric 行处理”，不携带目标 Metric ID，也不表达 ontology gap。

硬约束：

```text
MetricResolutionRequest
─X→ ObservationStructuringRequest.metric_row_hints
```

正式 `workflow.py` 不得用 `metric_overrides.keys()`、`ontology_gap_confirmations` 或 Semantic 结果自动补充 hints。需要同时表达“该行是 Metric”和“该行强制映射”时，调用方必须分别填写两个 Request。

R1 可提供一次性迁移函数，把旧 `MappingRequest` 显式转换为两个新 Request；其转换结果必须可检查并有专门 parity 测试。该函数仅用于迁移旧 fixture / 内部调用方，R3 删除，不进入新正式 workflow。

### 10.2 `ObservationSchema`

`ObservationSchema` 不是只保存 period / unit 值域，而是由轻量只读 loader 从 Definition 中提取的 **ActualObservation 结构窄投影**。loader 在构造 `OntologyCatalog` 时同时构造该值对象；正式 workflow 从 `catalog.observation_schema` 取得它，再单独传给 Observation Structuring。Core 和 Structuring 都不直接读取 Definition JSON。

它的作用是告诉 Observation Structuring：一条正式观测最终有哪些业务槽位、这些槽位是什么类型，以及当前 Structuring 确实需要的相关 struct / enum 基本约束。

当前正式 `ActualObservation` 的业务结构包括：

```text
id
organization_id -> Organization.id
metric_id       -> Metric.id
business_scope
source
period          -> Period
actual_value
unit            -> Unit
status          -> Status
```

`ObservationSchema` 可以投影 Structuring 真正需要的元数据，例如：

```text
property name / type
reference / struct / enum target
final required flag
Period 的 period_type / period_basis 等结构和值域
Unit 等当前 Structuring 已使用的相关枚举定义
```

这里的 `required` 只表示 **未来正式 ActualObservation 实例化时必须满足**，不能被当成 `ObservationDraft` 的生成门禁。例如正式 `metric_id`、`organization_id` 或 `status` 当前尚未可靠取得时，Draft 仍可生成；Structuring 不得为了满足 required 而猜测、伪造或提前解析。R1 不增加 status 请求字段、binding、规范化或推断逻辑；Status 即使出现在正式结构元数据中，也只是非执行性的未来实例化参考。

该投影不得包含：

```text
Metric 实例目录或候选
Organization 实例目录
Metric / Organization Resolution 结果
InstantiationReadiness
正式 ActualObservation.id 生成规则
```

`observation_structuring.py` 只接收这一窄投影，不接收或查询完整 `OntologyCatalog`。`ObservationStructuringResult` 记录该投影的稳定 `observation_schema_fingerprint`，用于解释和重放；它不是完整 `ontology_revision`，也不把 Metric / Organization revision 耦合进 Draft。只要 schema 投影内容变化，fingerprint 就必须变化。

这不是新的本体 provider，也不改变 Definition / Knowledge 的只读 loader 边界。

### 10.3 输出

Structuring 输出：

```text
TableMappingPlan
MetricSubject[]
ObservationDraft[]
ObservationStructuringReport
```

`BLOCKED` 不生成 Draft。`READY` 和 `NEEDS_BINDING` 都按值字段逐项判断：只要某个非空数值及其 organization、business scope、period、period basis、unit 等当前必需结构绑定已经完整，该值就可以生成 `ObservationDraft`；未完整绑定的值继续进入 `unprojected_values / unresolved_bindings`，不得阻塞同表其他完整值。

因此 `NEEDS_BINDING` 表示当前表仍有需要补充的结构绑定，不等于已经形成的 Draft 无效。指标值为空时仍可生成 `MetricSubject`，但不生成 Draft；一行可按有效值、`business_scope` 和 `period_basis` 投影 0～N 条 Draft。该规则保持当前 Phase 2 的部分投影行为。

### 10.4 Organization 边界

- `organization_value` 属于 Structuring；
- `organization_id` 仅是调用方已经知道并显式提供的可选引用；
- Structuring 不从 `organization_value` 推断 `organization_id`；
- Structuring 不查询、猜测或验证 Organization；
- 未提供已知引用时保持 `null`，不得伪造；
- 组织引用合法性和解析留给未来 Organization Resolution / Ontology Instantiation。

---

## 11. `ObservationDraft` 冻结语义

`ObservationDraft` 表示已经从 Curated 中结构化出的 **工作态观测**。它以 Definition 中 `ActualObservation` 的正式业务结构为骨架，但不是 `ActualObservation` 的缩水版，也不要求与正式实例 1:1 同构。

Draft 应尽量承载当前阶段已经可靠得到的正式观测业务信息，例如组织原始值、MetricSubject、期间、数值、单位、业务范围和来源；同时允许保留 Data Mapper 为追踪、解释、重跑和排错所需的额外信息，例如文件 / Sheet / 行列定位、Raw / Curated 身份、binding 与 evidence、规则版本等。

正式 `ActualObservation` 中某字段为 `required`，不代表 Draft 阶段必须已经得到它。特别是 `metric_id`、尚未解析的 `organization_id` 和未来实例状态，不得为了“凑齐正式结构”而伪造。R1 不新增 status 绑定、规范化或推断，也不在 Draft 起始契约中增加 status 字段。

Draft 也不要求与正式 `ActualObservation` 采用完全相同的字段形状。例如正式实例使用 `period: Period`，Draft 可以继续使用更适合数据映射和追踪的 `period_type / period_key / period_basis`；正式实例使用 `unit: Unit`，Draft 可以同时保留 `unit_raw / unit_normalized`。这些差异在未来 Ontology Instantiation 时再按 Definition 组装和投影。

建议起始字段如下。该列表不是封闭 schema；R1 应以现有真实运行结果和来源追踪需求为依据补充仍有意义的 Structuring / provenance 字段，但新增字段不得重新混入 Metric Resolution 或 Ontology Instantiation 职责。

```text
observation_draft_id
metric_subject_id
actual_value
business_scope
organization_value
organization_id | null
period_type
period_key
period_basis
unit_raw
unit_normalized | null
source
source_file
sheet_name
source_row
metric_source_column
value_source_column
value_field
curated_id
raw_dataset_id
raw_version_id
structuring_rule_version
role_bindings
binding_evidence
```

不得包含：

```text
current_metric_id
MetricDecision
MetricDecision.status
SemanticResolution
LLM 输出
ontology_revision
definition_constraints_satisfied
instantiation_missing_fields
```

`binding_evidence` 保留“这条观测如何从源数据得到”的证据；Metric match evidence 只进入 Resolution。`observation_schema_fingerprint` 记录在 `ObservationStructuringResult`，不重复写入每条 Draft。Draft 中超出正式 `ActualObservation` 的来源 / 追踪字段继续保留在 Data Mapper 结果中，未来实例化时不会因为没有进入正式本体实例而被视为无效或必须丢弃。

### 11.1 与未来 `ActualObservation` 的关系

未来 Ontology Instantiation 才按照 Definition 对已经解析好的信息做正式投影和校验。概念上：

```text
ObservationDraft
+ EffectiveMetricResolution
+ Organization Resolution（未来）
+ 其他未来必须补齐的正式字段
        ↓
Ontology Instantiation
        ↓
ActualObservation
```

正式 `ActualObservation` 只写入 Definition 当前定义的属性，例如 `id / organization_id / metric_id / business_scope / source / period / actual_value / unit / status`。Draft 中额外的文件、Sheet、行列、Raw / Curated identity、binding evidence 等 provenance / trace 信息继续保留在 Data Mapper 的上游结果或后续血缘记录中，不要求全部塞入正式本体实例。

本次 Clean Refactor 只保证这条边界可自然衔接，不实现 Ontology Instantiation，也不设计 `ActualObservation.id`。

---

## 12. Metric Resolution 设计

### 12.1 输入

`MetricResolutionRequest` 显式包含：

```text
mode
metric_overrides
ontology_gap_confirmations
deterministic_rule_version
retrieval options（仅 fallback 模式）
semantic execution options（仅 fallback 模式）
```

秘密、`.env` 路径和具体 provider 初始化不进入 Core Request。Judge 作为运行时依赖传入。

正式 `resolve_metrics()` 接收完整且只读的 `ObservationStructuringResult`，而不是只接收孤立的 `MetricSubject[]`。其中至少提供：

```text
TableMappingPlan
按源行稳定排序的全部 MetricSubject（包括 GROUP / NOTE / UNKNOWN）
source context 与 Raw / Curated provenance
value field 的 business_scope / period_type / period_basis / unit / binding 状态
structuring_run_id / rule version
```

这是 `Observation Structuring → Metric Resolution` 的单向数据依赖。Resolution 只能读取这些结果，不得修改它们，也不得借 Resolution 配置反向改变行角色、Draft 或表结构状态。

### 12.2 Deterministic Resolution

复用冻结顺序与规则：

```text
override
→ 正式名称精确匹配
→ alias 精确匹配
→ 受限规范化匹配
→ value semantic conflict 检查
→ MATCHED / AMBIGUOUS / UNMATCHED / ONTOLOGY_GAP
```

不新增 fuzzy、embedding、包含匹配或 LLM；不改变 `business_scope`、`period_basis`、表结构三态和 Metric 四态。

`MetricDecision` 继续以 `ontology_revision + current_metric_id` 表达当前 revision 内引用，不假设 Metric ID 永久稳定。

### 12.3 Semantic Resolution

复用现有：

- eligibility；
- Candidate Retrieval；
- Semantic Context；
- 同表独立项、周边行与 NOTE 证据；
- DeepSeek Judge；
- execution / semantic / review 三类状态；
- revision mismatch 的 `SKIPPED` 边界；
- `SemanticResolution`；
- `OntologyChangeProposal`。

Semantic Context 必须在同表全部 Deterministic `MetricDecision` 已形成后构造。除上面的 Structuring 结果外，它还组合本表确定性决策，以保留：

```text
nearest group
previous / following subjects
NOTE
same-table metric subjects
same-table candidate conflicts 及其确定性 Metric 结果
table value scope / period / unit context
source 与规则版本证据
```

这保证现有“同表独立项目”强负证据不会在拆模块时丢失。语义上下文继续不读取实际数值，不改变 Candidate Retrieval 的职责与排序算法。

Candidate Retrieval 负责召回，不直接等于可靠匹配。Judge 判断业务等价，不以名称相似或业务相关代替等价。Top-K 未召回不能证明本体不存在等价 Metric。

只有 `CONFIRMED` 的语义映射可以成为有效映射；`PROPOSED` 不改变确定性结果。Proposal 只生成草案，不自动修改 Definition / Knowledge。

`ADD_ALIAS` 只适用于脱离当前报表上下文仍稳定表达同一 Metric 的名称。依赖 GROUP、前后指标、位置或层级才能确定的表达不得成为全局 alias。

---

## 13. Semantic 模式开关

`MetricResolutionMode` 冻结为：

```text
DETERMINISTIC_ONLY
DETERMINISTIC_WITH_SEMANTIC_FALLBACK
```

### 13.1 `DETERMINISTIC_ONLY`

- 执行确定性匹配；
- Candidate Retrieval 调用数必须为 0；
- Judge / LLM 调用数必须为 0；
- 未决项保持 `UNMATCHED / AMBIGUOUS`；
- 不要求存在 Judge 配置。

### 13.2 `DETERMINISTIC_WITH_SEMANTIC_FALLBACK`

只有同时满足以下条件才进入 fallback：

```text
row_role == METRIC
AND deterministic status in {UNMATCHED, AMBIGUOUS}
```

以下项目不得进入：

```text
MATCHED
ONTOLOGY_GAP
GROUP
NOTE
UNKNOWN
```

启用该模式但未提供可用 Judge 时，返回明确配置错误，不能静默降级为 deterministic-only。单项 provider timeout / 格式错误按现有失败契约记录，不污染其他决议。

---

## 14. Effective Resolution、`ResolvedObservation` 与审核回放

`EffectiveMetricResolution` 表达某个 `MetricSubject` 当前真正生效的 Metric 结果，至少包含：

```text
source_metric_decision_id
ontology_revision
effective_status
current_metric_id | null
source
source_resolution_id | null
evidence
```

优先级保持：

```text
已确认 Semantic Resolution
> Deterministic MetricDecision
> unresolved
```

`ResolvedObservation` 冻结为纯组合：

```python
@dataclass(frozen=True)
class ResolvedObservation:
    observation: ObservationDraft
    metric_resolution: EffectiveMetricResolution
```

允许 `current_metric_id = null` 和 unresolved 状态。禁止增加：

```text
InstantiationReadiness
definition_constraints_satisfied
instantiation_missing_fields
```

这些检查属于未来 Ontology Instantiation。`ResolvedObservation` 既不是 `ActualObservation`，也不是“已准备好写入本体”的声明。

### 14.1 首次运行结果

`DataMappingResult` 至少明确组合：

```text
structuring_result
metric_resolution_result
effective_metric_resolutions
resolved_observations
```

`MetricResolutionResult` 至少保留 Deterministic decisions、CandidateSets、SemanticResolutions、OntologyChangeProposals 和分层报告。首次运行中，确定性 `MATCHED` 可以直接进入有效结果；LLM 新产生的 `PROPOSED` 继续保持 unresolved，不会因出现在同一次调用中而自动生效。

### 14.2 人工确认后的纯函数式回放

人工审核在正式 workflow 之外显式产生 `CONFIRMED / REJECTED` 的 `SemanticResolution`。确认后通过生产层纯函数重新派生：

```python
apply_reviewed_resolutions(
    mapping_result,
    reviewed_resolutions,
    catalog,
) -> DataMappingResult
```

该函数必须：

- 校验 `source_metric_decision_id`、`candidate_set_id`、`ontology_revision` 和单一决议约束；
- 只接受已成功执行且已经显式审核的 Resolution；
- `CONFIRMED + MAP_EXISTING` 才能形成新的有效 Metric 映射；
- `REJECTED`、`PROPOSED`、`FAILED`、`SKIPPED` 不得成为有效映射；
- 重新派生 `EffectiveMetricResolution` 和按 `metric_subject_id` 组合的 `ResolvedObservation`；
- 不重跑 Observation Structuring、Deterministic Resolution、Candidate Retrieval 或 LLM；
- 不修改输入对象、Gold、Curated、Definition / Knowledge，也不执行 Proposal 或任何持久化写入。

这是一条可重放的派生链，不是审核 UI、repository 或状态存储。如何保存和取得人工审核结果不属于本次重构。

---

## 15. ID 保留与迁移策略

### 15.1 保持稳定

只要身份语义与输入未变化，应保持：

- Raw / Curated identity；
- `MetricSubject.subject_id`；
- `MetricDecision.decision_id`；
- `ontology_revision`；
- `MetricCandidateSet.candidate_set_id`；
- Candidate 排序和 retrieval fingerprint。

`SemanticResolution.resolution_id` 只要求唯一、可追踪，不要求同一 CandidateSet 多次真实 Judge 运行产生相同 ID。追踪依赖 decision、candidate set、revision、judge/model/rules/prompt version 和正反证据。

### 15.2 新身份

`observation_draft_id` 表达“源数据经特定 Structuring 规则形成的一条观测草稿”。建议输入：

```text
curated/raw identity
metric_subject_id
source row / value column
period_type / period_key / period_basis
business_scope
organization_value
structuring_rule_version
```

不得依赖：

```text
metric_id
MetricDecision.status
SemanticResolution
LLM 输出
actual_value
完整 ontology_revision
```

### 15.3 退役身份

- `candidate_id` 随 `ObservationCandidate` 退役；
- `mapping_run_id` 拆为清晰的 `structuring_run_id` 与 `resolution_run_id`，不强行保留旧语义；
- Gold / fixtures 中引用旧技术身份的机器元数据允许一次性迁移；
- 人工确认的事实、预期状态、目标 Metric 和来源证据不得因技术 ID 迁移被重写。

本次不设计 `ActualObservation.id`。

---

## 16. Production / Evaluation 边界

### 16.1 Production

```text
Observation Structuring
Deterministic Resolution
Candidate Retrieval
Semantic Context
Semantic Judge
SemanticResolution
EffectiveMetricResolution
ResolvedObservation
OntologyChangeProposal（只读草案）
```

### 16.2 Evaluation

```text
Gold contracts / validation
P0 review material
Recall@K
hard negative evaluation
stable replay evaluation
Semantic Pilot
evaluation reports
```

硬约束：

- Production 包不得 import `evaluation`；
- Gold 不是生产调用的参数或前置条件；
- Pilot 不放在 `deepseek_judge.py`；
- 公共 JSON 序列化基类移到生产可依赖的中立位置，不能由生产契约从 Gold 契约导入；
- Evaluation 只调用公开或明确稳定的 Production 接口，不复制生产算法。

---

## 17. 最终主调用链

正式入口：

```python
map_curated_observations(
    curated,
    structuring_request,
    resolution_request,
    catalog,
    judge=None,
) -> DataMappingResult
```

内部编排：

```text
1. 从 catalog.observation_schema 取得 ActualObservation 的窄结构投影
2. structuring_result = structure_observations(curated, structuring_request, observation_schema)
3. resolution_result = resolve_metrics(structuring_result, resolution_request, catalog, judge)
4. 按 metric_subject_id 组合 ObservationDraft 与 EffectiveMetricResolution
5. 返回 DataMappingResult
```

首次运行不会自动确认 SemanticResolution。人工审核完成后，调用 `apply_reviewed_resolutions(mapping_result, reviewed_resolutions, catalog)` 纯函数式重建第 4～5 步；不得重跑第 2～3 步中的 Structuring、Retrieval 或 LLM。

该入口不负责：

- 读取 Excel / CSV；
- 加载 `.env` 或创建秘密；
- 修改 Curated；
- 写 Definition / Knowledge；
- 自动确认 SemanticResolution；
- 自动执行 OntologyChangeProposal；
- Organization Resolution；
- Ontology Instantiation / ActualObservation；
- 数据库或 Neo4j 写入。

`a.py` 最终只负责读取演示配置、创建可选 Judge、调用该入口和展示结果，不再手工拼装 Phase 2 / Phase 2.5。

---

## 18. 内部消费者迁移清单

R0 必须重新扫描，当前已知消费者如下：

| 消费者 | 当前依赖 | 迁移动作 |
| --- | --- | --- |
| `a.py` | `MappingRequest`、`map_curated_dataset()`、旧 Plan/Candidate、手工 Phase 2.5 | 改用两个 Request 和正式 workflow |
| `README.md` | 旧 API 示例与 candidate_id 说明 | 更新为新入口和 Draft ID 边界 |
| `src/data_mapper/__init__.py` | 导出旧契约/API | R2 导出新 API，R3 删除旧导出 |
| `tests/test_phase2_mapping.py` | 旧主入口和旧 DTO | 迁移为 Structuring、Deterministic、workflow 分层测试 |
| `tests/test_phase25_p0.py` | `MappingPlan` | 迁移 Gold 输入机器元数据 |
| `tests/test_phase25_p1.py` | Phase 2.5 旧路径 | 指向 Production Retrieval + Evaluation wrapper |
| `tests/test_phase25_p2.py` | Judge 契约 | 指向新生产契约 |
| `tests/test_phase25_p3.py` | 旧 MappingPlan 与 effective view | 改用 ResolutionResult / workflow |
| `tests/test_phase25_deepseek.py` | provider + Pilot 混合模块 | 拆为 provider 与 evaluation 测试 |
| `tests/test_a_phase25_entry.py` | a.py 手工编排 | 验证 a.py 调用正式 workflow |
| `docs/*` | Phase 命名和旧契约说明 | R3 更新架构现状；保留历史冻结文档并标明历史 |

只有全仓库搜索确认旧符号零生产/测试消费者后，才允许删除。

---

## 19. R0 — Baseline Freeze

### 工作

1. 记录实际起点 commit、分支和工作树状态；
2. 运行全量测试并保存结果；
3. 对 Phase 1 代表 fixture 保存 Curated 业务快照；
4. 对 Phase 2 保存结构三态、行角色、MetricSubject、Metric 四态、候选投影和稳定 ID 快照；
5. 对 Phase 2.5 保存 Top-K 排名、Semantic Context、同表证据、Judge 代表结果、Effective Mapping 和 Proposal 快照；
6. 保存 Definition / Knowledge 与 nano 参考目录的只读 diff / hash 基线；
7. 扫描并固化全部旧 API 消费者；
8. 明确不把实时 LLM 的自由文本逐字相等作为 parity 条件，改为契约、安全边界与已固定响应的可重复测试。

### 停止条件

- 全量测试通过；
- 快照能自动比较，而不是依赖人工浏览控制台；
- 代表数据覆盖 Phase 1、结构三态、四种 period basis、单/多 business scope、Metric 四态和 Phase 2.5 困难样例；
- 只读资产基线明确；
- 旧消费者清单完整。

任一条件未满足，不进入 R1。

---

## 20. R1 — Responsibility Split

### 工作

1. 建立 `observation_contracts.py` 与 `observation_structuring.py`；
2. 引入两个显式 Request，并由只读 `OntologyCatalog.observation_schema` 提供基于 `ActualObservation` 结构的窄投影；
3. 将表计划、行角色、主体提取和 Draft 投影从旧 `mapping.py` 拆出；
4. 将确定性匹配迁入 `deterministic_resolution.py`；
5. 保持 `MetricSubject` / `MetricDecision` 身份和判断；
6. 提供仅供迁移期使用的旧 Request 转换函数；
7. 新增分层单元和 parity 测试，旧主入口暂时可调用新实现以维持迁移窗口。

### 必测边界

- Draft 在 `metric_id = null` 时可生成；
- Draft 以正式 Observation 结构为业务骨架，但不包含已解析 `metric_id`、MetricDecision / SemanticResolution 或实例化 readiness；
- `NEEDS_BINDING` 下已经完整绑定的值仍可产生 Draft，未决值单独报告；`BLOCKED` 不产生 Draft；
- `metric_row_hints` 是 Structuring 的显式输入；
- 直接构造新 workflow 时，Resolution request 不能改变行角色和 Draft 数量；
- Organization ID 只透传，不查询或验证 catalog；
- `ObservationSchema` 由只读 loader 构造并通过 Catalog 提供，能反映 Definition 中 `ActualObservation` 的正式业务槽位、类型/引用/struct/enum 关系和 final required 元数据，但 required 不阻塞 Draft；
- R1 不新增 status binding、规范化或推断；`ObservationStructuringResult` 记录稳定的 `observation_schema_fingerprint`；
- Draft 可以保留正式实例之外的来源 / 追踪信息，且这些信息不会被正式字段投影规则误删；
- Structuring 不 import 或查询完整 `OntologyCatalog`，不访问 Metric / Organization 实例目录，只消费窄 `ObservationSchema`；
- 确定性匹配结果、证据和顺序与 R0 一致。

### 停止条件

- Observation Structuring 可独立调用；
- Deterministic Resolution 可独立调用；
- 新旧业务输出 parity 通过，允许差异仅限已登记的技术 ID / DTO 形状；
- Phase 1 全量回归通过；
- V1.0.2 冻结边界均有失败测试保护；
- 没有修改 Definition / Knowledge 或 nano。

任一条件未满足，不进入 R2。

---

## 21. R2 — Pipeline Recomposition

### 工作

1. 拆分 Phase 2.5 的 Production 与 Evaluation；
2. 建立 `metric_resolution.py`，组合确定性和可选 Semantic fallback，并只读消费完整 `ObservationStructuringResult`；
3. 建立 `workflow.py::map_curated_observations()`；
4. 实现 `EffectiveMetricResolution`、纯组合 `ResolvedObservation`、`DataMappingResult` 与人工确认后的纯函数式回放；
5. 迁移 `a.py`、README、tests、Gold / Pilot / evaluation；
6. 对新旧业务语义做端到端 parity；
7. 验证 provider 失败、revision mismatch、PROPOSED / CONFIRMED 和 proposal 安全边界。

### 停止条件

- `DETERMINISTIC_ONLY` 确认 Retrieval=0、LLM=0；
- fallback 只处理 `METRIC + UNMATCHED/AMBIGUOUS`；
- 确定性 `MATCHED` 不进入 LLM；
- Candidate 排名、context、同表证据无无理由漂移；
- NOTE、同表指标及其确定性决策、行顺序、scope、period、unit 等现有 Semantic Context 全部保留；
- `PROPOSED` 不成为有效映射；
- 审核回放只有 `CONFIRMED + MAP_EXISTING` 能改变有效映射，且不重跑 Structuring、Retrieval 或 LLM；
- `ResolvedObservation` 不含实例化 readiness；
- 生产代码零 Evaluation import；
- `a.py` 只调用正式 workflow；
- 全量测试与只读检查通过。

任一条件未满足，不进入 R3。

---

## 22. R3 — Clean Break & Freeze

### 工作

1. 用 `rg` 确认旧 API / DTO 零消费者；
2. 删除 `MappingRequest`、`ObservationCandidate`、旧 Plan/Report/Result 和 `map_curated_dataset()`；
3. 删除临时 Request adapter、旧 re-export、死代码和只为旧链路存在的测试；
4. 完成 `phase25_*` 生产命名迁移；
5. 更新 README、ARCHITECTURE、CURRENT_PHASE 和公开调用示例；
6. 运行完整 parity、测试、compile、lint/diff 和只读检查；
7. 形成最终重构验收报告。

### 停止条件

- 新主链是唯一正式路径；
- 旧混合抽象与临时兼容层均无残留；
- Production / Evaluation 依赖方向正确；
- 所有内部消费者已迁移；
- 全量测试通过；
- R0 业务语义 parity 通过；
- 所有已登记技术 ID 迁移均有说明和验证；
- Definition / Knowledge、nano 参考目录保持未修改；
- 未引入本计划非目标。

全部满足后，Clean Refactor 才能冻结完成。

---

## 23. 回归与验收方案

### 23.1 Phase 1

- parsers、表头、行报表、quality、failures、e2e 全量回归；
- 代表 Excel / CSV 的 Raw / Curated 内容与来源信息不漂移。

### 23.2 Observation Structuring

- `READY / NEEDS_BINDING / BLOCKED`；
- `NEEDS_BINDING` 下完整值继续生成 Draft、未完整值进入报告；`BLOCKED` 不生成 Draft；
- `METRIC / GROUP / NOTE / UNKNOWN`；
- raw label 与 comparison name/evidence；
- `PERIOD_VALUE / YEAR_TO_DATE / PERIOD_BEGIN / PERIOD_END`；
- 单业务范围和多 business scope；
- 空值产生 Subject 但不产生 Draft；
- 同名不按字符串跨上下文合并；
- Draft 稳定重跑；
- 显式 hints 与 Resolution 完全解耦；
- Organization ID 仅透传；
- `OntologyCatalog.observation_schema` 与 Definition 的 ActualObservation 窄结构投影一致；
- `observation_schema_fingerprint` 稳定且随投影内容变化；
- 正式 required 元数据不作为 Draft 生成门禁；
- R1 不新增 status binding、规范化或推断；
- Draft 额外 provenance / trace 字段可稳定保留。

### 23.3 Deterministic Resolution

- override、正式名、alias、受限规范化；
- Metric 四态；
- 普通 unmatched 不自动成为 gap candidate；
- revision 隔离；
- 相同输入与规则稳定重跑。

### 23.4 Semantic Resolution

- Recall@3 / Recall@5 与 hard negatives 不退化；
- CandidateSet 确定性；
- NOTE、同表 sibling / conflict、行顺序、scope、period、unit 等完整 Semantic Context 保留；
- Judge 结构化输出、失败和 revision mismatch；
- `NO_EQUIVALENT / AMBIGUOUS` 不被强制映射；
- `PROPOSED / CONFIRMED / REJECTED` 边界；
- 确认回放不重跑 Structuring、Retrieval 或 LLM，只有 `CONFIRMED + MAP_EXISTING` 改变有效映射；
- `ADD_ALIAS` 上下文护栏；
- Proposal 零自动执行。

### 23.5 Workflow

- 两种模式端到端；
- Draft 与 Resolution 按稳定 `metric_subject_id` 组合；
- unresolved Metric 仍保留 ObservationDraft；
- 首次运行与审核回放均可派生完整 `DataMappingResult`；
- `DataMappingResult` 可 JSON 序列化；
- 不读 `.env`、不写本体、不写数据库。

### 23.6 Parity 判定

强制一致：

```text
Curated 业务内容
结构状态和绑定
MetricSubject 语义
确定性 Metric 状态、目标和证据
Candidate Retrieval 排名和 context
Semantic 安全状态
Effective Metric 结果
OntologyChangeProposal 内容与只读边界
```

允许登记后变化：

```text
DTO 外形
模块/import 路径
mapping_run_id / candidate_id 等被纠正职责的技术 ID
实时 LLM 自由文本和唯一 resolution_id
```

任何未登记的业务差异视为回归失败。

---

## 24. 风险与回滚

| 风险 | 控制措施 | 回滚点 |
| --- | --- | --- |
| 代码移动时悄然改变匹配逻辑 | R0 快照 + 分层 parity | 回到上一 R 阶段提交 |
| hints 隐式耦合被误带入新 workflow | 显式双 Request + 负向测试 | 保留 R1 新契约，撤回 workflow 接线 |
| Structuring 再次依赖完整 catalog | 只允许消费 `OntologyCatalog.observation_schema` 窄投影，并用 import / fake catalog 测试保护 | 撤回窄投影以外依赖 |
| `NEEDS_BINDING` 时误删已完整值 | 固定部分绑定 fixture，逐值比较 Draft 与 unresolved report | 恢复当前部分投影规则 |
| Phase 2.5 context 丢字段 | 固定 NOTE、同表决策、顺序、scope、period、unit 的 CandidateSet/context 快照 | 保留旧 production 实现并重做移动 |
| 审核回放触发重算或误接纳 PROPOSED | 纯函数输入校验、调用计数与不可变对象测试 | 撤回回放接线，保留首次运行结果 |
| 实时 LLM 非确定性导致伪回归 | 固定 Judge 响应用于 parity，真实 Pilot 单列 | 不回滚业务，仅标记外部波动 |
| Gold 技术 ID 迁移污染事实标签 | 分离 machine metadata 与 human truth | 恢复 Gold 基线后重新生成机器部分 |
| Clean Break 过早删除 | R3 前零消费者扫描 | 在同一 R 阶段恢复删除提交 |

每个 R 阶段应形成独立、可回滚提交。不得用跳过测试、放宽断言或永久兼容层掩盖失败。

---

## 25. 本次明确不做

- 不重写或重新设计 Phase 1；
- 不修改确定性 Metric 匹配算法；
- 不修改 Candidate Retrieval 算法；
- 不扩展 Semantic Judge 业务逻辑；
- 不新增 embedding、向量数据库、额外 fuzzy 路线或 CALCULATION；
- 不实现 Organization Resolution；
- 不验证 `organization_id` 的本体合法性；
- 不实现 Instantiation Readiness；
- 不实现 Ontology Instantiation、ActualObservation 或正式 observation ID；
- 不写 Neo4j 或其他持久化；
- 不修改 Definition / Knowledge；
- 不自动确认语义结果或执行 OntologyChangeProposal；
- 不实现 Web API、UI、权限、审批平台；
- 不引入 repository、service、provider registry、event bus 等平台框架；
- 不永久保留旧/新双轨；
- 不为目录美观搬动与职责无关的代码。

---

## 26. 对当前方案的改进建议

### 必须修改（已纳入 V1.0.2）

1. 删除 `ResolvedObservation` 中的 `InstantiationReadiness`；
2. 将 `metric_row_hints` 冻结为 Structuring 显式输入，禁止 Resolution 反向控制；
3. 将 `organization_id` 冻结为已知引用透传，禁止 Structuring 承担 Organization Resolution / validation；
4. 纠正 Production 对 Evaluation 的反向 import；
5. 用自动 parity 快照控制“搬代码不改业务”。
6. 明确 `ObservationDraft` 以正式 `ActualObservation` 为业务骨架但允许携带更多 provenance / trace；正式 required 只在未来实例化时严格检查。
7. 保留 `NEEDS_BINDING` 下完整值的部分 Draft 投影行为；
8. 让 Metric Resolution 只读消费完整表级 Structuring 结果，保留现有 Semantic Context；
9. 由 `OntologyCatalog.observation_schema` 闭合窄结构投影来源，记录 fingerprint，R1 不新增 status 推断；
10. 增加人工确认后的纯函数式回放，重新派生有效映射和 ResolvedObservation。

### 建议优化（实施方法，不扩范围）

1. R1 优先复制测试再移动实现，尽量让每次提交只改变一个依赖方向；
2. `ObservationSchema` 只做 Definition 中 `ActualObservation` 业务结构及当前所需 struct / enum 的窄只读投影，由 Catalog 提供，不建立 provider/repository，也不携带 Metric / Organization 实例；
3. 新报告按 Structuring 与 Resolution 分层，顶层只汇总，不再形成一个全知 `MappingReport`；
4. 实时 LLM Pilot 与固定响应契约测试分开，避免把模型波动误判为架构回归。

### 未来再做

- Organization Resolution；
- Ontology Instantiation 与 readiness；
- ActualObservation 和正式业务身份；
- 本体变更审批 / 发布；
- 持久化、Neo4j、服务化和 UI。

当前方案已经是满足职责清晰、可验证迁移和 Clean Break 的最小合理结构。继续新增 DTO、抽象层或未来功能不会提高本次重构的正确性，因此不纳入。

---

## 27. 设计冻结检查表

- [x] 以最新代码和调用点为事实基础；
- [x] Phase 1 行为保持冻结；
- [x] Phase 2 / Phase 2.5 业务能力以 parity 保留；
- [x] Observation Structuring 与 Metric Resolution 边界明确；
- [x] Draft 不依赖 `metric_id`；
- [x] ObservationSchema 以 Definition 的 ActualObservation 正式结构为业务骨架；
- [x] Draft 允许保留正式实例之外的来源 / 追踪 / evidence 信息；
- [x] ActualObservation required 只表示未来实例化要求，不阻塞 Draft；
- [x] `NEEDS_BINDING` 保留完整值的部分 Draft 投影；
- [x] `OntologyCatalog.observation_schema` 来源和 fingerprint 明确，R1 不新增 status 推断；
- [x] `metric_row_hints` 无隐式反向耦合；
- [x] Organization ID 仅透传；
- [x] `ResolvedObservation` 不含实例化职责；
- [x] Metric Resolution 拥有完整只读表级语义上下文；
- [x] 人工确认可通过纯函数回放重新派生有效结果；
- [x] Production / Evaluation 依赖方向明确；
- [x] 旧抽象有完整消费者迁移和退役条件；
- [x] R0-R3 均有准入与停止条件；
- [x] 技术 ID 变化与业务语义稳定性区分明确；
- [x] Definition / Knowledge 和 nano 只读边界不变；
- [x] 非目标和扩展停止线明确。

**最终审核结论：V1.0.2 四项修订已经闭合，设计可以开工。下一步从 R0 开始；R0 停止条件全部满足前不得进入 R1，也不得在重构中混入功能升级。**
