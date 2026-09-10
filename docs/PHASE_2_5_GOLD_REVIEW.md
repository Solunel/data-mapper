# Phase 2.5 P0 — Gold Truth 说明

当前状态：**Gold Truth v2 已确认，P1 准入门禁通过。**

Gold 文件位于：

```text
tests/fixtures/phase25/phase25_p0_gold_truth.json
```

来源是 `reports/` 下四份代表性模拟报表：利润表作为开发集，资产负债表、现金
流量表和成本费用表作为跨表保留集。P0 仍只从冻结 Phase 2 的 eligible 未决
Metric 导出来源、上下文和空白审核字段；不生成 Candidate、不计算相似度，也不
预填语义答案。

## 1. 本次确认方式

冻结设计默认要求 Gold 独立于算法和 LLM。2026-09-10，项目所有者明确授权
Codex 基于模拟报表和当前 OntologyCatalog 独立审核并生成首版 Gold Truth。
这是一次显式、可追溯的项目治理决定，不是允许生产程序或 Semantic Judge 自动
制造 Gold。

为降低模型标注风险，本次没有把外部 AI 审核 JSON 当作事实，也没有把 172 个
案例全部强行赋予语义状态：

- 只纳入 12 个高置信代表案例；
- 其余 160 个案例标记为已审查但不纳入首版 Gold，不声明语义真值；
- 所有纳入项记录 reviewer、时间、逐项依据和当前 `ontology_revision`；
- Gold 不使用待评测 Candidate Retrieval 或 Semantic Judge 的输出。

同表独立项目证据复审后，Gold 已按当前 `reports/` 原始版本重新导出机器字段。
旧审核结论只按报表族、Sheet、源行和原始标签定位对应语义来源，未沿用旧
`case_id`、fingerprint 或 Mapping ID。表级只读上下文只保存一次，每个 case 通过
稳定 `table_context_id` 引用，运行时再解析；这样既能审计同表证据，也不会在每个
case 中复制整张表。

当前分布：

```text
MAP_EXISTING   2
NO_EQUIVALENT  10
AMBIGUOUS      0
合计           12
```

`AMBIGUOUS` 的运行时契约由 P2/P3 测试覆盖；首版 Gold 不为凑齐状态分布而制造
一个证据不足的真值案例。

## 2. 关键审核判断

- `利息费用 ≠ qc.interest_expense / qc.finance_expenses`：同表第 29 行已有独立
  `△利息支出` 并由 Phase 2 精确映射到 `qc.interest_expense`，当前行则位于
  `财务费用` 明细下；当前 revision 没有足够证据把两个独立报表项目合并，也不能
  把明细直接等同于更宽口径的 `qc.finance_expenses`；
- `资 产 总 计 → qc.total_assets`、`负 债 合 计 → qc.total_liabilities`：差异
  仅为展示空格；
- `汇兑净损失（净收益以“-”号填列） ≠ qc.exchange_gain`：源行以损失为正，
  本体以收益为正，当前链路没有显式取反变换；
- `主营业务利润 ≠ 营业利润`、`主营业务税金及附加 ≠ 税金及附加`：业务范围
  不同；
- 投资/筹资现金流 ≠ 经营活动现金流：活动类型不同；
- 长期待摊费用摊销/无形资产摊销 ≠ 对应资产余额：期间流量与时点存量不同。

## 3. 不得改动的机器字段

顶层 revision、`draft_id`、`mapping_run_ids`、`ontology_metrics`、`table_contexts`，以及每个案例的
`case_id`、`source_fingerprint`、来源、上下文和 Phase 2 标识均用于追溯，不得
手工改写。来源或本体发生变化时必须重新运行 Phase 2 和 P0 导出。

`human_review` 是历史兼容字段名；本次实际 reviewer 类型由顶层
`review_provenance.review_kind` 明确记录。只有：

```text
review_status = CONFIRMED
AND include_in_gold_set = true
```

才属于 Gold Truth。

## 4. 只读校验与 P1 评测

使用项目指定环境校验 Gold：

```powershell
& "D:\Dev_Env\Miniconda3\envs\nano-ontoprompt-py311\python.exe" a.py `
  --phase25-validate-review `
  "tests\fixtures\phase25\phase25_p0_gold_truth.json"
```

只读运行 P1 评测：

```powershell
& "D:\Dev_Env\Miniconda3\envs\nano-ontoprompt-py311\python.exe" a.py `
  --phase25-evaluate-retrieval `
  "tests\fixtures\phase25\phase25_p0_gold_truth.json"
```

当前结果为 `Recall@3 = Recall@5 = 1.0`，稳定重跑一致。Gold 校验通过只说明
P1 的输入门禁满足，不代表 Semantic Judge 的结论已自动确认。

2026-09-10 使用 `deepseek-v4-flash` 对 v2 Gold 做完整只读复验：12/12 技术成功，
2/2 `MAP_EXISTING` 选择正确，困难负样本误映射为 0，语义状态精确一致 10/12；
另外 2 项是 `NO_EQUIVALENT → AMBIGUOUS` 的保守弃权。`利息费用` 返回
`AMBIGUOUS` 且 `selected_metric_id = null`，符合“不确定时不强行映射”的门禁。
