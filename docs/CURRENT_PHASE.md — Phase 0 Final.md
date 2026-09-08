# CURRENT_PHASE.md

## Phase 0 — nano-ontoprompt 代码分析与借鉴判断

### 目标

在不修改任何代码的前提下，理解 `nano-ontoprompt` 与当前项目相关的真实实现，为后续开发确定借鉴和改造方向。

本阶段只回答：

> **nano-ontoprompt 的实际主流程是什么？其中哪些实现值得我们继续利用或改造？**

---

## 输入材料

分析时结合：

- `references/nano-ontoprompt/`
- `ontology/Definition.json`
- `ontology/Knowledge.json`
- `AGENTS.md`
- `docs/ARCHITECTURE.md`

关于 nano 的结论必须以实际代码和真实调用链为依据。

README 可以作为线索，但不能代替代码事实。

---

## 本阶段任务

### 1. 还原真实主调用链

重点确认：

```text
Excel / CSV
→ Dataset
→ Pipeline
→ Curated
→ Quality / Review
→ Mapping
→ Ontology / Graph
```

需要说明：

- 数据真实入口；
- 主要调用模块；
- 各阶段输入 / 输出；
- 数据形态如何变化。

如果实际代码与上述流程不同，以实际代码为准。

---

### 2. 分析与当前项目相关的核心实现

围绕真实调用链分析：

- Excel / CSV 接入；
- Dataset；
- Pipeline；
- Data Schema 推断；
- 清洗 / 整形 / 宽表处理；
- Curated Dataset；
- Quality / Review；
- Mapping；
- 本体 / Graph 的主流程边界。

不要求逐文件、逐函数解释整个仓库。

与当前主链无关的前端、认证、部署和通用基础设施等内容，除非影响判断，否则不深入分析。

---

### 3. 形成初步借鉴判断

对相关模块给出：

- `REUSE`
- `ADAPT`
- `REFERENCE`
- `IGNORE`

重要判断简要说明：

1. nano 当前如何实现；
2. 对应代码位置或关键入口；
3. 为什么适合或不适合我们；
4. 如果需要改造，主要差异在哪里。

这些属于当前阶段的工程判断，不是永久冻结结论。

证据不足时标记为待确认，不猜测。

---

### 4. 确认关键差异

重点判断：

- nano 对真实企业 Excel 的支持程度；
- Curated Dataset 的实际实现和数据形态；
- Mapping 实际如何工作；
- nano 是否以及如何创建或修改本体；
- 与我们已有 Definition / Knowledge 优先匹配的需求有什么差异；
- 本体缺口和本体演化有哪些实现可以参考；
- 哪些问题适合确定性代码，哪些可能需要 LLM。

---

## 交付物

输出：

`docs/NANO_REUSE_ANALYSIS.md`

保持简洁，至少包括：

1. 真实主调用链；
2. 数据形态变化；
3. 核心代码地图；
4. `REUSE / ADAPT / REFERENCE / IGNORE` 初步判断；
5. nano 与我们当前架构的关键差异；
6. 推荐的下一 Phase 切入点；
7. 尚未确认的问题。

---

## 验收标准

Phase 0 完成时应满足：

- 主调用链有实际代码依据；
- 关键判断可以定位到对应代码；
- Data Schema 与 Ontology Schema 没有混淆；
- 没有仅依据 README 得出关键结论；
- 已经能够说明 nano 与我们项目最主要的相同点和差异；
- 已经足以判断下一 Phase 应从哪里开始；
- 未修改 nano 源代码；
- 未修改 Definition / Knowledge；
- 未开始编写正式项目功能。

---

## 停止条件

当已有证据足以：

1. 说明 nano 与我们相关的真实主流程；
2. 判断核心模块大致属于 REUSE / ADAPT / REFERENCE / IGNORE；
3. 确定下一 Phase 的合理切入点；

即停止继续扩大代码研究范围。

不要为了“彻底读懂整个 nano-ontoprompt”而继续分析与当前目标无关的代码。

---

## 本阶段不做

不要在 Phase 0：

- 编写我们的正式实现；
- 修改 nano-ontoprompt；
- 设计完整未来平台；
- 深入异常检测和根因定位；
- 提前设计复杂本体版本或审批系统；
- 提前实现前端或 Neo4j 扩展能力。

本阶段只解决：

> **先看懂 nano 与我们相关的真实实现，再决定下一步怎么借。**