# Phase 1 合成测试样例

本目录中的文件均为 **synthetic（合成）测试数据**，不包含真实客户或生产数据。

- `synthetic_multisheet.xlsx`：两个非空 Sheet；`Synthetic Monthly` 的表头位于第 3 行，`Synthetic Budget` 的表头位于第 1 行；含日期、布尔值、数值和空值。
- `synthetic_sales_bom.csv`：UTF-8 BOM CSV；含首尾空白、完全空白行、完整重复行、空值，以及可由显式类型覆盖定位的转换失败。
- `synthetic_header_only.csv`：只有表头没有数据，用于验证空 Curated 表不会通过质量检查。
- `synthetic_invalid.xlsx`：故意不是合法 XLSX 的文本文件，用于验证明确解析失败。
- `synthetic_unsupported.txt`：范围外格式，用于验证明确拒绝不支持的输入。

`tests/test_row_report_parsing.py` 还会在 pytest 临时目录中动态生成指标在行的
synthetic 工作簿和 CSV，覆盖单行表头、两层/三层合并表头、左侧空白边距、
非首行表头及新增值列。动态样例不会写入或覆盖用户提供的测试工作簿。

这些样例只用于 Phase 1 技术验收，不能代表真实企业 Excel 的全部复杂度。
