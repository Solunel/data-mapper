# CURRENT PHASE

## 状态

Data Mapper Clean Refactor 已按冻结计划 V1.0.2 完成 R0 → R1 → R2 → R3。
Clean Refactor 冻结边界仍到 `ResolvedObservation` 为止；其后的 Ontology
Instantiation 已按独立冻结设计 V1.0.1 完成，当前主链为：

```text
真实 Excel / CSV
→ Data Preparation
→ CuratedDataset
→ Observation Structuring
→ Metric Resolution
→ EffectiveMetricResolution
→ ID Binding（Metric + Organization）
→ ResolvedObservation
→ Ontology Instantiation
→ ActualObservation / BlockedObservation / UnresolvedMetricItem
```

历史 Phase 2 / 2.5 设计文档仍作为业务语义来源和审计材料保留，但不再描述
当前模块或公共 API。当前实现与验收边界以本文、`ARCHITECTURE.md` 和冻结的
`DATA_MAPPER_CLEAN_REFACTOR_PLAN.md` 为准。

## 生产职责

### Data Preparation

读取 Excel / CSV，保留 Raw 身份和来源信息，生成经过表头、类型和质量检查的
`CuratedDataset`。Curated 是规范化数据，不是本体实例。

### Observation Structuring

`structure_observations()` 只消费 Curated、`ObservationStructuringRequest` 与只读
`ObservationSchema`，产出：

- `TableMappingPlan`；
- `MetricSubject` 与 `METRIC / GROUP / NOTE / UNKNOWN` 行角色；
- 以 Definition 中 `ActualObservation` 窄结构投影为业务骨架的
  `ObservationDraft`；
- `ObservationStructuringReport`。

`READY / NEEDS_BINDING / BLOCKED` 三态保留。`NEEDS_BINDING` 下已经完整绑定的值
继续形成 Draft，未完整值进入报告；`BLOCKED` 不形成 Draft。Definition 的
`required` 元数据不作为 Draft 门禁，不提前推断 Metric、Organization 或 status。
`organization_id` 在 Draft 中只保留调用方已知引用，不在 Structuring 中查询或验证。

### Metric Resolution

`resolve_metrics()` 只读 Structuring 结果，不得修改 Draft 或行角色。它先执行
冻结的确定性匹配规则；显式启用
`DETERMINISTIC_WITH_SEMANTIC_FALLBACK` 时，再对 eligible 未决项执行 Candidate
Retrieval 和 Semantic Judge。

Semantic Context 保留前后行、NOTE、同表 Metric 与确定性结果、scope、period、
unit 等表级证据。Candidate Retrieval 不是成功映射；语义结果初始为
`PROPOSED`，不能自动进入有效映射。

### Workflow 与人工回放

`map_curated_observations()` 是唯一正式主入口，组合 Structuring、Resolution、
`EffectiveMetricResolution` 和 `ResolvedObservation`，返回 `DataMappingResult`。

`apply_reviewed_resolutions()` 接收显式审核后的 resolution，纯函数式重新派生
有效映射与 ResolvedObservation。只有 `CONFIRMED + MAP_EXISTING` 能改变有效
Metric；回放不重跑 Structuring、Retrieval 或 LLM。

### ID Binding

Workflow 在 Structuring 与 Metric Resolution 完成后统一绑定本体引用。`metric_id`
只读取 `EffectiveMetricResolution.current_metric_id`；Organization 已知引用必须先在
当前 `OntologyCatalog` 中存在，否则再按 `organization_value == name_cn` 做唯一精确
匹配。无命中或多命中保持为空，不使用模糊匹配、LLM、自动创建或伪造 ID。

`ResolvedObservation` 直接暴露 `metric_id` 与 `organization_id`，同时保留原始
`ObservationDraft` 和完整 `EffectiveMetricResolution`。其中
`ResolvedObservation.metric_id == metric_resolution.current_metric_id` 始终成立；
Semantic `PROPOSED` 未经确认不会成为有效 `metric_id`。

### Ontology Instantiation

`instantiate_observations()` 对一个非空 `DataMappingResult` 序列执行统一批次校验，
因此可在文件 / Sheet 边界之外按业务身份去重和判冲突。正式业务身份由
`organization_id + metric_id + business_scope + period` 构成，不包含 `source`；
同身份、同 payload 视为重复事实，同身份、不同 payload 整组阻断并报告
`CONFLICTING_BUSINESS_IDENTITY`。

实例化只读使用 `OntologyCatalog`，不会修改 Definition / Knowledge，也不会把 Unit
的 `storage_semantics` 投影进现有 `ObservationSchema` fingerprint。`PERCENT` 的
`0_to_1` 约束由 Catalog 的独立只读投影校验。输出严格分为
`ActualObservation`、`BlockedObservation` 和 `UnresolvedMetricItem`，本阶段不执行
任何持久化写入。

## 只读本体与存储边界

Mapping Core 面向 `OntologyCatalog` 的只读值视图，而不是 JSON 文件或 Neo4j。
当前 loader 可以从 Definition / Knowledge JSON 构造 Catalog；未来 Definition
继续使用 JSON、Knowledge 改用 Neo4j 时，Deterministic Resolution、Candidate
Retrieval 与 Semantic Resolution 不应改变。当前不提前实现 adapter、repository
或 provider registry。

Production 不依赖 Gold、Recall 或 Pilot。评测能力位于 `data_mapper.evaluation`：

```text
Evaluation → Production
Production ─X→ Evaluation
```

## 当前非目标

- Organization 模糊匹配、语义 fallback、Proposal 或自动创建；
- Neo4j 或其他数据库写入；
- 自动修改 Definition / Knowledge；
- Web API、UI 或审批平台；
- repository / service / provider registry / event bus 等预建抽象；
- embedding、额外 fuzzy 或 CALCULATION 功能扩展。

## 验收条件

当前阶段只有在以下条件同时成立时才可视为 Frozen：

1. Phase 1 全量回归与代表 Excel / CSV parity 通过；
2. Structuring 三态、部分投影、行角色、稳定 Draft ID、只读 schema 投影通过；
3. 确定性匹配状态、目标、证据和稳定 decision ID 与冻结语义一致；
4. Candidate Retrieval 排名、完整表级 context、同表冲突证据与稳定 ID 通过；
5. Semantic 安全状态、失败隔离、Proposal 只读边界通过；
6. `PROPOSED` 不生效，人工确认回放纯函数且不重算上游；
7. 新主链是唯一正式路径，旧混合 API / DTO 和临时兼容层零残留；
8. Production 零 Evaluation import；
9. Definition / Knowledge 与 `references/nano-ontoprompt-master` 未被本阶段修改；
10. 全量测试、compile、parity、消费者扫描和 diff 自审全部通过。
11. Instantiation 批次输入、完整性门禁、稳定业务 ID、跨文件去重 / 冲突语义通过；
12. `PERCENT` 存储语义不改变既有 `ObservationSchema` fingerprint，且本阶段不修改本体文件。
