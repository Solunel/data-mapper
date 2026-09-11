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
```

`ResolvedObservation` 是 `ObservationDraft`、当前有效 Metric ID 与确定性绑定的
Organization ID 的组合，不是正式 `ActualObservation`。Organization 语义匹配、
Ontology Instantiation、正式 observation ID 和数据库写入均不在当前范围。

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
- Definition / Knowledge 与本体变更建议均保持只读，Proposal 不会自动执行。

## Evaluation

Gold review、Recall@K 与 Semantic Pilot 位于 `data_mapper.evaluation`，依赖方向为：

```text
Evaluation → Production
Production ─X→ Evaluation
```

## Reference

`references/nano-ontoprompt-master/` 仅作参考，不属于生产依赖，也不在本次
Clean Refactor 中修改。
