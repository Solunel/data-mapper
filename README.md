# Enterprise Data Mapping

企业 Excel / CSV 数据接入、观测结构化与本体指标解析。

## 当前正式主链

```text
Excel / CSV
→ Data Preparation
→ CuratedDataset
→ Observation Structuring
→ Metric Resolution（确定性匹配 + 可选语义 fallback）
→ EffectiveMetricResolution
→ ID Binding（Metric + Organization）
→ ResolvedObservation
→ Ontology Instantiation Gate
   ├─ ActualObservation
   ├─ BlockedObservation
   └─ UnresolvedMetricItem
```

`ResolvedObservation` 仍是 Mapping 层终点。独立的纯函数式 Instantiation Gate 可以
批量接收一个或多个 `DataMappingResult`，构造完整 `ActualObservation`，并显式返回
阻塞记录和按 MetricSubject 聚合的未匹配指标；本阶段不写数据库。

详细边界见 [ARCHITECTURE.md](docs/ARCHITECTURE.md) 与
[CURRENT_PHASE.md](docs/CURRENT_PHASE.md)。冻结施工基线为
[Data Mapper Clean Refactor Plan V1.0.2](docs/DATA_MAPPER_CLEAN_REFACTOR_PLAN.md)。

## 进程内调用

Mapping Core 只接收内存中的只读 `OntologyCatalog`，不依赖 JSON 或 Neo4j
的具体存储实现。当前只读 loader 从 Definition / Knowledge JSON 构造 Catalog：

```python
from pathlib import Path

from data_mapper import (
    MetricResolutionMode,
    MetricResolutionRequest,
    ObservationStructuringRequest,
    ScalarBinding,
    curate_file,
    load_ontology_catalog,
    map_curated_observations,
    instantiate_observations,
)

root = Path(__file__).resolve().parent
curated = curate_file(
    root / "tests/fixtures/财务快报-利润表.xlsx"
).curated_datasets[0]
catalog = load_ontology_catalog(
    root / "ontology/Definition.json",
    root / "ontology/Knowledge.json",
)

result = map_curated_observations(
    curated,
    ObservationStructuringRequest(
        curated_id=curated.curated_id,
        unit=ScalarBinding(constant="万元"),
    ),
    MetricResolutionRequest(mode=MetricResolutionMode.DETERMINISTIC_ONLY),
    catalog,
)

print(result.structuring_result.report.structure_status)
print(result.metric_resolution_result.report.deterministic_status_counts)
print(len(result.resolved_observations))

instantiation = instantiate_observations(
    (result,),
    catalog,
    status="DRAFT",
)
print(len(instantiation.actual_observations))
print(len(instantiation.blocked_observations))
print(len(instantiation.unresolved_metrics))
```

启用 `DETERMINISTIC_WITH_SEMANTIC_FALLBACK` 时必须显式传入 `SemanticJudge`。
LLM 返回的 `PROPOSED` resolution 不会自动生效；人工确认后通过
`apply_reviewed_resolutions()` 纯函数式回放，不重跑结构化、召回或 LLM。

## ID 与只读边界

- `observation_draft_id` 追踪结构化观测来源，不是正式 observation ID；
- `metric_subject_id` 连接 Draft、Metric Decision 与有效解析，不是本体外键；
- `resolution_run_id` 追踪一次 Metric Resolution；
- `ResolvedObservation.metric_id` 只来自 `EffectiveMetricResolution.current_metric_id`；
- `organization_id` 在 Structuring 后校验已知引用，或按 `name_cn` 唯一精确匹配；
- `ActualObservation.id` 只由 Organization、Metric、business scope 与 Period 构成；
- `source` 暂时只存表名，但不参与正式业务 ID；
- 跨 Mapping 结果的同身份同值输入会合并，数值或单位冲突会整组阻塞；
- Definition / Knowledge 与本体变更建议均保持只读，Proposal 不会自动执行。

本地 CLI 可使用 `--mode instantiation --status DRAFT`。该模式先收集本次所有 Mapping
结果，再统一执行批量实例化，因此能够识别跨文件和跨 Sheet 的重复与冲突。

## Evaluation

Gold review、Recall@K 与 Semantic Pilot 位于 `data_mapper.evaluation`，依赖方向为：

```text
Evaluation → Production
Production ─X→ Evaluation
```

## Reference

`references/nano-ontoprompt-master/` 仅作参考，不属于生产依赖，也不在本次
Clean Refactor 中修改。
