# CURRENT_PHASE.md

## Phase 1 — 真实 Excel / CSV → Curated 最小闭环

### 阶段目标

建立并验证当前项目第一条最小可运行数据闭环：

```text
真实 Excel / CSV
→ Raw Dataset
→ 解析
→ Data Schema
→ Pipeline
→ Curated Dataset
→ 基础质量检查
```

本阶段只解决：

> **如何把真实 `.xlsx` / `.csv` 文件稳定、可追踪地转换为程序可继续处理的 Curated Dataset。**

Phase 1 不处理业务本体语义。Data Schema 只描述输入表的结构与类型，不得混入 Definition / Knowledge 中的 Ontology Schema。

---

## 1. 规划依据与 nano 借鉴边界

Phase 1 以 `docs/NANO_REUSE_ANALYSIS.md` 的真实代码分析为依据，不机械复制 nano-ontoprompt。

### ADAPT

借鉴并改造：

- Dataset 元数据与原始 bytes / 版本分离的思路；
- `PipelineContext + PipelineStep` 的小型显式处理链；
- 规则优先的 SchemaInference、Cleansing 和 Quality 计算方式；
- 使用真实文件做端到端验收的测试思路。

主要改造点：

- 补齐文件、Sheet、原始表头、源行和转换记录等 lineage；
- Data Schema 形成单一、稳定、可持久化的数据契约；
- 类型推断与实际类型转换分开，转换失败必须可见；
- 清洗规则显式配置，不默认静默删行或去重；
- Curated 使用单一模型，不引入 nano 的 `Dataset(kind="curated")` / `CuratedDataset` 双模型；
- Pipeline 按真实输入版本运行，不固定读取 version 1，也不默认截断到 10,000 行。

### REFERENCE

只参考设计思想：

- nano 的宽表“建议与执行分离”；Phase 1 不实现 LLM 拆表或自动拆表；
- Quality Report 的分项指标结构；Phase 1 不强制汇总为一个可能误导的总分；
- Review 的“变更显式发生”原则；Phase 1 不建设审核工作流。

### IGNORE

Phase 1 不采用：

- nano 当前未真正驱动执行的 DAG 外壳；
- MinIO、Celery、Redis、数据库、Connection 调度等平台基础设施；
- Route B/C、文档提取和 LLM 本体生成；
- Mapping、Ontology、Graph、Logic、Action 相关实现；
- nano 对缺少拆分配置的宽表进行 LLM 建议或机械对半拆分的回退行为。

当前仍不把任何 nano 完整模块判定为无条件 `REUSE`。实施时可以复用小型算法或测试思路，但必须先适配本阶段的数据契约，并确认代码来源与许可证。

---

## 2. 本阶段范围

### 2.1 输入范围

必须支持：

- `.xlsx`；
- `.csv`。

最低输入能力：

- Excel 可枚举并分别处理所有非空 Sheet，而不是只读取活动 Sheet；
- 支持第一行表头，并以显式配置或简单确定性规则处理至少一种“表头不在第一行”的真实样例；
- CSV 至少正确处理 UTF-8、UTF-8 BOM；若项目样例需要中文传统编码，应以明确配置或受限候选集支持，不做不可解释的任意猜测；
- 对不支持或无法可靠解析的输入给出明确错误，不得伪装成成功的空数据集。

`.xls`、`.xlsb`、宏执行、受密码保护文件、图片/OCR 表格和复杂文档格式不在本阶段范围。

### 2.2 Raw Dataset

Raw Dataset 是不可变的输入记录。最小信息包括：

- dataset / version 标识；
- 原始文件名、扩展名、文件大小、内容校验值；
- 原始文件位置或内容引用；
- 接入时间；
- 解析配置及其版本；
- 解析状态和明确错误。

本阶段不要求数据库或对象存储。可以使用本地文件与轻量元数据完成验证，但 Raw 内容必须可回溯，且 Pipeline 不得覆盖原文件。

### 2.3 解析结果与 lineage

Excel 的每个 Sheet、CSV 文件分别形成一个表形解析单元。最小解析结果包括：

- `source_file`；
- `sheet_name`（CSV 可为空或使用固定标识）；
- `header_row`；
- 原始列名与规范化列名的对应关系；
- 数据行；
- 每行的 `source_row`；
- 解析警告和错误。

Phase 1 不为每个单元格建立复杂 provenance 对象。文件、Sheet、源行、原始列名与规范列名已经足以反推源位置；发生类型转换错误时，应额外记录源列和值。

### 2.4 Data Schema

Data Schema 只描述数据自身结构。每个解析单元至少包含：

- 列顺序；
- 原始列名；
- 唯一且非空的规范列名；
- 推断类型；
- nullable；
- 非空样本值；
- 推断依据或置信信息；
- 行数与表头位置。

最低类型集合：

```text
string | integer | number | boolean | date | datetime | null
```

Schema 推断必须使用确定性规则和可解释采样；不得仅凭首行决定类型。混合类型或证据不足时优先保守为 `string`，并记录 issue，不调用 LLM。

### 2.5 Pipeline

Phase 1 使用简单、固定顺序、可重放的步骤链，不设计 DAG：

```text
Parse
→ Normalize Headers
→ Infer Data Schema
→ Convert Types
→ Basic Clean
→ Build Curated Dataset
→ Quality Check
```

本阶段允许的最小转换：

- 生成唯一、稳定的规范列名，同时保留原始列名；
- 清除完全空白的数据行；
- 字符串首尾空白规范化；
- 按已推断或显式指定的类型转换值；
- 保留 null，不默认填充；
- 对转换失败保留原值或明确的空值策略，并记录 issue；
- 记录每一步的输入行数、输出行数、配置、警告和错误。

默认不得：

- 静默删除含部分空值的行；
- 自动删除重复行；
- 自动拆分宽表；
- 根据业务语义重命名列；
- 调用 LLM 改写数据。

### 2.6 Curated Dataset

Curated Dataset 是规范化数据，不是本体实例。每个 Curated Dataset 对应一个解析单元，至少包含：

- 稳定 ID 与生成时间；
- 对应 Raw Dataset / version；
- `source_file`、`sheet_name`；
- Data Schema；
- 类型化数据行；
- 行级 `source_row`；
- 原始列名到规范列名的映射；
- Pipeline 步骤、配置与执行统计；
- 质量报告和 issues。

Curated 的具体持久化格式在实现时选择满足类型、可读性和测试便利性的最简单方案。本阶段不为未来大规模数据提前引入分布式存储或复杂表格式。

### 2.7 基础质量检查

至少计算并报告：

- 输入行数、Curated 行数及差异原因；
- 列数；
- 各列 null 数量与比例；
- 完全重复行数量，但不默认删除；
- 类型转换失败数量与代表性样例；
- 空表、空表头、重复规范列名等结构问题；
- 解析和 Pipeline warnings / errors。

质量检查以可解释分项结果为主。空数据集不得报告为质量通过或满分。

---

## 3. 实施任务

Phase 1 实施时按以下顺序推进：

1. 确认至少一个 `.xlsx` 和一个 `.csv` 验收样例，记录它们代表的真实结构；
2. 定义 Raw Dataset、Parsed Table、Data Schema、Curated Dataset、Pipeline Report、Quality Report 的最小内部契约；
3. 实现 XLSX / CSV 接入与不可变 Raw 记录；
4. 实现多 Sheet 解析、表头定位、列名规范化和 lineage；
5. 实现 Data Schema 推断与显式类型转换；
6. 实现固定有序 Pipeline 和最小清洗规则；
7. 生成 Curated Dataset 与基础质量报告；
8. 用真实样例完成端到端测试，并补必要单元测试与失败场景测试；
9. 检查 Definition / Knowledge 与 nano 参考源码未被修改。

如果现有样例只是合成测试数据，仍可用于技术验收，但必须明确标注，不能据此声称已经覆盖真实客户 Excel 的全部复杂度。

---

## 4. 交付物

Phase 1 完成时应至少形成：

- 可从本地 `.xlsx` / `.csv` 生成 Curated Dataset 的最小正式实现；
- 明确的数据契约和必要说明；
- 基础质量报告；
- 至少一个 Excel 和一个 CSV 的端到端测试；
- 解析、Schema、Pipeline、Quality 的必要单元测试；
- 对不支持输入和解析失败的明确错误测试。

本阶段不要求 Web API、数据库服务、对象存储服务或前端页面作为交付物。最小闭环可以通过进程内服务或简单命令入口被测试和调用。

---

## 5. 验收条件

只有同时满足以下条件，Phase 1 才算完成：

1. 一个 `.xlsx` 和一个 `.csv` 真实通过 Raw → Parse → Data Schema → Pipeline → Curated → Quality 全链路；
2. Excel 至少验证多个非空 Sheet，或验证一个非第一行表头样例；如果验收文件不具备其中某种结构，必须补针对性 fixture；
3. Raw 文件未被修改，内容校验值可验证；
4. 每个 Curated 行可以追溯到源文件、Sheet 和源行，列可以追溯到原始列名；
5. Data Schema 与 Ontology Schema 明确分离，代码不读取或修改 Definition / Knowledge 来推断表结构；
6. 类型推断不是仅看首行；转换后的值与声明类型一致，失败项进入可定位的 issue；
7. Pipeline 步骤顺序、配置和行数变化可见且可重放；没有无法解释的静默删行、去重或拆表；
8. Curated Dataset 具有稳定列结构、类型化值、lineage、执行报告和质量报告；
9. 质量报告至少覆盖 null、重复行、转换失败、空表/表头问题，空数据集不会被判定为通过；
10. 相关单元测试、失败场景测试和 Excel/CSV 端到端测试通过；
11. `ontology/Definition.json`、`ontology/Knowledge.json`、`references/nano-ontoprompt-master/` 未修改；
12. 未实现 Mapping、本体演化、Neo4j、异常检测、根因定位或前端功能。

验收报告必须列出实际使用的样例、运行的命令、测试结果、已知限制和 nano 借鉴情况。

---

## 6. 停止条件

当已有实现和测试足以证明：

1. 支持范围内的 Excel / CSV 可以稳定形成 Raw Dataset；
2. 每个表形输入可以得到可解释的 Data Schema；
3. 固定 Pipeline 可以生成类型化、可追踪的 Curated Dataset；
4. 基础质量问题可以被检测并定位；
5. 产物已经具备下一阶段 Mapping 所需的结构信息与来源信息；

即停止 Phase 1。

不要为了支持所有 Excel 变体、任意规模数据、完整 DAG、分布式执行、平台化存储或未来分析场景而继续扩大范围。遇到范围外文件时，清楚报告限制并保留样例，进入后续阶段评估。

---

## 7. 本阶段不做

Phase 1 不做：

- Mapping 或 Mapping 状态设计；
- 读取 Definition / Knowledge 进行语义匹配；
- 本体创建、修改、演化或变更审批；
- Ontology Instance Data；
- Neo4j、ChromaDB 或其他图/向量存储；
- 异常检测与根因定位；
- LLM 数据提取、语义改写或宽表拆分；
- 前端、Web API、用户/权限系统；
- Connection、调度、增量同步、Celery / Redis；
- MinIO、数据库或分布式数据平台；
- `.xls`、`.xlsb`、OCR、PDF、Word、PPT、JSON、XML 等额外格式；
- 复杂数据审核、版本发布或回写工作流；
- 为尚未出现的性能与扩展需求提前抽象平台。

本阶段只完成：

> **把支持范围内的真实 Excel / CSV 可靠地变成可追踪、可验证的 Curated Dataset。**
