# Enterprise Data Mapping

企业业务数据接入与本体映射项目。

## 当前目标

参考 nano-ontoprompt，
实现：

Excel / CSV
→ Raw Dataset
→ Pipeline
→ Curated Dataset
→ Mapping
→ Ontology Instance Data

## 当前阶段

Phase 2 — 指标在行 Curated → 可解释观测 Mapping 最小闭环

详见：
- AGENTS.md
- docs/ARCHITECTURE.md
- docs/CURRENT_PHASE.md

## 当前本体

- ontology/Definition.json
- ontology/Knowledge.json

## Reference

本项目当前主要参考开源项目：

* `jingw2/nano-ontoprompt`

参考代码保存在：

`references/nano-ontoprompt/`

该目录仅作为代码研究与实现参考，默认不直接修改。

当前参考版本来自 GitHub `master` 分支快照。后续如果能够确定对应的 upstream commit，应在此补充具体 commit hash，以保证参考版本可追踪。

## Phase 2 进程内调用

Mapping Core 只接收内存中的 `OntologyCatalog`。当前 Definition / Knowledge JSON
仅由轻量只读 loader 负责转换：

```python
from pathlib import Path

from data_mapper import (
    MappingRequest,
    ScalarBinding,
    curate_file,
    load_ontology_catalog,
    map_curated_dataset,
)

root = Path(__file__).resolve().parent
curated = curate_file(root / "tests/fixtures/财务快报-利润表.xlsx").curated_datasets[0]
catalog = load_ontology_catalog(
    root / "ontology/Definition.json",
    root / "ontology/Knowledge.json",
)
request = MappingRequest(
    curated_id=curated.curated_id,
    unit=ScalarBinding(constant="万元"),
)
result = map_curated_dataset(curated, request, catalog)

print(result.plan.table_mapping_plan.structure_status)
print(result.report.metric_status_counts)
```

Metric 匹配前会先保留报表 `raw_label`，并只对明确的编号、`其中：`、
`加：/减：` 和已确认展示标记生成带证据的 `comparison_name`。明确的分类标题与
注释行不会进入 Matcher；不确定行保持 `UNKNOWN`，普通 `UNMATCHED` 不会自动
标记为本体缺口候选。这里不包含 fuzzy、embedding 或 LLM 判断。

对成本归属等多业务范围列，应使用 `ValueFieldBinding` 显式给出
`business_scope`、期间和单位。系统不会拆解多层表头后猜测业务语义。

`ObservationCandidate.candidate_id` 是来源 Mapping 候选身份，不是正式
`ActualObservation.id`。本阶段不会修改 Curated、Definition、Knowledge，
也不会创建正式实例或写入图存储。
