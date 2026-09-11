# ARCHITECTURE.md

## 1. 项目是干什么的

本项目把企业 Excel / CSV 转换成可追踪、可解释的业务数据，并将报表指标映射到已有的 Definition / Knowledge 本体。

它解决两个相邻但不同的问题：

1. 先把原始表格整理成程序能够稳定处理的数据；
2. 再判断表格里的业务指标在本体中对应什么。

已有本体优先使用，但不假设它永远完整。确认本体无法表达真实业务概念时，系统可以提出变更建议，但不能静默修改正式本体。

---

## 2. 当前状态

Data Mapper Clean Refactor 已按职责边界完成，当前正式实现为：

```text
Data Preparation：数据接入与 Curated 整理
Observation Structuring：表结构、行角色与 ObservationDraft
Metric Resolution：确定性匹配 + 可选 Candidate Retrieval / Semantic Judge
ID Binding：绑定有效 Metric ID + 确定性 Organization ID
Workflow：EffectiveMetricResolution + ResolvedObservation
```

旧混合 Mapping API / DTO 已退役，新主链是唯一正式路径。
[Data Mapper Clean Refactor Plan V1.0.2](DATA_MAPPER_CLEAN_REFACTOR_PLAN.md)
保留为冻结施工与验收依据；R0 → R1 → R2 → R3 的执行记录见
`docs/refactor/`。

---

## 3. 一张表进入系统后经历什么

重构后的目标主链是：

```text
Excel / CSV
→ Data Preparation
→ CuratedDataset
→ Observation Structuring
→ MetricSubject + ObservationDraft
→ Metric Resolution
   ├─ Deterministic Resolution
   └─ Semantic fallback（可选）
→ ID Binding（Metric + Organization）
→ ResolvedObservation
→ 未来：Ontology Instantiation
→ ActualObservation
```

大白话说就是：

> 先把“这条业务数据本身是什么”整理清楚，再单独判断“这个指标对应本体里的谁”。

Data Preparation 包括读取文件、保留 Raw 来源、识别表结构、清洗和整形，产出 CuratedDataset。Curated 是规范化数据，不是本体实例。

Data Schema 描述“表长什么样”，例如 Sheet、列、表头和类型；Ontology Schema 描述“数据是什么意思”，例如 Organization、Metric、关系和计算语义。两者不能混用。

---

## 4. 为什么拆开 Observation Structuring 和 Metric Resolution

例如报表中有一条数据：

```text
一级子公司A | 主营业务利润 | 2025-01 | 1000万元
```

即使当前本体中没有可以可靠匹配的“主营业务利润”，这条 1000 万元的源观测仍然客观存在。把结构化和 Metric 匹配绑在一起，会让“本体暂时没答案”错误地影响“源数据是否存在”。

因此重构后：

- Observation Structuring 负责理解源数据；
- Metric Resolution 负责解析 Metric 引用；
- 两者最后组合成 ResolvedObservation。

这条边界让未匹配数据不丢失，也让确定性规则、AI 判断和未来本体实例化各自保持清晰。

---

## 5. Observation Structuring

Observation Structuring 不会脱离本体随意发明一套观测结构。它会参考 Definition 中 `ActualObservation` 的正式结构作为业务骨架，再从 CuratedDataset 中看懂：

- 公司原始名称；
- 指标名称和行角色；
- 时间与期间口径；
- 数值、单位和业务范围；
- 文件、Sheet、行列等来源 / 追踪信息。

它产出待解析指标 `MetricSubject` 和观测草稿 `ObservationDraft`。Draft 可以比正式 `ActualObservation` 更丰富：除了未来正式实例需要的业务信息，还可以保留文件、Sheet、行列、Raw / Curated 身份、结构化证据等 Data Mapper 追踪信息。它也不必和正式实例采用完全相同的字段形状，例如 Draft 可以拆开保存期间口径和原始 / 规范化单位，未来实例化时再组装成 Definition 要求的 `Period` 和 `Unit`。核心原则是：

> `ObservationDraft` 以正式观测结构为业务骨架，但不依赖 `metric_id`、完整 `organization_id` 或其他未来实例化条件才成立。

报表中的组织名称作为 `organization_value` 保留。如果调用方已经明确知道 `organization_id`，Structuring 只负责原样保留；不知道就保持为空。Observation Structuring 不负责查询、猜测或验证 Organization。Structuring 完成后，ID Binding 才会校验已知引用，或使用 `organization_value` 对只读 Catalog 中的 Organization 做 `name_cn` 唯一精确匹配。类似地，Definition 中 `ActualObservation` 的 required 表示未来正式实例必须满足，不代表 Draft 现在必须把所有正式引用和状态凑齐。

它也不从 Metric override、ontology gap 或 AI 结果反向猜测行结构。需要明确某行是指标行时，由结构化请求显式提供行提示。R1 不新增 `status` 绑定或推断。

轻量只读 loader 从 Definition 提取窄 `ObservationSchema`，由 `OntologyCatalog.observation_schema` 提供给 Structuring。它描述正式 `ActualObservation` 的业务槽位和当前需要的 Period / Unit 等结构；required 只供未来实例化参考。Structuring 不接收完整 Catalog，不读取 Metric / Organization 实例目录，也不做正式实例化完整性判断。

表级 `NEEDS_BINDING` 表示仍有部分结构绑定未完成，不会否定已经完整绑定的值。完整值仍可生成 `ObservationDraft`，未完整值进入 unresolved report；只有 `BLOCKED` 不生成 Draft。

---

## 6. Metric Resolution

Metric Resolution 只回答：

> 报表里的这个 MetricSubject，对应当前本体 revision 中的哪个 Metric？

默认先使用可解释的确定性规则，例如显式 override、正式名称、alias 和受限规范化匹配。能可靠解决就结束；不能解决时，根据运行模式决定是否使用语义 fallback。

```text
DETERMINISTIC_ONLY
只使用确定性规则，不调用 LLM。

DETERMINISTIC_WITH_SEMANTIC_FALLBACK
规则先处理能确定的，只把剩余未解决项交给候选召回和 LLM 判断。
```

三个边界始终不变：

- 已被确定性规则可靠匹配的 Metric 不再询问 LLM；
- Candidate Retrieval 只是寻找可能候选，不等于 Mapping 成功；
- AI 产生的 `PROPOSED` 只是待确认建议，不能直接改变正式有效映射。

Metric 结果仍区分 `MATCHED / AMBIGUOUS / UNMATCHED / ONTOLOGY_GAP`。普通匹配失败不等于本体缺口；无法确定时应保留不确定性，不能为了提高命中率猜测。

Metric Resolution 只读使用完整的 Structuring 结果构造语义上下文，包括 NOTE、前后行、同表其他指标及其确定性结果，以及 scope、period 和 unit。这样可以保留同表独立项目等强负证据，但 Resolution 不能反向改变行角色或 Draft。

---

## 7. Mapping 结果与 ID

`ResolvedObservation` 表示：

```text
一条结构化好的 ObservationDraft
+
当前真正生效的 Metric Resolution 结果
+
经过只读校验或唯一精确匹配得到的 Organization ID
```

它允许 Metric 或 Organization 任一方仍然 unresolved。`ResolvedObservation.metric_id` 始终等于 `metric_resolution.current_metric_id`；未经确认的 Semantic `PROPOSED` 不会写入 `metric_id`。`ResolvedObservation` 不是 `ActualObservation`，尚未执行 Ontology Instantiation。

LLM 首次给出的 `PROPOSED` 不会自动生效。人工显式确认后，系统通过纯函数重新派生 `EffectiveMetricResolution` 和 `ResolvedObservation`；这个过程不重跑 Structuring、候选召回或 LLM，也不写入本体或数据库。

几个 ID 的含义必须分开：

- `observation_draft_id`：系统生成，用于追踪一条观测从哪份数据、哪个位置结构化出来；
- `metric_id`：ID Binding 从 `EffectiveMetricResolution.current_metric_id` 取得，Excel 不需要提供，也不能伪造；
- `organization_id`：已知引用必须存在于当前 Catalog；否则按 `organization_value` 与 `name_cn` 唯一精确匹配，无命中或多命中保持为空；
- `ActualObservation.id`：尚未实现，未来在 Ontology Instantiation 阶段单独设计。

```text
observation_draft_id ≠ ActualObservation.id
```

本次重构只整理技术身份边界，不顺手设计正式业务实例 ID。未来 Ontology Instantiation 时，再从丰富的 Draft / Resolution 结果中解析并挑出 Definition 当前要求的正式字段，形成 `ActualObservation`；Draft 中额外的来源、行列、Raw / Curated identity、evidence 等追踪信息继续保留在 Data Mapper / 数据血缘侧，不要求全部写入本体实例。

---

## 8. 本体演化与安全边界

Metric Resolution 依赖带明确 `ontology_revision` 的只读 `OntologyCatalog`；同一个 Catalog 还提供 loader 从 Definition 投影出的窄 `observation_schema`，workflow 只把这个窄值对象交给 Observation Structuring。两层都不直接理解 Definition / Knowledge JSON 的存储细节，不建设复杂 provider、repository 或图数据库框架。

当 Metric 暂时无法解析时：

- ObservationDraft 继续保留；
- `metric_id` 可以为空；
- 系统不能丢弃源数据、随便匹配相似 Metric 或编造 ID；
- Mapping、规则和 LLM 都不能自动修改 Definition / Knowledge。

如果经过明确判断确认本体确实缺少概念，可以生成 `OntologyChangeProposal`。它只是待审核建议，正式本体变更必须显式发生，Proposal 不会自动执行。
当前 Definition / Knowledge 可以都来自 JSON，但核心 Mapping 不与 JSON 绑定。未来可以保持 Definition 为 JSON、将 Knowledge 迁到 Neo4j，核心匹配流程原则上不需要改。
---

## 9. Production、Evaluation 与参考项目

Production 是实际处理业务报表的代码，包括结构化、确定性匹配、候选召回、语义判断和有效映射。Evaluation 使用 Gold、困难样例、Recall 和 Pilot 检查这些能力是否可靠。

依赖方向固定为：

```text
Evaluation → Production
Production ─X→ Evaluation
```

评测可以调用生产能力，但真实报表处理不能依赖 Gold 或 Pilot 才能运行，也不能由评测数据制造业务事实。

`nano-ontoprompt` 继续作为重要参考实现。每次只根据真实需求判断 `REUSE / ADAPT / REFERENCE / IGNORE`，不机械复制。它更偏从数据生成本体，而本项目优先映射到已有 Definition / Knowledge，因此不会照搬其全部架构。

---

## 10. 当前范围与后续方向

本次 Clean Refactor 到 `ResolvedObservation` 为止，主要目标是拆清职责并保持现有业务行为可回归验证。

当前不做：

- Organization 模糊或语义解析；
- Ontology Instantiation、实例化完整性判断和 ActualObservation；
- 正式 observation ID；
- Neo4j 或其他持久化写入；
- 自动本体变更；
- embedding、额外 fuzzy / CALCULATION 扩展；
- Web API、前端或审批平台；
- 为未来规模预建复杂抽象。

未来可以在 ResolvedObservation 之后独立建设 Ontology Instantiation：按 Definition 补齐并校验 `organization_id / metric_id / business_scope / source / period / actual_value / unit / status` 等正式字段，再形成 ActualObservation。Organization 的模糊或语义解析仍可作为后续独立能力，但不属于当前确定性 ID Binding。CuratedDataset 仍可并行服务异常检测、根因定位、AI 问答和其他业务分析；这些下游不属于当前 Data Mapper 重构。
