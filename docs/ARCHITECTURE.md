# ARCHITECTURE.md

## 1. 项目目标

本项目用于建设企业业务数据接入与本体映射层。

当前核心目标是：

> **参考 nano-ontoprompt 的数据处理与 Mapping 流程，将真实企业 Excel / CSV 转换为可稳定使用的数据，并进一步映射到我们已有的 Definition / Knowledge。**

已有本体优先使用，但不假设本体永远完整。

如果现有本体无法合理表达新的业务数据，应能够发现问题并提出本体演化建议。

---

## 2. 当前主流程

```text
真实企业 Excel / CSV
        ↓
      数据接入
        ↓
    Raw Dataset
        ↓
Pipeline
解析 / Data Schema 推断 / 清洗 / 整形
        ↓
   Curated Dataset
    规范化业务数据
        ↓
      质量检查
        ↓
    本体 Mapping
        ↓
┌──────────────┬──────────────┬──────────────┬──────────────┐
│              │              │              │
MATCHED     AMBIGUOUS      UNMATCHED     ONTOLOGY_GAP
│              │              │              │
直接映射      需要确认       暂不映射       本体变更建议
                                               ↓
                                            审核确认
                                               ↓
                                            本体演化
                                               ↓
                                            重新 Mapping
        ↓
Ontology Instance Data
        ↓
   Neo4j / 后续应用
```

---

## 3. 数据层次

### Raw Dataset

企业原始数据进入系统后的原始数据资产。

主要保留：

- 原始文件；
- Sheet；
- 原始表头；
- 原始数据；
- 来源信息。

Raw 层尽量不改变原始业务数据。

---

### Data Schema

描述原始数据本身的结构，例如：

- Sheet；
- 列；
- 表头；
- 数据类型；
- 表结构。

Pipeline 中的 SchemaInference 主要属于这一层。

它不是 Ontology Schema。

---

### Curated Dataset

经过 Pipeline 处理后的规范化数据。

主要解决：

> **原始 Excel 是否已经变成程序能够稳定处理的数据。**

可能包括：

- 表头处理；
- 类型统一；
- 日期和单位规范；
- 数据清洗；
- 宽表整形；
- 必要的数据质量检查。

Curated Dataset 不等于本体实例。

它是 Raw Data 与本体 Mapping 之间的中间数据层。

规范化过程中应尽量保留必要的数据来源信息，方便后续追踪。

---

### Ontology Schema

描述业务语义，包括：

- 对象；
- 属性；
- 指标；
- 关系；
- 计算关系。

当前主要由 Definition / Knowledge 表达。

可以简单理解为：

> **Data Schema 说明“表长什么样”；Ontology Schema 说明“这些数据是什么意思”。**

---

### Ontology Instance Data

Curated 数据完成 Mapping 后，按照 Definition / Knowledge 表达出来的业务实例数据。

例如实际观测可能包含：

```text
organization
metric
period
actual_value
unit
source
```

---

## 4. Mapping 原则

Mapping 优先利用已有 Definition / Knowledge。

基本顺序：

```text
Curated 数据
    ↓
正式名称 / 别名 / 已知规则
    ↓
必要时 LLM 辅助判断
    ↓
Mapping 结果
```

需要区分：

### MATCHED

已有本体能够可靠表达，直接映射。

### AMBIGUOUS

存在多个候选或判断不够确定，需要进一步确认。

### UNMATCHED

当前无法可靠映射，但不代表一定缺少本体。

例如辅助字段、无关字段或信息不足。

### ONTOLOGY_GAP

确认存在真实业务概念，但当前本体无法合理表达。

此时生成本体变更建议，经确认后再更新正式本体并重新 Mapping。

原则：

> **匹配不上，不等于一定要扩展本体。**

正式 Definition / Knowledge 不应被 Mapping 或 LLM 静默修改。

---

## 5. nano-ontoprompt 的角色

`nano-ontoprompt` 是当前阶段最重要的参考实现。

目前重点研究它的实际代码链路：

```text
Dataset
→ Pipeline
→ Curated
→ Quality / Review
→ Mapping
→ Ontology / Graph
```

当前不提前决定哪些代码一定复用。

后续根据真实代码逐模块判断：

- `REUSE`：可以直接借鉴；
- `ADAPT`：借鉴并改造；
- `REFERENCE`：只参考设计；
- `IGNORE`：不适用于本项目。

执行原则是：

> **优先检查并利用 nano-ontoprompt 的已有实现；确认不适合当前需求后，再考虑自行实现。**

其中目前已经明确的一点差异是：

> nano-ontoprompt 更偏从数据生成本体；  
> 我们已有 Definition / Knowledge，因此 Mapping 应优先利用已有本体，同时保留本体演化能力。

其他差异待实际代码分析后再决定。

---

## 6. 后续扩展

Curated Dataset 未来还需要服务：

- 异常检测；
- 根因定位；
- AI 问答；
- 其他业务分析。

但这些能力当前不是数据映射层的主要实现目标。

当前设计只需保证 Curated 数据结构清晰、稳定、可追踪，并且**不阻碍后续被异常检测、根因定位等模块使用**。

具体适配方式在对应阶段再设计。

---

## 7. 当前设计原则

1. **优先参考 nano-ontoprompt，不重复造轮子。**
2. **先理解真实代码，再决定复用还是改造。**
3. **先治理数据，再进行业务语义 Mapping。**
4. **Data Schema 与 Ontology Schema 分开。**
5. **已有本体优先，但允许后续演化。**
6. **匹配失败不等于本体缺口。**
7. **确定性规则能够可靠解决的问题优先使用规则；模糊语义可使用 LLM 辅助。**
8. **正式本体变更必须显式发生。**
9. **优先完成最小可运行闭环，不提前建设未来平台。**

---

## 8. 仓库结构

当前仓库采用以下顶层结构：

```text
enterprise-data-mapping/
│
├─ AGENTS.md
├─ README.md
│
├─ docs/
│   ├─ ARCHITECTURE.md
│   ├─ CURRENT_PHASE.md
│   └─ NANO_REUSE_ANALYSIS.md
│
├─ ontology/
│   ├─ Definition.json
│   └─ Knowledge.json
│
├─ references/
│   └─ nano-ontoprompt/
│
├─ src/
└─ tests/
```

各目录职责：

* `AGENTS.md`：AI 长期开发规则；
* `docs/`：项目架构、当前阶段与分析结论；
* `ontology/`：正式 Definition / Knowledge 本体资产；
* `references/`：外部参考代码，默认不直接修改；
* `src/`：本项目正式实现代码；
* `tests/`：测试、样例与回归验证。

目录结构只定义当前稳定边界；后续子目录根据实际实现逐步增加，不提前设计。
