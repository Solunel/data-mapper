# Phase 1 数据契约与调用说明

## 边界

Phase 1 只处理 `.xlsx` / `.csv` 到 Curated Dataset 的确定性技术治理，不读取 `ontology/Definition.json` 或 `ontology/Knowledge.json`，也不进行 Mapping、本体演化或图存储写入。

公开入口：

```python
from data_mapper import PipelineConfig, curate_file

result = curate_file(
    "input.csv",
    PipelineConfig(
        type_overrides={"CSV": {"amount": "number"}},
    ),
)
```

返回的 `Phase1Result` 包含一个 `RawDataset` 和每个非空 Sheet / CSV 表对应的一个 `CuratedDataset`。`result.to_dict()` 可直接序列化为 JSON；日期和时间会输出 ISO 8601 字符串。

## 最小契约

- `RawDataset`：稳定 dataset/version ID、文件名、扩展名、大小、完整 SHA-256、绝对内容引用、接入时间、解析配置/版本、状态和错误。
- `ParsedTable`：源文件、Sheet、1-based 表头行、原始表头、带 1-based `source_row` 的数据行，以及解析 warning/error。
- `DataSchema`：列顺序、原始/规范列名、`string | integer | number | boolean | date | datetime | null`、nullable、样本、推断证据、置信度和推断方式。
- `CuratedDataset`：稳定 ID、Raw 关联、表头映射、Schema、类型化行、完整步骤报告、质量报告和 issues。它不是本体实例。
- `QualityReport`：输入/输出行数和差异原因、列数、逐列 null、完整重复行、转换失败样例、结构问题与是否通过。

## 确定性规则

- Excel 枚举全部非空 Sheet；CSV 使用固定标识 `CSV`。
- `header_rows` 允许按 Sheet 显式指定 1-based 表头行；未指定时只扫描前 20 行，选择至少两个唯一非空单元格且后续行具有合理数据密度的首个候选。单列表格或复杂合并表头应显式配置。
- CSV 默认严格尝试 `utf-8-sig`，失败后只尝试 `gb18030`；也可显式指定编码和分隔符。
- 列名只做 NFKC、首尾空白、大小写、标点转下划线和稳定去重，不做业务语义改名。
- 类型推断使用最多 100 个均匀抽取的非空样本；混合证据保守为 `string` 并记录 issue。显式类型覆盖优先。
- 转换失败默认置为 `null`，保留源文件、Sheet、源行、原始列和值；不静默删除记录。
- Basic Clean 只 trim 字符串并移除完全空白行；不填充 null、不去重、不拆表。

## 当前限制

不支持 `.xls`、`.xlsb`、宏执行、密码文件、OCR、合并/多层表头、公式重新计算、任意编码猜测和超大文件流式处理。合成夹具只证明当前最小闭环，不等同于真实客户数据验收。
