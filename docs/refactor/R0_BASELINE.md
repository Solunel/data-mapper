# R0 — Baseline Freeze

## 起点

- 分支：`codex/phase1-row-report-rework`
- 实施前 commit：`be0234df36ecb4dd9a75f5c7c10480926109323f`
- 实施前工作树：仅有用户既存未跟踪目录 `reports/`；R0 未修改或提交该目录
- 冻结实施基线：`docs/DATA_MAPPER_CLEAN_REFACTOR_PLAN.md` V1.0.2

## 自动 parity 快照

`tests/refactor_baseline.py` 从受版本控制的真实/合成 fixture 重新构造完整业务快照，
`tests/test_clean_refactor_parity.py` 与
`tests/baselines/clean_refactor_r0.json` 中的 SHA-256 基线自动比较。

覆盖内容：

- Phase 1 CSV、利润表与成本费用表的 Raw / Curated 稳定身份、schema、行范围、质量状态和完整业务 payload fingerprint；
- Phase 2 `READY / NEEDS_BINDING / BLOCKED`，其中 `NEEDS_BINDING` 明确覆盖“一项可投影、一项未决”的部分投影；
- `METRIC / GROUP / NOTE / UNKNOWN` 行角色；
- `MATCHED / AMBIGUOUS / UNMATCHED / ONTOLOGY_GAP` Metric 四态；
- `PERIOD_VALUE / YEAR_TO_DATE / PERIOD_BEGIN / PERIOD_END`、单/多 business scope、MetricSubject / MetricDecision / ObservationCandidate 稳定 ID；
- Phase 2.5 Gold Recall@3 / Recall@5、Top-K 顺序与分路分数；
- Semantic Context 的字段集合和内容 fingerprint，以及 NOTE、前后项、scope、period、unit 和同表 `qc.interest_expense` 冲突证据；
- 固定 Judge 响应下的 `PROPOSED`、人工 `CONFIRMED` 前后 Effective Mapping，以及只读 `OntologyChangeProposal`。

实时 LLM 的自由文本和随机 `resolution_id` 不参与逐字 parity；固定 Judge 的契约、
候选白名单、安全状态和审核边界参与 parity。

## 只读资产基线

- `ontology/Definition.json` SHA-256：`4bad41a3f5f5c38e33afa741ebef97972a27e8cf6e7ce17ea67f65a5ee974fbf`
- `ontology/Knowledge.json` SHA-256：`2bfed6290c551ee9fedafe1a9fcae2f54e9b018fa0da46b4604fd87883fbcbb5`
- `references/nano-ontoprompt-master/` Git tree：`2e4a8abb8374eb0a32357636f9a6df6fc04f993d`
- Gold fixture SHA-256：`a25902e6cec32f1d14ae67a52fb2cc430c6ae05bac2f1888c8be07abc9d67424`

后续每阶段使用 Git path diff 加资产 hash 检查，Definition / Knowledge 与 nano
不得出现改动。

## 旧 API / DTO 消费者清单

全仓库 `rg` 扫描确认的迁移对象：

- 生产/演示：`a.py`、`src/data_mapper/__init__.py`；
- 旧 Phase 2：`src/data_mapper/mapping.py`、`src/data_mapper/mapping_contracts.py`；
- Phase 2.5 生产反向依赖：`phase25.py`、`phase25_retrieval.py`、`phase25_semantic.py`、`phase25_deepseek.py` 及对应 contracts；
- 测试：`test_phase2_mapping.py`、`test_phase25_p0.py`、`test_phase25_p1.py`、`test_phase25_p2.py`、`test_phase25_p3.py`、`test_phase25_deepseek.py`、`test_a_phase25_entry.py`；
- 文档：`README.md`、`CURRENT_PHASE.md`、`ARCHITECTURE.md`、`PHASE_2_5_DESIGN.md`；历史冻结文档只保留历史说明，不作为新公开入口。

扫描符号包括：`MappingRequest`、`ObservationCandidate`、`MappingPlan`、
`MappingReport`、`MappingResult`、`map_curated_dataset`、
`EffectiveMappingView` 及生产 `phase25_*` 命名。

## R0 验证结果

- 项目解释器：`D:\Dev_Env\Miniconda3\envs\nano-ontoprompt-py311\python.exe`
- 全量测试：`64 passed`（5.58 秒）
- 首次沙箱内执行因 pytest 系统临时目录 ACL 得到 13 个 setup error；沙箱外以同一解释器重跑后 64 项全部通过，确认不是业务失败。

R0 新增 parity 测试通过、只读资产 hash 与消费者清单完成后，方可判定 R0 `PASS`。
