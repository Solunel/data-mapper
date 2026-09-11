# ActualObservation 实例化与未匹配指标清单设计

**状态：V1.0.1 设计已审核冻结；代码实施中**  
**版本：V1.0.1**  
**设计基线：** `CURRENT_PHASE.md`、`ARCHITECTURE.md`、冻结的
`DATA_MAPPER_CLEAN_REFACTOR_PLAN.md` V1.0.2，以及当前代码与只读
Definition / Knowledge。  
**实现状态：** 本文只设计，不代表代码已经实现。

---

## 1. 结论

下一阶段应在现有 `DataMappingResult` 之后增加一个独立、纯函数式的
Ontology Instantiation 步骤：

```text
ResolvedObservation[]
        │
        ├── 按 MetricSubject 汇总 metric_id = null
        │       └── UnresolvedMetricItem[]
        │
        └── Instantiation Gate
                ├── PASS    → ActualObservation[]
                └── BLOCKED → BlockedObservation[]
```

推荐公开入口：

```python
instantiate_observations(
    mapping_results: Sequence[DataMappingResult],
    catalog: OntologyCatalog,
    *,
    status: str,
) -> OntologyInstantiationResult
```

入口统一采用批量语义；单个 Mapping 结果由调用方传入一元素序列。这样不新增第二套
API，同时使不同文件、不同 Sheet 产生的业务事实能在同一次实例化中统一去重和检查
冲突。

关键设计决定如下：

1. 保持 `DataMappingResult` 和 `ResolvedObservation` 的冻结语义不变，不把实例化
   readiness、blocked reason 或正式实例反向塞回 Mapping 层。
2. 只有完整通过门禁的记录才构造 `ActualObservation`；失败记录保留原始
   `ResolvedObservation` 和结构化原因，不生成残缺实例。
3. `ActualObservation.id` 使用稳定业务身份的规范 JSON 计算 SHA-256，不使用随机
   UUID、运行时间、LLM 输出、`actual_value`、源行号或 Draft 技术 ID。
4. `ActualObservation.source` 暂时只存表名，即
   `ObservationDraft.sheet_name`；文件名和行列继续留在 Data Mapper provenance。
5. 当前业务身份由 `organization_id + metric_id + business_scope + period`
   组成；`source` 是 required provenance 属性，但不参与业务身份，`unit`、
   `actual_value`、`status` 和技术追踪字段同样不参与身份。
6. 同一批次（一个或多个 `DataMappingResult`）若多条记录产生相同业务身份：事实
   payload（`actual_value + unit`）相同则
   确定性合并为一个 `ActualObservation`；payload 冲突则整组标为
   `BLOCKED: CONFLICTING_BUSINESS_IDENTITY`。不使用 `source_row` 伪造唯一性。
7. 当前 Definition 只有 `DRAFT / ACTIVE / INACTIVE` 值域，没有观测状态推导规则。
   因此 `status` 必须由调用方显式传入并经过值域校验；函数不设置隐式默认值。
   当前无发布动作的本地生成场景建议调用方显式选择 `DRAFT`。
8. 未匹配指标清单按 `metric_subject_id` 汇总，不按 Observation 展开；同一指标行的
   本期值、本年累计值等只形成一项。

本阶段不写 Neo4j，不修改 Definition / Knowledge，不重新运行或改变 Metric
Resolution，不引入人工审核、回流、repository、service、adapter 或 event bus。

---

## 2. 当前事实基线

### 2.1 主流程已经走到哪里

当前正式生产链已经稳定到 `ResolvedObservation`：

```text
真实 Excel / CSV
→ Data Preparation
→ CuratedDataset
→ Observation Structuring
→ ObservationDraft
→ Metric Resolution
→ EffectiveMetricResolution
→ ID Binding（Metric + Organization）
→ ResolvedObservation
```

`map_curated_observations()` 是当前唯一正式入口。它返回的
`DataMappingResult` 已包含：

```text
structuring_result
metric_resolution_result
effective_metric_resolutions
resolved_observations
```

当前 `ResolvedObservation` 直接暴露：

```text
metric_id
organization_id
observation: ObservationDraft
metric_resolution: EffectiveMetricResolution
```

其中 `metric_id` 始终来自
`EffectiveMetricResolution.current_metric_id`；未经确认的 Semantic
`PROPOSED` 不会进入有效 `metric_id`。Organization ID 已按当前 Catalog 做已知引用
校验或 `name_cn` 唯一精确匹配，无命中或多命中时保持 `null`。

### 2.2 Definition 的正式约束

当前 Definition 将 `ActualObservation` 的以下字段全部标为 required：

```text
id: string
organization_id: reference -> Organization.id
metric_id: reference -> Metric.id
business_scope: string
source: string
period: struct -> Period
actual_value: number
unit: enum -> Unit
status: enum -> Status
```

`Period` 的三个字段也全部 required：

```text
period_type: PeriodType
period_key: string
period_basis: PeriodBasis
```

当前值域和格式规则为：

| 字段 | 合法值 / 格式 |
| --- | --- |
| `PeriodType` | `MONTH / QUARTER / YEAR` |
| `MONTH.period_key` | `YYYY-MM`，月份必须为 01～12 |
| `QUARTER.period_key` | `YYYY-Q1`～`YYYY-Q4` |
| `YEAR.period_key` | `YYYY` |
| `PeriodBasis` | `PERIOD_VALUE / YEAR_TO_DATE / PERIOD_BEGIN / PERIOD_END` |
| `Unit` | `CNY_10K / PERCENT / PERSON / CNY_10K_PER_PERSON` |
| `Status` | `DRAFT / ACTIVE / INACTIVE` |

`PERCENT` 还声明了 `storage_semantics: 0_to_1`。当前
`ObservationSchema.unit_values` 只投影 enum key 与中文显示名，且其结构与 fingerprint
均属于已冻结的 Structuring / Mapping 上游契约。本阶段不得为实例化校验修改该 schema
或重新计算其 fingerprint；应由 `OntologyCatalog` 从同一份 Definition 额外提供
`unit_storage_semantics` 最小只读投影，仅供 Instantiation 使用。

### 2.3 当前可复用能力

可直接复用：

- `ObservationDraft` 中的业务字段与完整 provenance；
- `EffectiveMetricResolution` 的有效状态、有效 Metric ID 与来源证据；
- `ResolvedObservation.metric_id / organization_id`；
- `OntologyCatalog.metric_by_id()`、`organization_ids` 与
  `observation_schema`；实现阶段在 Catalog 顶层补充与 schema 分离的
  `unit_storage_semantics` 只读投影；
- `ObservationSchema` 已有的 required 字段、Period / Unit / Status 值域和
  fingerprint；
- 当前稳定 ID 的“规范 JSON + SHA-256 + namespace”实现模式；
- `MetricSubject`、`MetricDecision`、`MetricCandidateSet` 与
  `SemanticResolution` 的现有关联链。

不应复用为正式实例 ID：

- `observation_draft_id`：表达源数据经特定 structuring rule 形成的技术草稿；
- `metric_subject_id`：表达某个来源位置的指标主体；
- `source_row / source_column`：是定位信息，不是稳定业务维度；
- `resolution_id / ontology_revision`：是决议与目录 revision 的追踪信息。

### 2.4 当前数据暴露的身份冲突

2026-09-11 使用 `conda nano-ontoprompt-py311`、当前工作树代码和当前只读
Knowledge，对 `reports/` 中 45 份 2025-01～03 报表执行了现有主链检查。对 6,660 条
同时具有 Metric、Organization 和规范单位的 `ResolvedObservation`，分别按“包含表名
source”和“不包含 source”两种候选业务键统计，结果完全一致：

- 有 600 组业务身份重复；
- 其中 570 组是事实 payload（`actual_value + unit`）完全一致的重复观测，共涉及
  1,710 条输入，典型情况是不同
  月报重复携带同一年度期初值；
- 另有 30 组 payload 冲突，共涉及 60 条输入；
- 两种候选业务键之间没有出现分组差异，跨表名重复组数量为 0。

典型冲突发生在同一利润表：源行 22 的 `3.△利息收入` 与源行 42 的
`其中：利息收入` 都映射到 `qc.interest_income`，且 Organization、
`business_scope=公司整体`、Period 和表名全部相同，但数值不同。

样例说明：把表名加入身份并没有提供额外区分能力；30 组真实冲突即使包含表名仍然
存在。与此同时，`source` 的 required 约束只证明正式实例必须记录来源，不证明来源
属于业务键。若未来相同事实来自不同表名，把 source 纳入 ID 反而会令同一业务事实
生成多个实例，无法按业务身份识别重复或冲突。

因此最终结论是：`ActualObservation.id` **不包含 `source`**。事实 payload 相同的重复
输入可以用同一业务 ID 合并；数值或单位冲突的同身份输入则说明当前正式字段尚不能
表达业务差异。把 `source`、`source_row`、
`metric_subject_id` 或 `actual_value` 加入正式 ID 只能掩盖模型缺口，不能形成可靠的
业务身份。因此本阶段必须把这类冲突作为门禁阻塞原因明确保留，而不是伪造两个看似
合法的正式实例。

上述数量是当前样例检查结果，不是写入代码的固定阈值。

### 2.5 nano-ontoprompt 借鉴判断

与本阶段最相关的是 nano 的 Mapping 实例 ID：它优先使用来源主键，否则退化为整行
hash，再通过 UUID5 形成稳定行 ID。分类为 **REFERENCE**：

- 可参考“相同稳定身份输入得到相同 ID”的原则；
- 不直接复用 UUID5、数据库 merge、EntityInstance、row hash 或自动写图逻辑；
- row hash 会把 `actual_value` 等整行内容带入身份，不能满足本项目“值变化不改变业务
  身份”的要求；
- nano 的实例化与 SQL / Neo4j 写入、本体创建耦合，而本阶段明确只做纯内存投影与
  门禁。

---

## 3. 阶段目标与边界

### 3.1 目标

本阶段只完成：

1. 对 `ResolvedObservation` 执行最小且严格的实例化门禁；
2. 把通过门禁的记录确定性构造成 Definition 兼容的
   `ActualObservation`；
3. 保留未通过记录及全部明确原因；
4. 把没有有效 `metric_id` 的 MetricSubject 汇总成未匹配指标清单；
5. 对相同输入提供稳定、可重放、可 JSON 序列化的输出。

批次至少包含一个 `DataMappingResult`；每个结果仍保持其原有单 Curated 数据集边界，
Instantiation 只在读取后统一分组，不合并或改写 Mapping DTO。

### 3.2 明确不做

- 不修改 Observation Structuring、确定性 Metric 匹配、Candidate Retrieval 或
  Semantic fallback 语义；
- 不把未确认 Semantic Proposal 当成有效 Metric；
- 不自动创建 Metric、Organization 或其他本体对象；
- 不修改、发布或回写 Definition / Knowledge；
- 不实现人工审核、Review Queue、审核状态机、审核数据库或回流；
- 不实现 Ontology Change Proposal 的审批与执行；
- 不写 Neo4j、SQL、文件型实例库或其他持久化；
- 不做实例版本管理、更新策略或跨运行冲突合并；
- 不把异常检测、根因分析混入实例化；
- 不为未来存储预建 repository、service、adapter、registry 或 event bus。

---

## 4. `ResolvedObservation` 与 `ActualObservation`

| 维度 | `ResolvedObservation` | `ActualObservation` |
| --- | --- | --- |
| 所属层 | Mapping 工作结果 | 正式本体实例 |
| 是否允许 `metric_id = null` | 允许 | 不允许 |
| 是否允许 `organization_id = null` | 允许 | 不允许 |
| Period 形态 | Draft 中拆分字段 | 正式 `Period` struct |
| Unit 形态 | `unit_raw` + `unit_normalized` | 合法 `Unit` enum key |
| status | 当前没有 | required，必须显式确定 |
| ID | Draft 技术追踪 ID | 稳定业务身份 ID |
| provenance | 保留文件、Sheet、行列、Raw/Curated identity 和 evidence | 只包含 Definition 属性 |
| 可否直接视为可写本体 | 否 | 通过门禁后可以，但本阶段仍不执行持久化 |

`ActualObservation` 不是删减版 Draft，也不是带 readiness 的工作对象。它一旦被
构造，九个 Definition required 字段必须全部合法；额外 provenance 继续保留在
`DataMappingResult` 或 `BlockedObservation.resolved_observation` 中，不塞入正式对象。

---

## 5. 最小数据结构与模块落点

建议保持扁平结构，只新增一个实现模块：

```text
src/data_mapper/
├─ observation_contracts.py   # 增加 Period、ActualObservation
├─ ontology_catalog.py        # Catalog 顶层补只读 unit storage semantics 投影
├─ ontology_instantiation.py  # 门禁、构造、ID、blocked、未匹配汇总
├─ workflow.py                # 保持现有 Mapping 入口语义不变
└─ __init__.py                # 导出新公开入口与结果契约
```

不新增 `ontology_instantiation_contracts.py`、目录层级或存储抽象。下面是建议的最小
逻辑形态，具体字段顺序可按现有 dataclass 风格调整：

```python
@dataclass(frozen=True)
class Period(JsonContract):
    period_type: str
    period_key: str
    period_basis: str


@dataclass(frozen=True)
class ActualObservation(JsonContract):
    id: str
    organization_id: str
    metric_id: str
    business_scope: str
    source: str
    period: Period
    actual_value: int | float
    unit: str
    status: str


@dataclass(frozen=True)
class InstantiationIssue(JsonContract):
    code: str
    field: str | None
    message: str


@dataclass(frozen=True)
class BlockedObservation(JsonContract):
    resolved_observation: ResolvedObservation
    reasons: tuple[InstantiationIssue, ...]


@dataclass(frozen=True)
class UnresolvedMetricItem(JsonContract):
    metric_subject_id: str
    metric_name: str
    comparison_name: str
    effective_status: str
    source_metric_decision_id: str
    source_file: str
    sheet_name: str
    source_row: int
    organization_value: str | None
    organization_id: str | None
    observation_count: int
    candidate_metric_ids: tuple[str, ...] = ()
    source_resolution_id: str | None = None


@dataclass(frozen=True)
class OntologyInstantiationResult(JsonContract):
    ontology_revision: str
    observation_schema_fingerprint: str
    actual_observations: tuple[ActualObservation, ...]
    unresolved_metrics: tuple[UnresolvedMetricItem, ...]
    blocked_observations: tuple[BlockedObservation, ...]
    deduplicated_observation_count: int
```

说明：

- `InstantiationIssue.code` 是稳定机器码，`message` 是面向人的解释；测试和下游统计
  应依赖 code，不依赖中文文本。
- `BlockedObservation` 包住完整 `ResolvedObservation`，因此不需要重复定义一套
  source / row / evidence 字段。
- `UnresolvedMetricItem` 是运行结果投影，不是审核任务；不包含 `review_status`、
  `confirmed_metric_id`、审核人、审核时间或人工操作。
- `candidate_metric_ids` 和 `source_resolution_id` 只是已有结果的轻量引用。完整候选、
  证据和 Proposal 继续在 `MetricResolutionResult` 中，不在清单里复制。
- `deduplicated_observation_count` 让完全相同的重复输入不会被静默忽略；各输入的详细
  provenance 仍在原 `DataMappingResult` 中。
- `OntologyCatalog.unit_storage_semantics` 与 `observation_schema` 并列，最小形态可为
  `Mapping[str, str]`（当前只需 `{"PERCENT": "0_to_1"}`）；它不属于
  `ObservationSchema`，不参与该 schema 的 fingerprint。
- `DataMappingResult` 保持不变。调用方需要完整链路结果时同时持有原 Mapping 结果和
  `OntologyInstantiationResult`，本阶段不再增加全知顶层 wrapper。

---

## 6. Instantiation Gate

### 6.1 运行级前置条件

以下问题不是单条业务数据缺陷，应在遍历记录前 fail fast，避免基于不一致快照生成
一半可信、一半不可信的结果：

1. 批次必须非空；
2. 每个 `mapping_result.metric_resolution_result.ontology_revision` 必须等于
   `catalog.ontology_revision`；
3. 每个 `mapping_result.structuring_result.observation_schema_fingerprint` 必须等于
   `catalog.observation_schema.fingerprint`；
4. 调用方传入的 `status` 必须是当前 `ObservationSchema.status_values` 中的值；
5. 每个 Mapping 结果内部 Draft、Effective Resolution 与 ResolvedObservation 的现有
   关联不变量必须成立。

这些条件失败时抛出清晰的 `OntologyInstantiationError`，不把同一个配置错误复制成
每条 `BlockedObservation`。

### 6.2 单条门禁

单条门禁收集全部可判定原因，不采用“遇到第一项就返回”，方便一次看清问题；只有
原因集合为空时才是 `PASS`。

| 检查项 | PASS 条件 | BLOCKED code |
| --- | --- | --- |
| Effective Metric | `effective_status == MAPPED` 且 `metric_id` 非空 | `METRIC_ID_MISSING` / `METRIC_STATUS_NOT_MAPPED` |
| Metric 引用 | `catalog.metric_by_id(metric_id)` 存在 | `METRIC_REFERENCE_NOT_FOUND` |
| Organization | `organization_id` 非空且属于 `catalog.organization_ids` | `ORGANIZATION_ID_MISSING` / `ORGANIZATION_REFERENCE_NOT_FOUND` |
| business scope | 是去除首尾空白后非空的 string | `BUSINESS_SCOPE_INVALID` |
| period type | 属于 `period_type_values` | `PERIOD_TYPE_INVALID` |
| period key | 与 type 对应格式精确匹配 | `PERIOD_KEY_INVALID` |
| period basis | 属于 `period_basis_values` | `PERIOD_BASIS_INVALID` |
| actual value | `int / float`，排除 bool，float 必须 finite | `ACTUAL_VALUE_INVALID` |
| percent semantics | 根据 `catalog.unit_storage_semantics["PERCENT"] == "0_to_1"` 校验值处于 `[0, 1]` | `PERCENT_VALUE_OUT_OF_RANGE` |
| unit | `unit_normalized` 非空且属于当前 Unit enum | `UNIT_MISSING` / `UNIT_INVALID` |
| source | `ObservationDraft.sheet_name` 是非空 string | `SOURCE_INVALID` |

`status` 已在运行级校验，构造前仍应作为断言检查，确保最终对象没有绕过值域。
Catalog 加载器必须从当前 Definition 读取所需的 Unit 存储语义；若该声明缺失或是未知
值，应作为 Catalog / Instantiation 配置错误 fail fast，而不是修改 schema fingerprint
或静默跳过校验。

### 6.3 不增加的伪校验

当前 Definition 没有下列约束，不能自行发明：

- `business_scope` 全局白名单；
- `period_basis` 与某种 Metric 的额外组合限制；
- `actual_value` 一般正负范围；
- Metric 或 Organization 必须为 `ACTIVE`；
- source 文件必须在某个外部存储中存在；
- 必须经过 LLM 或人工审核。

当前工作树 Knowledge 中所有 330 个 Metric 与 5 个 Organization 都是 `DRAFT`，而
现有 ID Binding 将它们作为当前 Catalog 中的有效引用。新门禁延续已验证的“当前
Catalog 中存在即引用有效”语义；本阶段若新增 `ACTIVE-only` 规则会让当前数据全部
阻塞，而且 `OntologyOrganization` 当前也没有投影 status。引用生命周期策略如需
改变，必须作为单独设计显式发生。

### 6.4 批次唯一性门禁

通过单条校验后，先计算业务身份 ID，再按 ID 分组：

```text
count(id) == 1
→ PASS

count(id) > 1 且事实 payload（actual_value、unit）完全相同
→ 合并为一个 ActualObservation，并累计 deduplicated_observation_count

count(id) > 1 且事实 payload（actual_value、unit）存在差异
→ 该组全部 BLOCKED: CONFLICTING_BUSINESS_IDENTITY
```

`source` 不参与事实 payload 比较；仅 source 不同不会构成冲突。重复组按规范化表名、
`source_file`、`source_row`、`observation_draft_id` 稳定排序，选择第一条作为正式对象的
确定性代表，其规范化表名写入单值 `ActualObservation.source`；所有来源的完整
provenance 仍留在原 `DataMappingResult`。这是当前单值 source 契约下的最小策略，不
新增多来源 DTO。冲突组不静默保留第一条，也不按 `actual_value` 选胜者。reason 的
message 应至少说明冲突数量，并可在 details 后续需要时加入冲突
`observation_draft_id`；V1.0.1 不因此增加审核流程。

---

## 7. `ActualObservation` 构造

字段投影如下：

| `ActualObservation` 字段 | 来源 | 处理 |
| --- | --- | --- |
| `id` | 通过门禁的业务身份 | 规范 JSON + SHA-256 |
| `organization_id` | `ResolvedObservation.organization_id` | 直接取值；门禁保证 Catalog 中存在 |
| `metric_id` | `ResolvedObservation.metric_id` | 直接取值；门禁保证有效且状态为 MAPPED |
| `business_scope` | `observation.business_scope` | NFKC、trim、折叠连续空白后写入 |
| `source` | `observation.sheet_name` | NFKC、trim、折叠连续空白后写入；暂时只存表名 |
| `period` | Draft 的三个 period 字段 | 构造正式 `Period` struct |
| `actual_value` | `observation.actual_value` | 不做猜测或字符串强转 |
| `unit` | `observation.unit_normalized` | 只写 enum key，不写 `unit_raw` |
| `status` | `instantiate_observations(..., status=...)` | 调用方显式提供并通过 Status 值域校验 |

概念代码：

```python
period = Period(
    period_type=draft.period_type,
    period_key=draft.period_key,
    period_basis=draft.period_basis,
)

actual = ActualObservation(
    id=actual_observation_id(identity),
    organization_id=resolved.organization_id,
    metric_id=resolved.metric_id,
    business_scope=normalize_identity_text(draft.business_scope),
    source=normalize_identity_text(draft.sheet_name),
    period=period,
    actual_value=draft.actual_value,
    unit=draft.unit_normalized,
    status=status,
)
```

`source_file / source_row / metric_subject_id / raw_dataset_id / curated_id / evidence`
以及原始 `sheet_name` 不作为独立追踪字段写入 `ActualObservation`；仅规范化后的表名写入
其 `source` 属性，完整原值仍可通过 Mapping 结果追溯。

---

## 8. `ActualObservation.id`

### 8.1 业务身份字段

推荐身份载荷：

```json
{
  "organization_id": "org.level1_subsidiary_a",
  "metric_id": "qc.net_profit",
  "business_scope": "公司整体",
  "period": {
    "period_type": "MONTH",
    "period_key": "2025-01",
    "period_basis": "YEAR_TO_DATE"
  }
}
```

字段判断：

| 字段 | 是否参与 | 理由 |
| --- | --- | --- |
| `organization_id` | 是 | 同一指标在不同组织是不同业务观测 |
| `metric_id` | 是 | 正式 Metric 决定观测对象 |
| `business_scope` | 是 | 公司整体、发电成本、购电成本等不能互相覆盖 |
| `period` 三字段 | 是 | 期间锚点和本期/YTD/期初/期末共同决定时间语义 |
| `source` | 否 | required 表示实例必须携带 provenance，不等于该属性属于业务身份；不同来源的同一事实应能识别为重复或冲突 |
| `unit` | 否 | 单位是同一事实的表达方式；单位冲突应被发现，不应生成新身份 |
| `actual_value` | 否 | 更正值或重复导入不应改变对象身份 |
| `status` | 否 | 生命周期变化不改变业务对象身份 |
| `source_row / column` | 否 | 报表排版变化不应改变业务身份 |
| Draft / Subject / Decision ID | 否 | 属于处理链技术身份，不是本体业务键 |
| `ontology_revision` | 否 | revision 用于验证引用快照，不应让同一业务事实换 ID |

### 8.2 规范化与哈希

1. 身份中的 `business_scope` 仅做确定性的 NFKC、trim 和连续空白折叠，不做同义词
   替换、路径解析或 LLM 归一化；
2. enum 与引用 ID 使用门禁通过后的精确值；
3. 用 UTF-8、`ensure_ascii=False`、key 排序、紧凑分隔符序列化 JSON；
4. 计算完整 SHA-256，并使用明确 namespace：

```text
actual-observation:sha256:<64 lowercase hex>
```

不把规则版本写入身份载荷。规则升级若没有改变规范后的业务身份，同一观测必须保持
同一 ID；若未来业务键需要改变，应先显式升级并迁移，而不是悄悄通过版本字段制造新
对象。

### 8.3 为什么不能靠源行消除当前冲突

当前样例已经证明，即使使用 `organization + metric + scope + period + 表名` 仍可能
冲突，而且加入表名没有改变任何样例分组。因此排除 source 不是放弃一项已验证有效的
区分维度；它使 ID 更准确地表达
`organization + metric + scope + period` 业务身份。这不
是哈希算法问题，而是两条业务事实被当前字段投影成了相同身份。payload 完全相同的
重复输入合并；payload 不同的 V1.0.1 正确行为是阻塞并暴露冲突。

后续如果业务确认两条“利息收入”确实是不同观测，应优先选择以下一种显式修复：

1. 修正错误的 Metric 映射；或
2. 把真实业务维度可靠映射到 `business_scope`；或
3. 经正式本体变更流程扩展观测维度。

本阶段不实施这些修复，也不为绕过冲突修改 Definition。

---

## 9. `status` 规则

检查结果是：

- Definition 只定义 `DRAFT / ACTIVE / INACTIVE` 值域和“当前观测记录状态”的说明；
- Knowledge 当前没有 `ActualObservation` 实例可提供沿用范例；
- 现有 Structuring 冻结设计明确不推断 status；
- 当前代码没有“门禁通过即 ACTIVE”或其他观测状态规则。

因此本阶段采用最小的显式策略：

```text
status 不从源报表、Metric 状态、LLM 或门禁结果自动推导；
由实例化调用方按本次运行目的显式提供；
没有默认值；
必须属于当前 ObservationSchema.status_values。
```

建议语义：

- `DRAFT`：已是结构完整、Definition 合法的正式 `ActualObservation` 对象，但尚未
  进入对外生效/发布状态；当前只生成、不持久化的阶段建议显式使用此值；
- `ACTIVE`：调用方已经明确决定这些实例应作为生效数据使用时才能传入；
- `INACTIVE`：用于既有实例失效语义，不应作为新导入的自动默认值。

“正式实例”描述对象类型与完整性；`DRAFT` 描述该正式实例的生命周期状态，两者不
矛盾。把 `DRAFT` 写死在 dataclass 或构造函数默认值中则缺少可解释的调用方决策，
因此不采用。

---

## 10. BLOCKED 数据

### 10.1 保留方式

门禁失败时不创建 `ActualObservation`，只创建：

```text
BlockedObservation
├─ resolved_observation  # 原对象完整保留
└─ reasons[]             # 一条或多条稳定原因
```

这样既能直接看到问题，也能沿
`ResolvedObservation → ObservationDraft → source_file/sheet/row/evidence` 追溯，
不需要重复复制 provenance。

### 10.2 建议 reason code

```text
METRIC_ID_MISSING
METRIC_STATUS_NOT_MAPPED
METRIC_REFERENCE_NOT_FOUND
ORGANIZATION_ID_MISSING
ORGANIZATION_REFERENCE_NOT_FOUND
BUSINESS_SCOPE_INVALID
PERIOD_TYPE_INVALID
PERIOD_KEY_INVALID
PERIOD_BASIS_INVALID
ACTUAL_VALUE_INVALID
PERCENT_VALUE_OUT_OF_RANGE
UNIT_MISSING
UNIT_INVALID
SOURCE_INVALID
CONFLICTING_BUSINESS_IDENTITY
```

同一记录可同时包含多条原因，例如 Metric 缺失、Organization 缺失和 Unit 缺失。
批次 payload 冲突在单条字段校验完成后追加；完全相同的重复输入不进入 blocked。

### 10.3 与未匹配指标清单的关系

一条 `metric_id = null` 的 Observation 会：

1. 作为 observation-level 数据进入 `BlockedObservation`，原因包含
   `METRIC_ID_MISSING`；
2. 其 MetricSubject 作为 subject-level 项进入 `UnresolvedMetricItem`。

这不是重复错误记录，而是服务两个视角：前者说明哪些观测不能实例化，后者说明哪些
报表指标尚未映射。未匹配主体没有非空值、因而关联 Observation 数为 0 时，也仍可
出现在未匹配指标清单中。

---

## 11. 未匹配指标清单

### 11.1 选择条件

遍历 `mapping_results` 的 `effective_metric_resolutions`，选择：

```text
current_metric_id is None
```

不要只看 Deterministic `MetricDecision.status`，因为人工确认回放后的有效结果可能已
改变；也不要把 Semantic `PROPOSED` 当成已匹配。

`effective_status` 保留现有值：

```text
UNRESOLVED
ONTOLOGY_GAP
```

### 11.2 为什么按 MetricSubject 聚合

`MetricSubject` 表示“来源表某一指标行中待解析的指标语义”，而
`ObservationDraft` 表示该指标行投影出的具体数值。一条 Subject 可以产生：

- 本月数与本年累计数；
- 期初数与期末数；
- 多个 business scope；
- 0 条观测（指标存在但当前值为空）。

Metric Resolution 本来就是按 `metric_subject_id` 做一次决议，再被多个 Draft 复用。
因此未匹配清单按该 ID 聚合正好与 Resolution 的责任粒度一致，避免同一未匹配指标因
不同期间口径或业务范围重复出现。

不能只按 `metric_name` 聚合：当前 `metric_subject_id` 包含 Curated、文件、Sheet、
行列和上下文身份；不同文件、不同表内语境或同名重复行可能不是同一解析问题，按名称
合并会丢失证据并隐藏歧义。

### 11.3 构造算法

```text
1. effective_metric_resolutions 中筛 current_metric_id = null
2. 用 metric_subject_id 连接 row_subjects
3. 用同一 ID 连接 deterministic decision / candidate set / semantic resolution
4. 统计 resolved_observations 中同一 metric_subject_id 的数量
5. 收集关联 Draft 中唯一的 organization context；没有 Draft 时允许为空
6. 每个 metric_subject_id 输出一个 UnresolvedMetricItem
7. 按 source_file、sheet_name、source_row、metric_subject_id 稳定排序
```

`candidate_metric_ids` 优先使用现有 CandidateSet 的排名顺序；没有 Semantic
CandidateSet 时可使用 Deterministic decision 的 candidates。完整评分和证据不复制到
清单，仍通过 `source_metric_decision_id` 回查。

### 11.4 最小输出示例

```json
{
  "metric_subject_id": "metric-subject:...",
  "metric_name": "归属于母公司所有者的综合收益总额",
  "comparison_name": "归属于母公司所有者的综合收益总额",
  "effective_status": "UNRESOLVED",
  "source_metric_decision_id": "metric-decision:...",
  "source_file": "一级子公司A_利润表_2025-01.xlsx",
  "sheet_name": "财务快报-利润表_国家电网有限公司",
  "source_row": 77,
  "organization_value": "一级子公司A",
  "organization_id": "org.level1_subsidiary_a",
  "observation_count": 2,
  "candidate_metric_ids": [],
  "source_resolution_id": null
}
```

---

## 12. 完整调用链

推荐保持 Data Preparation 与 Mapping 入口不变，在调用方显式追加一步：

```python
mapping_results = tuple(
    map_curated_observations(
        curated,
        structuring_request,
        resolution_request,
        catalog,
        judge,
    )
    for curated in curated_datasets
)

instantiation_result = instantiate_observations(
    mapping_results,
    catalog,
    status="DRAFT",  # 调用方显式生命周期决策
)
```

内部顺序：

```text
1. 校验批次非空，并逐个校验 ontology revision、schema fingerprint、内部关联与 status
2. 从 EffectiveMetricResolution 构造 UnresolvedMetricItem[]
3. 对每条 ResolvedObservation 收集单条 gate reasons
4. 对单条 PASS 候选规范化正式字段并计算业务身份 ID
5. 按 ID 合并完全相同的重复输入，并检测批次内 payload 冲突
6. 唯一候选和完全相同的重复组构造 ActualObservation
7. 返回 ActualObservation[] + UnresolvedMetricItem[] + BlockedObservation[]
```

未匹配清单应在 Gate 旁路生成，而不是只从 BlockedObservation 反推，因为它的聚合粒度
是 MetricSubject，并且需要保留 observation count 为 0 的指标主体。

---

## 13. 推荐编码顺序

### I0 — 契约与 Catalog 只读投影

1. 在 `observation_contracts.py` 增加 `Period` 和 `ActualObservation`；
2. 在 `OntologyCatalog` 顶层增加与 `ObservationSchema` 分离的 Unit 存储语义只读
   窄投影；保持现有 schema 内容和 fingerprint 不变；
3. 增加实例化输出的最小 dataclass 和 JSON 序列化测试；
4. 固定 Definition / Knowledge 只读 hash，确认没有资产写入。

停止条件：契约与当前 Definition 精确一致；现有 ObservationSchema fingerprint 在
改动前后完全不变；没有修改上游业务输出。

### I1 — Gate、Period 与构造

1. 实现运行级 revision / schema / status 检查；
2. 实现单条字段门禁并收集多原因；
3. 实现 `Period` 组装和 `ActualObservation` 投影；
4. 覆盖四种 PeriodBasis、三种 PeriodType、Unit、finite number 和 PERCENT 语义。

停止条件：任何 required 字段或引用非法都不会产生残缺实例。

### I2 — 稳定 ID 与批次冲突

1. 实现身份文本的受限规范化和规范 JSON；
2. 实现 SHA-256 ID；
3. 验证同一输入重跑稳定，`actual_value / unit / status / source / source_row` 变化不
   改变 ID；
4. 用包含多个 `DataMappingResult` 的批次验证：跨文件或跨表名但业务身份与事实 payload
   完全相同的输入合并为一个实例；
5. 用当前真实“利息收入”冲突样例验证整组 BLOCKED；
6. 验证不使用行号、Draft ID 或 LLM 输出消除冲突。

停止条件：输出中 `ActualObservation.id` 唯一；相同重复被可见地合并，冲突重复没有被
静默覆盖。

### I3 — 未匹配指标清单与主入口

1. 实现按 `metric_subject_id` 的 unresolved 汇总；
2. 同一 Subject 的多 Observation 只输出一项；
3. 保留 `UNRESOLVED / ONTOLOGY_GAP`、来源、organization context、计数和候选引用；
4. 接入 `instantiate_observations()`，导出公共 API；
5. 更新 CLI 展示，但不增加审核动作或持久化。

停止条件：一次调用稳定得到三类输出，Mapping 结果保持不变。

### I4 — 回归与真实样例验收

1. 运行当前全量测试，确保 Frozen Mapping 语义不漂移；
2. 增加 Definition 形状、门禁、ID、聚合和 JSON round-trip 测试；
3. 使用代表利润表、资产负债表、现金流量表与一个未匹配 synthetic case；
4. 做 production → evaluation 依赖扫描和 Definition / Knowledge / nano diff 自审。

全部停止条件通过后，才可把本阶段的实现状态从“代码实施尚未开始”更新为已完成；
不得在实施中静默改写已冻结设计。

---

## 14. 验收标准

### 14.1 正式实例

- [ ] 每个 `ActualObservation` 精确包含 Definition 的九个 required 字段；
- [ ] Metric 与 Organization 引用存在于同一个当前 Catalog；
- [ ] Period struct、Unit、Status 与 Definition 当前值域一致；
- [ ] `actual_value` 是非 bool 的有限 number；PERCENT 遵守 0～1 存储语义；
- [ ] 任一必需字段失败时不生成残缺实例；
- [ ] `ActualObservation` 不携带 Draft / Resolution / review / persistence 字段。

### 14.2 ID

- [ ] 同一业务身份重复运行得到完全相同 ID；
- [ ] 更换运行时间、LLM 文本、rule version、ontology revision 不直接改变 ID；
- [ ] `actual_value`、unit 表达、status、source、source row 不参与 ID；
- [ ] Organization、Metric、scope 或 Period 改变时 ID 改变；source 单独改变时 ID 不变；
- [ ] 同身份、同 payload 的重复输入只形成一个实例并计入 deduplicated count；
- [ ] 当前真实样例中的业务身份冲突被 BLOCKED，不通过技术字段伪造唯一性；
- [ ] `ActualObservation[]` 内没有重复 ID。

### 14.3 Gate 与 blocked reason

- [ ] Gate 能同时返回一条记录的全部已知原因；
- [ ] reason code 稳定，中文 message 清楚；
- [ ] Blocked 记录完整保留原 `ResolvedObservation` 与 provenance；
- [ ] revision、schema、status 等运行级不一致 fail fast；
- [ ] Metric / Organization 当前只按 Catalog membership 判断引用有效，不暗加
  `ACTIVE-only` 规则。

### 14.4 未匹配指标清单

- [ ] 只选择 `current_metric_id is None`；
- [ ] Semantic `PROPOSED` 仍进入未匹配清单；
- [ ] 人工确认回放后已成为有效 Metric 的 Subject 不再进入清单；
- [ ] 同一 MetricSubject 的本期/YTD、多 scope 观测只形成一项；
- [ ] 不同 MetricSubject 即使名称相同也不被错误合并；
- [ ] `observation_count = 0` 的未匹配指标主体仍能保留；
- [ ] 清单不包含 review、审核人、审核时间或回流字段。

### 14.5 边界与回归

- [ ] `DataMappingResult`、`ResolvedObservation` 和 Frozen Metric Resolution 语义不变；
- [ ] Definition / Knowledge 与 `references/nano-ontoprompt-master` 未被修改；
- [ ] 不写 Neo4j 或其他持久化；
- [ ] Production 不依赖 Evaluation；
- [ ] 全量测试、compile、真实样例与 JSON 序列化通过；
- [ ] 没有引入本阶段非目标的抽象或流程。

---

## 15. 已发现的现有设计问题与处理结论

### 15.1 正式业务键在部分真实数据上仍不充分

这是本次检查发现的实际问题，不是推测。当前相同表名中存在两条
`qc.interest_income`，正式候选身份字段完全相同而值不同。V1.0.1 不修改冻结的 Metric
Resolution 或 Definition，只通过 `CONFLICTING_BUSINESS_IDENTITY` 安全阻塞。

### 15.2 Unit 窄投影遗漏 PERCENT 存储语义

Definition 已声明 `PERCENT.storage_semantics = 0_to_1`，当前
`ObservationSchema` 没有携带。下一阶段只在 `OntologyCatalog` 顶层增加供
Instantiation 使用的最小只读投影；不修改 `ObservationSchema` 的结构、语义或
fingerprint，也不建立通用验证框架。

### 15.3 status 没有业务推导规则

不能从 required 字段倒推出 `DRAFT` 或 `ACTIVE`。V1.0.1 用调用方显式参数解决，并建议
当前只生成、不发布的运行显式传 `DRAFT`。未来若引入发布/失效动作，再单独设计状态
迁移规则。

---

## 16. 设计冻结建议

进入代码实施前，已明确审核并冻结以下五点：

1. `ActualObservation.source` 暂时只存表名，文件名与行列保留在 provenance；
2. 当前业务身份不包含 source、unit、actual value、status 或技术追踪字段，只由
   Organization、Metric、business scope 与 Period 构成；
3. 单次实例化接收一个非空 `DataMappingResult` 序列；跨结果同身份且
   `actual_value + unit` 相同的输入确定性合并（source 不参与比较），同身份但两者任一
   冲突的输入整组 BLOCKED；
4. `status` 是必传调用参数且无默认值，当前示例显式使用 `DRAFT`；
5. 当前 Metric / Organization 引用有效性只要求存在于本次 Catalog，不要求引用对象
   为 `ACTIVE`。

这五点决定正式实例的身份、生命周期与可实例化范围，代码实现不应在未审核时静默改写。
