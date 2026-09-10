# Phase 2.5 — 未决 Metric 语义解析与本体补全建议

**阶段状态：设计已审核冻结；P0 → P3 实现、单一 DeepSeek LLM Pilot 与全量回归验收已完成。**

本文档承接已经冻结的 Phase 1 / Phase 2。它定义 Phase 2.5 的目标、边界、
最小契约、分阶段实施顺序和验收原则，但不授权或实现 P0 业务代码、LLM、
embedding、CALCULATION 扩展或正式本体变更。

事实基线以仓库当前版本为准：

- `docs/ARCHITECTURE.md`：总体分层与本体治理原则；
- `docs/CURRENT_PHASE.md`：Phase 2 冻结实现与验收基线；
- `docs/NANO_REUSE_ANALYSIS.md`：nano-ontoprompt 真实代码分析；
- 当前 `MetricDecision`、`MappingPlan`、`OntologyCatalog` 进程内契约；
- 当前只读 `ontology/Definition.json` 与 `ontology/Knowledge.json`。

---

## 1. 问题与阶段目标

Phase 2 已经通过确定性证据完成：

```text
Curated Dataset
→ TableMappingPlan
→ Row / Metric Subject Extraction
→ Exact / Alias / 受限规范化匹配
→ MetricDecision
→ ObservationCandidate
```

它在不能可靠判断时保留 `UNMATCHED / AMBIGUOUS`。这不是失败，而是阻止
低证据映射污染正式业务语义的必要边界。

Phase 2.5 只解决：

> 对 Phase 2 无法可靠映射的 Metric 主体，结合当前可用报表上下文和同一
> `ontology_revision` 的本体语义，判断是否存在表达同一业务概念的已有
> Metric；若当前本体没有可靠等价概念，则保留该语义结论，并在证据适当时
> 形成可追踪、不可自动执行的本体补全建议。

核心判断是：

```text
是否为同一个业务指标
```

而不是：

```text
哪个指标最相关、名字最像或属于同一业务主题
```

Phase 2.5 不以提高自动匹配率为目标。长期目标是把经过确认的知识沉淀为
正式名称、alias 或新 Metric，使后续输入尽可能回到 Phase 2 的确定性路径，
而不是永久依赖语义模型。

---

## 2. 冻结边界与派生关系

Phase 2 的 `MetricDecision`、`ObservationCandidate` 和既有状态保持不变。
Phase 2.5 不回写、不覆盖、不重新解释原始 Phase 2 产物，而是通过引用建立派生
结果：

```text
Frozen Phase 2 MetricDecision
        ↓
Eligibility Gate
        ↓
Semantic Context Builder
        ↓
MetricCandidateSet
        ↓
Semantic Judgment
        ↓
SemanticResolution
        ↓
Review
        ├─ Effective Mapping
        └─ OntologyChangeProposal
```

任何 Phase 2.5 产物至少绑定：

- `source_metric_decision_id`；
- Phase 2 Mapping / Curated / Raw 来源；
- 与 Phase 2 一致的 `ontology_revision`；
- Phase 2.5 规则、召回器、Judge 或人工确认版本。

输入 `MetricDecision.ontology_revision` 与当前 `OntologyCatalog` 不一致时不得
静默跨版本解析。若在执行前发现不一致，应停止该项执行并记录：

```text
execution_status = SKIPPED
semantic_status = null
reason = ONTOLOGY_REVISION_MISMATCH
```

---

## 3. Eligibility Gate

默认进入 Phase 2.5 的对象：

```text
row_role = METRIC
AND
MetricDecision.status IN {UNMATCHED, AMBIGUOUS}
```

默认跳过：

- Phase 2 已经 `MATCHED` 的主体；
- 已经由显式确认形成 `ONTOLOGY_GAP` 的主体；
- `GROUP / NOTE`；
- `UNKNOWN`。

`UNKNOWN` 只有在显式请求或新增、可追踪的上下文足以确认其确为指标主体时，
才能进入 Phase 2.5。Eligibility 不重新建设 row role 分类器，也不修改 Phase 2
的角色结论。

被跳过不等于业务否定，应记录 `SKIPPED` 及原因。

---

## 4. Semantic Context

Context Builder 只组合 Phase 1 / Phase 2 已经提供或可由其稳定派生的信息：

- `raw_label`、`comparison_name`、`row_role` 和主体提取 evidence；
- `source_file`、`sheet_name`、`source_row`、原始列位置；
- Phase 2 已记录的最近 `GROUP`；
- 有序 `row_subjects` 中有限窗口内的前序、后续主体；
- 同表其他独立 Metric 主体；
- 已绑定的 `business_scope`、单位和可用 `value_semantics`；
- Mapping / Curated / Raw 标识与规则版本。

第一版不建立完整报表层级树，不推断不存在的 `parent_metric_id`，不把行序邻近
直接解释为父子、组成或计算关系。实际数值默认不进入 Metric 身份判断；若未来
确需使用，必须单独论证其必要性和数据外发边界。

同表存在两个独立业务项目时，可记录为强负证据：它们通常不是 alias 或同一
Metric。但该证据不是绝对规则，不能在存在明确重复展示、简称与正式名共现等
反证时机械否决。

---

## 5. Candidate Retrieval

Candidate Retrieval 只负责：

> 从当前 `OntologyCatalog` 中召回值得进一步判断的已有 Metric，并给出稳定、
> 可解释的排序证据。

它不得产生 `MAP_EXISTING`，也不得使用如下捷径：

```text
Top-1 + similarity threshold → MAP_EXISTING
```

第一版 Pilot 优先使用小规模、纯内存、无新增基础设施的组合召回：

- `name_cn` 字符 n-gram / 字符序列相似度；
- alias 命中和相似度；
- `definition_cn` 的低权重重叠；
- `business_labels` 的低权重重叠；
- 基于最近 GROUP、前后主体的上下文查询变体。

每个候选应分别记录各路分数、命中字段和召回证据，不能只保留一个不可解释的
总分。Top-K 是运行参数和评测对象，不是冻结业务契约。

当前只有约 161 个 Metric，P1 优先使用标准库或小型内存算法。是否引入额外
fuzzy 库或 embedding，只能由人工 Gold Set 的 `Recall@K` 结果决定。候选召回
不足只能导致 `AMBIGUOUS` 或执行报告，不能证明 `NO_EQUIVALENT`。

---

## 6. Semantic Judgment

Semantic Judgment 判断业务等价，而不是相关度。以下关系即使名称相似也默认
不能直接视为同一个 Metric：

```text
父指标 ↔ 子指标
汇总项 ↔ 明细项
总额 ↔ 组成项
一般口径 ↔ 特定业务口径
```

典型困难负样本包括：

```text
主营业务利润 ≠ 营业利润
主营业务税金及附加 ≠ 税金及附加
农网还贷资金返还收入 ≠ 农网还贷资金(一省多贷)
归属于母公司所有者的净利润 ≠ 净利润
```

这些只作为待人工确认的测试案例，不得 hardcode 为生产特判。

Judge 必须允许拒绝全部 Candidate。若选择已有 Metric，`selected_metric_id`
必须属于实际提供的 `MetricCandidateSet`，并属于相同 `ontology_revision`；越权
或虚构 ID 属于技术执行失败，不是业务结论。

---

## 7. 三层状态

机器是否执行成功、机器作出了什么业务判断、该判断是否正式确认必须分开。

### 7.1 技术执行状态

```text
SUCCEEDED | FAILED | SKIPPED
```

Provider 不可用、timeout、输出格式错误、候选越权，或执行过程中发生的异常版本
问题属于 `FAILED`。此时：

```text
semantic_status = null
```

技术失败不得伪装成 `NO_EQUIVALENT` 或 `AMBIGUOUS`。

若在执行前发现输入 MetricDecision 与当前 OntologyCatalog 的 revision 不一致，
则属于输入不适用于当前本体快照，应使用：

```text
execution_status = SKIPPED
semantic_status = null
reason = ONTOLOGY_REVISION_MISMATCH
```

这与运行过程中发生异常版本问题的 `FAILED` 相互区分。

### 7.2 业务语义状态

```text
MAP_EXISTING | NO_EQUIVALENT | AMBIGUOUS | null
```

- `MAP_EXISTING`：证据支持与 CandidateSet 中一个已有 Metric 为同一概念；
- `NO_EQUIVALENT`：语义判断认为当前 revision 没有可靠等价 Metric；
- `AMBIGUOUS`：多个候选合理、上下文不足、证据冲突，或召回不足以支持结论。

Top-K 未召回正确结果不能推出 `NO_EQUIVALENT`。机器可以提出
`NO_EQUIVALENT + PROPOSED`，但未经确认不能成为正式本体缺失事实。

### 7.3 人工/正式确认状态

```text
PROPOSED | CONFIRMED | REJECTED | null
```

所有机器结论默认是 `PROPOSED`。第一版不建设独立 Review 数据库或工作流；
确认状态作为纯数据契约字段或显式确认输入即可。只有 `CONFIRMED` 结果可以进入
effective view，或授权正式本体变更。

首版冻结以下状态不变量：

- `execution_status IN {FAILED, SKIPPED}` 时，`semantic_status`、`review_status`
  和 `selected_metric_id` 均为 `null`；
- `execution_status = SUCCEEDED` 时，`semantic_status` 不得为 `null`，且必须
  绑定实际用于判断的 `candidate_set_id`；
- `semantic_status = MAP_EXISTING` 时，`selected_metric_id` 必填，并且必须属于
  对应 CandidateSet 和同一 `ontology_revision`；
- `semantic_status IN {NO_EQUIVALENT, AMBIGUOUS}` 时，`selected_metric_id` 必须
  为 `null`；
- 只有实际产生机器语义结论时，`review_status` 才默认取 `PROPOSED`。

---

## 8. 最小业务契约

第一版优先保持三个公开产物，不把内部每一步都建设成平台对象。

### 8.1 MetricCandidateSet

至少包含：

- 稳定 `candidate_set_id`；
- `source_metric_decision_id`、`ontology_revision`；
- 使用的 Semantic Context 快照或摘要；
- `retrieval_version` 和 Top-K 参数；
- 有序 Metric 候选快照；
- 每项的分路召回分数和 evidence。

### 8.2 SemanticResolution

至少包含：

- 唯一、可追踪的 `resolution_id`，不要求同一 CandidateSet 多次 Judge 产生相同 ID；
- `source_metric_decision_id`、`candidate_set_id`；后者仅在执行未进入语义判断时
  可以为 `null`；
- `execution_status`、失败阶段与可诊断错误；
- `semantic_status`、`review_status`；
- 可选 `selected_metric_id`；
- supporting / counter evidence；
- Judge 类型、规则 / Prompt / 模型版本；
- `ontology_revision`。

不额外建设独立 Review 实体。若后续真实治理证明需要多人审批，再进入独立阶段。

### 8.3 OntologyChangeProposal

它是与 Resolution 分离的本体治理建议，不是正式本体变更。至少包含：

- 稳定 `proposal_id` 和 `proposal_kind`；
- 来源 Resolution / MetricDecision 与 `ontology_revision`；
- source file / sheet / row；
- `raw_label`、`comparison_name` 和可用上下文；
- `ADD_METRIC` 的 `suggested_name_cn`，以及有充分证据时可选的
  `suggested_definition_cn`、`suggested_value_semantics`；
- `ADD_ALIAS` 的 `target_metric_id` 与 `suggested_alias`；目标必须存在于同一
  `ontology_revision`；
- 相关但不等价的已有 Metric；
- reason、evidence 和 `review_status`。

首版 proposal kind 只考虑：

```text
ADD_METRIC | ADD_ALIAS
```

`ADD_ALIAS` 只适用于名称脱离当前报表上下文后，仍能稳定表达目标 Metric 的情况。
如果某个表达必须依赖当前 GROUP、前后指标、特定报表位置或层级上下文才能确定
含义，就不得将它沉淀为目标 Metric 的全局 alias。例如，“其他”在主营业务收入
扣减上下文中可能被判断为“主营业务收入其他项”，但不能因此把“其他”加入该
Metric 的 aliases。该例只解释设计原则，不 hardcode 为生产规则。

Proposal 的生成和生效遵守以下不变量：

- `ADD_ALIAS` 只能来源于 `MAP_EXISTING` Resolution，且 `target_metric_id` 必须
  等于该 Resolution 的 `selected_metric_id`；
- `ADD_METRIC` 只能来源于 `NO_EQUIVALENT` Resolution；
- `AMBIGUOUS`、`FAILED` 或 `SKIPPED` 不生成 Proposal；
- `review_status = PROPOSED` 的 Resolution 可以生成 draft Proposal 供审核，但
  不表示其语义结论已经正式确认；
- 正式执行本体变更必须同时满足来源 Resolution 和对应 Proposal 均为
  `CONFIRMED`，并由 Phase 2.5 之外的显式本体治理动作完成。

可以在证据充分时生成 draft Proposal，但不得自动执行。可选 definition / value
semantics 只是待审核建议，不因字段存在而获得正式效力。首版不要求完整 Metric
定义，不自动生成 CALCULATION，不修改 Definition / Knowledge。

---

## 9. Effective Mapping

Effective Mapping 是派生视图，不回写 Phase 2：

```text
Phase 2 MATCHED
→ 直接保留 Phase 2 结果

Phase 2 unresolved + Phase 2.5 MAP_EXISTING + CONFIRMED
→ 派生已有 Metric 映射

其他 Phase 2 unresolved 情况
→ 仍保持未决，不伪造 metric_id
```

`NO_EQUIVALENT + CONFIRMED` 可以进入本体治理，但仍不等于已经更新正式本体。
正式本体变化后必须形成新的 `ontology_revision`，并重新运行 Phase 2；不能把旧
revision 的 Proposal 当成新 revision 下已经存在的 Metric。

---

## 10. Gold Set 与评测原则

Gold Set 必须独立于被评测的 Candidate Retrieval 和 Semantic Judge。默认由独立
人工确认，当前人工讨论或机器建议只能作为待审核草案，不能直接声明为真值。

首版 Gold 的确认存在一项项目所有者显式授权的治理例外：允许 Codex 在 P1/P2
算法实现前，基于模拟报表、完整 OntologyCatalog 和来源上下文进行独立审核。
该例外必须记录 reviewer 类型、时间、逐项依据和授权来源，不得把其他 AI 标注
直接当真，也不推广为程序、规则或生产 LLM 自动制造 Gold 的能力。

每个案例至少记录：

- 稳定 `case_id`；
- `source_metric_decision_id`、`ontology_revision`；
- 允许用于判断的上下文快照；
- 期望语义状态；
- `MAP_EXISTING` 时的期望 Metric ID；
- `AMBIGUOUS` 时允许的候选范围；
- 困难负样本对；
- review 状态、审核依据和必要说明。

利润表可作为开发集；资产负债表、现金流量表和成本费用表各选择少量案例作为
跨表保留集。应按报表族隔离开发与保留案例，避免随机拆分相邻行造成上下文泄漏。

P0 / P1 至少报告：

- `Recall@3`、`Recall@5`；
- 已确认困难负样本的误匹配情况；
- CandidateSet 稳定重跑；
- ontology revision 隔离；
- eligible / skipped / failed 数量与原因。

召回阶段不使用“最终自动匹配率”作为目标。Gold Set 规模和质量尚未确定前，不
冻结数值阈值；但冻结 Phase 2.5 前，已确认困难负样本不得出现错误
`MAP_EXISTING`。

---

## 11. P0 → P3 实施顺序

### P0 — 独立确认真值与评测基线

1. 从冻结 Phase 2 结果导出 eligible 未决 Metric；
2. 生成带必要上下文、但不带算法答案的审核草案；
3. 定义 revision-aware Gold Set 格式和人工确认规则；
4. 建立只读评测入口；
5. 利润表作为开发集，其他报表族补少量保留案例；
6. 不实现真实 LLM、embedding 或 CALCULATION。

P0 的目标是获得可信真值和可重复评测，不是开始自动语义 Mapping。

### P1 — Candidate Retrieval

实现最小 Context Builder 和纯内存 Candidate Retrieval。先用 Gold Set 验证正确
已有 Metric 是否进入合理 Top-K，再决定是否需要新增依赖或 embedding。

### P2 — Semantic Judge Pilot

先使用人工 oracle 和 mock judge 验证：

- 状态契约；
- Candidate 白名单；
- supporting / counter evidence；
- JSON / schema 错误；
- timeout / unavailable；
- 执行前 revision mismatch 的跳过，以及执行中版本异常和候选越权；
- 技术失败不产生业务状态。

契约稳定后才评估单一真实 LLM 接入。本阶段不建设多 Provider、Prompt 管理或
模型调度平台。

### P3 — 端到端与本体补全建议

用真实报表证明：已有等价 Metric 能找到；相关但不同的能拒绝；本体无等价概念
能形成待确认结论；证据不足保持 `AMBIGUOUS`；确认后的结果才进入 effective
view；draft Proposal 可生成但不执行。

---

## 12. CALCULATION、embedding 与 LLM 的进入条件

P0 / P1 不把 CALCULATION 作为前置条件。若 Gold Set 证明一跳关系能够显著改善
召回或等价判断，后续才考虑由只读 loader 向 `OntologyCatalog` 添加最小、附加的
Calculation 语义；不建设图查询层，不做公式推理。

embedding 只有在轻量召回对已确认等价案例的 Recall@K 明显不足、且字面与定义
方案无法弥补时才评估。当前规模不需要向量数据库。

真实 LLM 只有在 P0 真值可用、P1 CandidateSet 契约和失败边界稳定、mock / oracle
测试通过后才进入。LLM 自报 confidence 仅供参考，不能单独成为自动确认阈值。

---

## 13. nano-ontoprompt 借鉴边界

### ADAPT / REFERENCE

- Mapping 建议作为独立显式产物；
- draft / confirmed 等治理状态与执行状态分离；
- LLM 结构化结果、理由和测试替身；
- 建议、审核、生效分阶段；
- 稳定 ID 和可重放测试思路。

### IGNORE

- 根据数据集名称或 LLM 发明实体类、属性或 Metric ID；
- LLM 失败后把全部列机械映射为同名属性；
- Mapping 后自动创建概念、实例、关系、Logic 或 Action；
- 自动写 SQL、Neo4j、ChromaDB；
- 技术失败后用规则强行给出业务结论；
- 为本阶段复制 nano 的数据库模型、HTTP 路由或多模型配置系统。

当前没有 nano 模块可以直接 `REUSE`。Phase 2.5 只借鉴建议/审核/生效分离和
测试思路，核心等价判断必须建立在本项目当前 `OntologyCatalog` 与冻结 Phase 2
产物之上。

---

## 14. 当前不做

- 程序、规则或生产 Semantic Judge 自动确认 Gold Truth；
- 自动修改、发布或审批 Definition / Knowledge；
- Neo4j、SQL、向量数据库或其他持久化；
- Organization 主数据治理；
- ActualObservation 正式实例化；
- BudgetTarget Mapping；
- 新表形、完整报表层级树或 CALCULATION 公式推理；
- 人工审核 Web UI、权限或多级审批平台；
- 多模型 Provider / Prompt 平台；
- 异常检测、根因分析和前端；
- 为未来规模预建 repository、plugin 或分布式任务框架。

---

## 15. 实施验收与停止条件

本设计已经冻结；Phase 2.5 实施只有在代码和测试能够证明以下事实后才能停止并
冻结实施结果：

1. Phase 2 原始结果未被覆盖，Phase 1 / Phase 2 全量回归不变；
2. Eligibility、Context、Candidate Retrieval、Judgment、Review 边界清楚；
3. 正确已有 Metric 能进入 CandidateSet；
4. 相近但不同的 Metric 不会被强行映射；
5. Top-K 漏召回不会被解释为 `NO_EQUIVALENT`；
6. 本体无等价概念时可以提出待确认结论；
7. 证据不足时保持 `AMBIGUOUS`；
8. 执行前 revision mismatch 产生 `SKIPPED + semantic_status = null`，技术失败
   产生 `FAILED + semantic_status = null`；
9. 机器建议与正式确认分离，只有确认结果进入 effective view；
10. Proposal 可追溯且不会自动修改本体；
11. 全链路绑定 Phase 2 MetricDecision、context、CandidateSet、evidence 和
    `ontology_revision`；
12. Definition / Knowledge 与 nano 参考目录保持只读。

达到以上条件即停止 Phase 2.5 实施。不得为了 100% 自动 Mapping、自动创建全部
Metric、适配任意企业报表或建设完整本体治理平台扩大范围。

---

## 16. 实施证据形成的决议与剩余项

P0 / P1 已形成以下可复核决议：

1. 首版 Gold 采用项目所有者显式授权的独立 Codex 审核，并完整记录 provenance；
2. 只纳入 12 个高置信代表案例，不给其余案例强行制造语义真值；
3. 利润表为开发集，其他三个报表族各保留少量 holdout；
4. 纯内存字符序列、n-gram、alias、定义、业务标签和低权重 GROUP 上下文已满足
   首版 Gold 的 `Recall@3 / Recall@5 = 1.0`，不进入 embedding；
5. `NO_EQUIVALENT` 必须提供 reason 和 supporting / counter evidence，不能由召回
   分数直接产生；
6. P2 已冻结单一 `SemanticJudge` 结构化调用边界及失败状态；
7. 当前证据不满足引入 CALCULATION 或 embedding 的条件。

单一真实 LLM Pilot 已选择 DeepSeek OpenAI-compatible Chat Completions，模型为
`deepseek-v4-flash`。适配器、结构化 JSON 校验、超时/不可用边界和 Gold 只读
Pilot 入口已经实现并通过真实请求验证。该接入不改变上述契约，未纳入首版 Gold
的案例也不因此获得正式语义结论。

---

## 17. P0 实施记录

P0 已实现只读导出：从同一运行中的冻结 Phase 2 `MappingPlan` 选择 eligible
`MetricDecision`，输出 revision-aware 的审核草案。草案只包含来源、上下文和
空白结论字段，不运行相似度召回，不预填 LLM / 算法答案，不修改本体。

首版 Gold 确认并通过只读门禁后才开始 Candidate Retrieval；实施顺序符合本设计。

---

## 18. 单一 DeepSeek LLM Pilot

首版只实现一个 Provider 适配器，不建设模型注册、Prompt 管理或调度平台。本地
配置文件为项目根目录下已由 Git 忽略的 `.env`：

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=<仅在本机填写，不提交>
```

只读 Pilot 入口：

```text
python a.py --phase25-deepseek-pilot tests/fixtures/phase25/phase25_p0_gold_truth.json
```

外发内容只包含指标主体、有限报表上下文、业务范围/期间/单位语义和 Top-K 本体
候选快照；不发送实际数值、源文件路径、Curated / Raw / Mapping 内部标识或密钥。
Provider 返回值必须通过既有 Candidate 白名单和状态契约校验。所有成功结果仍为
`PROPOSED`，Pilot 不回填 Gold、不自动确认、不生成正式映射，也不修改本体。

2026-09-10 的真实 Pilot 结果：12/12 案例执行成功，3 个 `MAP_EXISTING` 的
Metric 选择全部正确，困难负样本错误映射为 0。语义状态与 Gold 精确一致 9/12；
其余 3 项均为 `NO_EQUIVALENT → AMBIGUOUS` 的保守弃权，没有非保守错误。该结果
按长期正确性原则通过安全门禁：不为了提高精确一致率削弱“Top-K 未召回不能证明
本体缺失”的规则，也不把任何 LLM 结果自动确认。
