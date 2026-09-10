# AGENTS.md

## 1. 项目目标

本项目用于建设企业业务数据接入与本体映射层。

当前目标主链：

```text
真实 Excel / CSV
→ 数据接入与整理（Data Preparation）
→ 观测结构化（Observation Structuring）
→ 指标匹配（Metric Resolution：确定性匹配 + 可选 AI 语义匹配）
→ 已解析观测（ResolvedObservation）
```

同时，Curated 数据也需要服务异常检测与根因定位。

---

## 2. 当前冻结基线

`docs/DATA_MAPPER_CLEAN_REFACTOR_PLAN.md` V1.0.2 已审核冻结，代码实施尚未开始。下一步从 R0 开始，之后严格按 `R0 → R1 → R2 → R3` 推进；详细架构、迁移步骤和停止条件以该文档为准。

本次 Clean Refactor 到 `ResolvedObservation` 为止，允许冻结计划明确授权的内部 breaking refactor，但必须保持业务行为可回归验证，不永久保留旧 / 新双轨，不擅自扩大范围，也不顺手开发未来功能。

以下不属于本次重构：Organization Resolution、Ontology Instantiation、ActualObservation、正式 observation ID 和 Neo4j 写入。

---

## 3. 核心原则

1. **已有本体优先，但不是绝对约束**  
   优先利用现有 Definition / Knowledge。  
   如果已有本体无法合理表达新数据或新业务领域，可以提出扩展、修改或创建新本体结构的方案。

2. **正式本体变更必须显式发生**  
   Pipeline、Mapping 或 LLM 不得静默修改正式 Definition / Knowledge。  
   本体变更应形成明确建议，经人工审核或明确授权后再正式生效。

3. **规则优先，LLM 辅助**  
   确定性问题优先使用规则。  
   对模糊语义、新业务概念、本体缺口和结构设计，可以使用 LLM 分析并提出方案。

4. **允许质疑现有设计，但不得擅自大改**  
   当前设计是工作基线，不默认认为一定最优。  
   发现问题时应主动指出。涉及架构、公共接口、本体结构或明显跨模块调整时，应先说明方案和影响。

5. **大事受控，小事自主**  
   明确标记为 Frozen 的内容属于强约束。  
   未冻结的局部实现细节，可在不改变任务目标、核心架构和公共契约的前提下自主选择合理方案。

6. **最小修改优先**  
   优先完成当前目标所需的最小修改。  
   不因代码风格、抽象程度或“更优雅”而进行无关重构。

7. **明确区分数据层次**  
   Curated Dataset 是规范化数据，不等于本体实例数据。  
   Data Schema 与 Ontology Schema 不得混用。

8. **Draft 以正式观测结构为骨架，但不是正式实例**
   Observation Structuring 可以使用 Definition 中 `ActualObservation` 的窄结构投影来理解正式观测的属性、类型和相关 enum / struct；`ObservationDraft` 允许额外保留来源、行列、Raw / Curated identity、evidence 等追踪信息。正式 required 不得被当成 Draft 生成门禁，也不得因此提前猜测 Metric / Organization / status 或执行 Ontology Instantiation。

9. **优先最小可运行闭环**
   先解决当前真实问题，再根据实际需求扩展，不为假设中的未来场景过度设计。

---

## 4. 开工前

编码前必须：

1. 阅读 `docs/CURRENT_PHASE.md`；
2. 阅读当前任务涉及的架构文档；
3. 重构任务还必须阅读冻结的 `docs/DATA_MAPPER_CLEAN_REFACTOR_PLAN.md`；
4. 当任务与 `nano-ontoprompt` 的已有能力相关时，优先检查其对应实现，并判断：
   - REUSE
   - ADAPT
   - REFERENCE
   - IGNORE

不得机械复制 `nano-ontoprompt`。

简单、局部且明确的修改可以直接实施。

如果任务涉及架构、公共接口、本体结构或明显跨模块修改，应先说明准备如何修改以及可能影响。

---

## 5. 开发范围
本次 Clean Refactor 实施期间，如 CURRENT_PHASE.md 与项目负责人最终审核冻结的 DATA_MAPPER_CLEAN_REFACTOR_PLAN.md V1.0.2 存在冲突，以冻结重构计划为本次实施依据；不得借此扩大重构范围。
原则上只实现 `docs/CURRENT_PHASE.md` 中定义的内容。

当前 Phase 应尽可能给出明确的验收条件（Acceptance Criteria）。

不得：

- 无目的扩展到后续 Phase；
- 顺手重构无关模块；
- 静默修改已经明确 Frozen 的设计；
- 无理由改变稳定使用的公共接口。

发现当前范围之外的问题时：

- 如果不阻塞当前任务，记录并提出建议；
- 如果确实阻塞当前任务，说明原因后进行必要的最小修改。
不得让 Deterministic Resolution、Candidate Retrieval、Semantic Resolution 直接依赖 Knowledge 的具体存储实现。
---

## 6. 测试与完成标准

代码写完不等于任务完成。

完成至少意味着：

1. 当前任务目标已经实现；
2. `CURRENT_PHASE.md` 中的验收条件满足；
3. 当前阶段规定的测试通过；
4. 已有相关回归测试通过；
5. 当前任务存在真实样例或集成场景时，使用真实样例进行必要验证；
6. Definition / Knowledge 未被非预期修改；
7. 没有引入与当前目标无关的复杂度。

测试或关键验收条件未通过，不得标记任务完成。

---

## 7. 完成后报告

每次任务完成后简要报告：

- 修改了什么；
- `nano-ontoprompt` 如何参考（如适用）；
- 实际运行了哪些测试；
- 验收结果；
- 是否发现现有设计问题；
- 当前真实限制；
- 后续建议。

发现的问题可以提出，但不要未经确认顺手实施明显超出当前目标的大规模改造。
