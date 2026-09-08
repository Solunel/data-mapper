# nano-ontoprompt Phase 0 代码分析与借鉴判断

## 1. 结论

Phase 0 的结论是：nano 已经有一条可运行的 v2 数据链，但它不是“上传后自动一路生成图谱”的单一调用链，而是由多个 HTTP 操作人工串联：

```text
POST /api/v2/datasets/upload
  → Dataset(kind=structured) + DatasetVersion(raw bytes)
POST /api/v2/pipelines
  → Pipeline(definition/spec)
POST /api/v2/pipelines/{id}/run 或 run-sync
  → Route A/B/C → Dataset(kind=curated) + DatasetVersion(CSV bytes)
GET /api/v2/curated/{id}/quality
POST /api/v2/curated/{id}/review?action=approve
POST /api/v1/ontologies
POST /api/v2/ontologies/{id}/mappings[/suggest]
POST /api/v2/ontologies/{id}/mappings/{mapping_id}/apply-from-dataset
  或 POST /api/v2/ontologies/{id}/mappings/build-all
  → Entity（概念）+ EntityInstance（行实例）+ Relation
  → Neo4j（主要为概念图；不可用时图查询回退关系库）
```

这条顺序也由端到端用例实际串联，而不是只来自 README：`frontend/src/test/e2e/pipeline_ontology_supply_chain.spec.ts:82-176` 依次上传文件、创建/运行 Pipeline、审批 Curated、创建 OntologyProject、创建 Mapping、调用 `build-all`。

最值得借鉴的是分层方向、显式 Pipeline Step、确定性清洗/质量检查、数据集版本与稳定实例 ID。不能直接沿用的是“由数据集名称/LLM 生成新实体类并立即写入本体”的 Mapping 主逻辑。我们的下一 Phase 应先完成真实 Excel/CSV 的 Raw → Data Schema → Curated 最小闭环，再接已有 Definition/Knowledge 优先的 Mapping。

## 2. 分析范围与本体基线

仓库中的实际参考目录是 `references/nano-ontoprompt-master/`；阶段文件实际名是 `docs/CURRENT_PHASE.md — Phase 0 Final.md`。本分析以当前仓库提交中的代码快照为准。README 仅用于辅助确认项目自述；所有关键结论均落到代码或测试。

对本项目本体只读取了结构与少量代表项：

- `Definition.json` 定义 4 类对象：`Organization`、`Metric`、`ActualObservation`、`BudgetTarget`；5 类关系：`OBSERVES`、`TARGETS`、`FOR_ORGANIZATION`、`CALCULATION`、`PARENT_OF`。
- Mapping 直接相关的指标识别字段是 `Metric.id`、`name_cn`、`aliases`、`definition_cn`、`business_labels`、`value_semantics`；观测实例还必须形成 `organization_id`、`metric_id`、`source`、`period`、`actual_value`、`unit`。
- `Knowledge.json` 当前包含 161 条 `Metric` 和 163 条 `CALCULATION`。Phase 0 未逐条审查业务知识。

因此，对我们的核心问题不是“为每张表发明一个新实体类”，而是先判断字段/行应落到已有 `Metric`、`ActualObservation`、`BudgetTarget` 等哪种结构，并输出 `MATCHED / AMBIGUOUS / UNMATCHED / ONTOLOGY_GAP`。nano 当前没有这一层状态模型。

## 3. 真实主调用链与数据形态

### 3.1 Excel / CSV → Raw Dataset

真实 HTTP 入口是 `backend/app/routers/v2/datasets.py:26-58`。上传端点按扩展名把 CSV/XLSX/XLS 归为 `structured`，随后调用 `DatasetService.create_dataset()` 和 `create_version()`。模型位于 `backend/app/models/v2/dataset.py:7-27`：

```text
文件 bytes
  → Dataset {id, name（无扩展名）, kind, schema_json, latest_version_id}
  → DatasetVersion {version_no, rowcount, storage_uri, checksum}
  → MinIO raw-datasets/datasets/{dataset_id}/v{n}/data.bin
```

实际解析发生在预览/执行时，而不是上传时。`backend/app/services/v2/dataset_service.py:66-121` 的行为是：

- JSON：`json.loads` 后返回对象列表；
- XLSX：仅用 `openpyxl` 读取 `wb.active`，第一行直接作为表头；
- 其他输入：UTF-8 `errors="replace"` 解码后交给 `csv.DictReader`。

对真实企业 Excel 的支持程度因此只是基础级：能读单个 XLSX 活动 Sheet，但没有多 Sheet、合并表头、空白/标题行、表区域探测、公式错误、隐藏 Sheet、单元格来源坐标或格式/单位保留。虽然入口允许 `.xls`，解析器只识别 ZIP 头的 XLSX；旧式 `.xls` 没有对应实现。CSV 也没有编码、分隔符或方言探测。

另有 Connection 抽象，但并非当前可用主入口：`backend/app/tasks/v2/connection_sync.py:8-33` 两个同步函数均为 `pass`，而 `backend/app/routers/v2/connections.py:186-203` 引用的 `app.tasks.v2.sync_tasks` 在当前目录不存在。

### 3.2 Raw Dataset → Pipeline → Curated

Pipeline 的创建、校验、发布、异步/同步执行入口分别位于 `backend/app/routers/v2/pipelines.py:76-110`、`:186-260`、`:263-296`、`:318-343`、`:385-408`。

执行核心是 `backend/app/tasks/v2/pipeline_run.py:360-477`：

1. 从 `definition.nodes` 中收集 connector 的文件/数据集，或回退到 `source_dataset_id`（`:202-243`）；
2. 读取每个源，但结构化数据固定读取 `version_no=1`，最多 10,000 行（`:245-262`）；
3. 按数据种类/Transform 配置选择 Route A/B/C（`:51-77`、`:171-184`）；
4. 执行 route；
5. 把结果重新序列化为 UTF-8 CSV，创建新的 `Dataset(kind="curated")` 和 version（`:274-358`）；
6. 在 `schema_json` 保存 `quality_score`、列名、route、`source_dataset_id`，并把 Curated ID 写回 Pipeline。

三条 route 在 `backend/app/services/v2/pipeline/engine.py:8-51` 中是固定程序链：

- Route A（structured）：`SchemaInferenceStep → CleansingStep → 可选 WideTableSplitStep`；
- Route B（semi）：JSON flatten 或 XML parse → cleansing；
- Route C（unstructured）：document → Markdown → 规则或 LLM 结构化。

需要注意，`dag_compiler.compile_definition()` 虽然生成拓扑顺序，但 `pipeline_run_task()` 只在 `:386` 赋值给 `plan`，之后没有读取它。也就是说，当前 `nodes/edges` 主要是配置/展示外壳，执行语义仍由固定 Route 决定，不是真正按 DAG 节点逐步运行；节点状态也是 route 完成后统一设为 success。

Data Schema 与 Ontology Schema 在代码中可以区分，但 Data Schema 自身有两种形态：

- `SchemaInferenceStep` 在最多 10 行上投票，得到 `{column: string|integer|float|timestamp|boolean}`，只放在 `PipelineContext.meta`，不转换实际值（`backend/app/services/v2/pipeline/steps/schema_inference.py:15-57`）；
- `/datasets/{id}/schema` 再独立从 10 行预览生成 `[{name,type,sample_values}]`（`backend/app/routers/v2/datasets.py:86-133`）。

清洗是确定性实现，支持空值策略、去重、字符串 trim、日期规范化、jagged row 过滤（`backend/app/services/v2/pipeline/steps/cleansing.py:30-124`）。但默认值会直接填空、去重、规范日期，若用于企业数据必须显式配置并保留变更与来源记录。

宽表执行内核按确认的 `split_config` 投影列并去重；但无配置时会调用 LLM，失败后把列机械对半分组，并在 `suggest_only=false` 时自动执行（`backend/app/services/v2/pipeline/steps/wide_table_split.py:10-105`）。这不能作为我们的默认治理行为。

### 3.3 Curated → Quality / Review

Pipeline 产物的真实存储是 `v2_datasets(kind="curated")`，不是 `v2_curated_datasets`。`backend/app/routers/v2/curated.py:36-70` 也从前者列出数据；但审核记录的外键和 `ReviewService` 仍依赖后者。`submit_review()` 会临时复制一个同 ID 的兼容行（`:160-205`），其他 review 入口则直接要求旧模型存在。这是一套尚未收敛的双模型边界。

质量也有两套计算：

- Pipeline 保存时的简化分数：结构化 route 使用行保留率与单元格填充率（`backend/app/tasks/v2/pipeline_run.py:29-49`）；
- 查询质量报告时，`QualityService` 计算完整性、全行唯一性和粗粒度类型一致性（`backend/app/services/v2/curated/quality_service.py:57-157`）。新 Curated 路径最多读取 200 行样本（`backend/app/routers/v2/curated.py:130-157`）。空数据的 overall score 为 1.0，只通过 issue 标出“数据集为空”。

审核编辑仅记录 overlay；`ReviewService.apply_edits_to_snapshot()` 明确不修改原始存储（`backend/app/services/v2/curated/review_service.py:95-119`），而 Mapping 的 `apply-from-dataset` 直接重读存储版本（`backend/app/routers/v2/mappings.py:128-151`）。当前主链没有把已审核编辑合并成 Mapping 实际输入。

### 3.4 Curated → Mapping → Ontology / Graph

Mapping 是显式人工步骤，不由 Pipeline 自动生成：

- `/mappings/suggest` 把 dataset 名、列和样本发给 `AutoMapper`；路径中的 `ontology_id` 没有传给建议器（`backend/app/routers/v2/mappings.py:35-57`）；
- `AutoMapper` 的 LLM 提示要求“设计一个实体类”，回退逻辑则由数据集名生成 CamelCase 类，并把所有列映射为同名属性（`backend/app/services/v2/mapping/auto_mapper.py:39-161`）；
- 用户再提交自由格式的 `entity_class`、`field_mapping` 和主键，形成 `OntologyMapping`（`backend/app/routers/v2/mappings.py:60-78`）。

`MappingService.apply_mapping()` 会补全缺失列映射、必要时重选主键、自动创建一个概念 `Entity`、把每行写成 `EntityInstance`，然后把 Mapping 标为 applied（`backend/app/services/v2/mapping/mapping_service.py:39-74`、`:631-760`、`:949-981`）。`build_all()` 还会继续推断关系、Logic、Action，并写 Neo4j/ChromaDB，最后才返回 `review_required=true`（`:78-228`）。它没有检查 Curated 是否 approved，也没有先产生“本体变更建议”再等待确认。

自动审核触发链目前也不完整：`mapping_apply_task` 只从旧 `CuratedDataset.schema_json.sample_rows` 取数据（`backend/app/tasks/v2/mapping_apply.py:19-35`），而 Pipeline 写入的新 Curated `schema_json` 不包含 `sample_rows`。按当前代码，除非其他路径额外补入该字段，自动 apply 会得到空行；人工 `apply-from-dataset` 才会读取真实 DatasetVersion。

Graph 是下游投影边界。`build_all()` 只把概念 Entity 写入 Neo4j；行实例保存在关系库 `entity_instances`。Graph API 优先读 Neo4j，无数据/不可用时回退关系库 Entity/Relation（`backend/app/routers/v2/graph.py:29-99`）。因此 nano 当前图谱主要是概念图，并非把全部 Curated 行直接写成 Neo4j 实例节点。

nano 还保留一条独立的文档 LLM 本体生成链：`run_extraction()` 从已转换文档调用 `extract_ontology()`，随后按中文名直接 upsert Entity，并可为 LLM 未声明的实例类型自动创建概念（`backend/app/tasks/extraction.py:249-360`、`:410-524`）。这说明 nano 确实会创建或修改运行时本体；它不适合直接作用于我们的正式 Definition/Knowledge。

## 4. 核心代码地图与初步判断

| 模块 | 代码入口 | nano 当前实现 | 判断 | 对我们的处理 |
|---|---|---|---|---|
| Dataset/版本/对象存储 | `routers/v2/datasets.py:26`；`services/v2/dataset_service.py:12`；`models/v2/dataset.py:7` | 元数据与 bytes 分离，带 version/storage URI | `ADAPT` | 保留分层与版本思想；补文件名/扩展名、Sheet/表区域/单元格来源、编码与最新版本读取 |
| Pipeline Step/Context | `services/v2/pipeline/base.py:8`；`pipeline/engine.py:8` | 小型同步 Step，route 固定编排 | `ADAPT` | 保留显式 step/context；让输入输出契约、错误、lineage、统计可验证，不复制伪 DAG |
| SchemaInference | `steps/schema_inference.py:15` | 10 行投票，只产 meta，不转换数据 | `ADAPT` | 规则优先可保留；需全局/分层采样、置信度、混合类型和持久化 Data Schema |
| Cleansing | `steps/cleansing.py:30` | 空值、去重、trim、日期、jagged 规则 | `ADAPT` | 算法可借鉴；所有改变必须显式配置并生成变更记录，不能用首行列集合静默丢行 |
| 宽表拆分 | `steps/wide_table_split.py:10`；`duckdb_service.py:75` | 显式配置投影；无配置时 LLM/对半回退并可自动执行 | `REFERENCE` | 只借鉴“建议与执行分离”；执行必须基于审核后的确定性配置，禁止机械对半拆分 |
| Curated 存储 | `tasks/v2/pipeline_run.py:274-358` | 重新写为 CSV Dataset，schema_json 保存少量元数据 | `ADAPT` | 需要稳定 Curated 契约、类型值、完整 lineage；消除 Dataset/CuratedDataset 双模型 |
| Quality | `curated/quality_service.py:57` | 完整性/重复/类型一致性启发式 | `ADAPT` | 指标框架可用；需全量或可解释采样、主键/单位/期间/业务约束，空集不能视为满分 |
| Review | `curated/review_service.py:8`；`routers/v2/curated.py:160` | 状态与编辑记录分离，但编辑未进入 Mapping 输入 | `REFERENCE` | 保留“建议/审核/生效”思路；下一 Phase 不建设复杂审批平台，先定义可重放的已审核快照 |
| AutoMapper | `mapping/auto_mapper.py:39` | 根据数据生成新 entity_class；LLM 失败后同名映射 | `IGNORE`（作为已有本体匹配器） | 不用于正式 Mapping；其中候选解释/置信度输出形式可作 UI 参考 |
| Mapping 执行 | `mapping/mapping_service.py:39`、`:78` | 自动创建概念、实例、关系、Logic、Action并写下游 | `REFERENCE` | 稳定 ID/upsert 可改造；主流程必须改成已有本体候选解析与四态结果，正式本体写入另行授权 |
| 图存储适配 | `graph/neo4j_service.py:99`；`routers/v2/graph.py:29` | Neo4j MERGE + 关系库回退 | `REFERENCE` | Graph 是 Mapping 后的适配器，不应反向定义 Data Schema 或本体审批语义；非下一 Phase 重点 |
| Connection 增量同步 | `tasks/v2/connection_sync.py:8`；`routers/v2/connections.py:186` | stub 且路由引用缺失任务模块 | `IGNORE` | 当前不作为入口，也不据此设计调度平台 |
| Route B/C 与文档 LLM 提取 | `pipeline/engine.py:22-51`；`tasks/extraction.py:249` | 半结构化/文档提取并可直接生成本体 | `IGNORE`（下一 Phase） | 当前真实 Excel/CSV 闭环完成后再评估；仅参考 LLM 降级与验证边界 |
| 现有 v2 E2E 场景 | `frontend/src/test/e2e/pipeline_ontology_supply_chain.spec.ts:82` | 真实文件串联 API，但 class/pk 是用例硬编码 | `REFERENCE` | 借鉴端到端验收方式，不把硬编码 Mapping 当成自动匹配能力 |

当前没有完整模块可以判为无条件 `REUSE`：我们的数据契约尚未建立，而且参考快照未包含独立 LICENSE 文件（README 自述为 MIT，仍需在复制代码前确认来源与许可证）。这不妨碍复用小型算法或测试思路，但应在下一 Phase 结合目标契约逐项确认。

## 5. nano 与当前架构的关键差异

| 维度 | nano 实际代码 | 我们的要求 |
|---|---|---|
| 本体起点 | OntologyProject 为空壳，Mapping/LLM 可创建新 `Entity.type` | Definition/Knowledge 是正式基线，优先匹配已有对象、指标和关系 |
| Mapping 语义 | 一张 Curated 表 → 一个自由命名 entity_class；列 → 属性 | 字段与记录需落到既有 `Metric`、`ActualObservation`、`BudgetTarget` 等明确结构 |
| 名称匹配 | 不查询目标 Ontology；规则回退主要是列名归一化 | 正式名、`aliases`、已知规则优先；模糊语义才交给 LLM |
| 结果状态 | Mapping 只有 draft/applied 等执行状态 | 每个候选需区分 `MATCHED / AMBIGUOUS / UNMATCHED / ONTOLOGY_GAP` |
| 本体变更 | apply/build/extraction 可直接 upsert 概念、关系、Logic、Action | 只能生成显式变更建议；未经审核不得改 Definition/Knowledge |
| Curated | CSV bytes + 少量 schema_json；双模型兼容 | 稳定、类型化、可追踪，并能服务后续异常检测/根因定位 |
| Graph | 主要同步概念 Entity，实例留在 SQL | Ontology Instance Data 的存储适配应在 Mapping 契约之后决定 |

## 6. 规则与 LLM 的合理边界

优先用确定性代码完成：文件格式/编码/Sheet 识别，表头与数据区候选，类型与日期/单位规范，质量统计，主键与 lineage，正式名称/别名精确匹配，Definition 结构校验，稳定 ID，以及已经人工确认的转换/拆分规则。

LLM 只适合辅助：复杂表头语义解释、多个已有指标候选的排序与理由、宽表拆分建议、无法由名称/别名确定的业务语义，以及 `ONTOLOGY_GAP` 的结构化变更建议。LLM 输出必须保留候选、证据、置信度和不确定性；不能直接改正式本体，也不能把“未匹配”自动等同于“本体缺口”。

## 7. 推荐的下一 Phase 切入点

建议下一 Phase 只做“真实 Excel/CSV → 可追踪 Curated”的最小闭环：

1. 先定义最小 Raw Dataset、Data Schema、Curated Dataset 和 lineage 契约，明确 Sheet、原始表头、数据区域、源行/列位置与转换记录；
2. 用一个真实 Excel 和一个 CSV 验证接入，至少覆盖多 Sheet/非第一行表头中的一个真实难点；
3. 借鉴 nano 的 Step/Context、schema inference、cleansing 和 quality 思路，但让每一步显式、确定、可重放；
4. 产出类型化 Curated 和质量报告，暂不写 Neo4j、不调用本体生成、不建设复杂审批系统；
5. 同时预留下一阶段 Mapping 输入所需字段：原始列名、规范列名、样本/统计、单位/期间候选、来源位置。

这个切入点先解决 nano 最薄弱、又是我们后续 Mapping 的前置条件；等 Curated 契约稳定后，再实现 Definition/Knowledge 索引与四态 Mapping。

## 8. 尚未确认的问题与当前限制

- 参考目录没有上游 commit/tag 元数据；只能定位到当前仓库快照，无法确认它与上游哪个版本完全对应。
- 当前项目 `src/`、`tests/` 为空，尚无本项目真实样例和运行契约；Phase 0 因而只能给出借鉴判断，不能验证集成适配成本。
- nano 的 E2E/单元测试同时使用新 `Dataset(kind="curated")` 与旧 `CuratedDataset`，且部分期望与当前 `MappingService.build_all()` 返回语义并不一致；不能把测试文件存在等同于当前快照全套回归已通过。
- 还未决定 Curated 的持久化格式、单文件大小边界和对象存储是否是下一 Phase 的必要依赖；应由真实样例驱动，而不是照搬 MinIO/DuckDB/Celery。
- 尚未抽样验证 161 个 Metric 的别名覆盖质量。该工作属于已有本体 Mapping 阶段，不是 Phase 0 的必要前置。

至此已有足够证据说明主流程、完成核心模块分类并确定下一 Phase 入口，按 Phase 0 停止条件不再扩大到前端、部署、认证、异常检测或完整本体知识审查。
