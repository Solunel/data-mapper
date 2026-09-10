# R3 Clean Break 验收记录

实施基线：`DATA_MAPPER_CLEAN_REFACTOR_PLAN.md` V1.0.2。

## 结果

R3 停止条件全部通过，Clean Refactor 可以进入 Frozen 状态。

- 新主链 `map_curated_observations()` 是唯一正式 Mapping 入口；
- 旧混合 Request、Observation Candidate、Plan / Report / Result、旧入口和临时
  adapter / facade 已删除；
- `phase25_*` 生产文件已退役，Gold / Recall / Pilot 收敛到
  `data_mapper.evaluation`；
- Production 不依赖 Evaluation；
- README、ARCHITECTURE、CURRENT_PHASE 和本地调用入口已更新；
- 未实现任何计划外的写入、实例化、Organization Resolution 或存储 adapter。

## 技术 ID 迁移登记

| 退役身份 | 正式身份 | 验证 |
| --- | --- | --- |
| `mapping_run_id` | Structuring 使用 `structuring_run_id`；Resolution 使用 `resolution_run_id` | 相同输入稳定重跑；职责变化时 ID 随对应输入变化 |
| 来源 Mapping `candidate_id` | `observation_draft_id` | 只依赖观测结构与来源，Metric 解析变化不反向改变 Draft ID |
| 旧复合 candidate 中的 Metric 关联 | `metric_subject_id` + `EffectiveMetricResolution` | Draft、Decision、有效解析与 ResolvedObservation 可按 subject 稳定组合 |

Candidate Retrieval 的 `candidate_set_id`、Metric `decision_id` 与 Phase 1 Raw /
Curated 稳定身份保持冻结语义。`SemanticResolution.resolution_id` 继续是每次 Judge
执行的唯一追踪 ID，不进入确定性 parity。

冻结基线中 Evidence 的 `source="MappingRequest"` 是已有序列化 provenance 值，
不是仍存在的 Python API / DTO 或消费者。R3 曾尝试只做该文本重命名，但独立
parity 捕获到稳定 ID 漂移，因此已撤回；最终基线未放宽、未重写。

## 验证

- 全量测试：`85 passed`；
- 独立 Clean Refactor parity：PASS，R3 snapshot SHA-256
  `ae14d432c508f1510b135ed197f6dcc631f226918ce7cf0fac83a599c2cd1f53`；
- Python compileall：PASS；
- Git diff check：PASS（仅有 Git 的 CRLF 转换提示）；
- 旧符号 AST 消费者扫描：`[]`；
- 旧模块存在性与公开导出负向测试：PASS；
- Production → Evaluation / 具体本体存储依赖负向测试：PASS；
- Definition SHA-256：
  `4bad41a3f5f5c38e33afa741ebef97972a27e8cf6e7ce17ea67f65a5ee974fbf`；
- Knowledge SHA-256：
  `2bfed6290c551ee9fedafe1a9fcae2f54e9b018fa0da46b4604fd87883fbcbb5`；
- nano 参考目录 Git tree：
  `2e4a8abb8374eb0a32357636f9a6df6fc04f993d`。

## Parity 结论

Curated 内容、结构三态与 bindings、MetricSubject、确定性状态 / 目标 / 证据、
Candidate Retrieval 排名与完整表级 context、Semantic 安全状态、Effective Metric
结果、Proposal 内容和只读边界均通过。最终不存在未登记的业务行为差异。

允许且已登记的差异仅为 DTO 外形、模块 / import 路径、CLI 命名，以及职责纠正后
的技术 ID。实时 LLM 自由文本与唯一 resolution ID 不作为逐字 parity 条件。
