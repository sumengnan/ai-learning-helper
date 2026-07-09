---
name: table-cleanup
description: 清洗与快速透视 CSV/Excel 表格数据（缺失值、类型、去重、分组统计）
---
# 表格清洗技能

当用户上传或提到 CSV/表格数据、需要清洗或做统计时使用本技能。

## 步骤
1. 用 `read_skill_resource("table-cleanup", "scripts/describe.py")` 取到概览脚本。
2. 把脚本连同用户数据写入沙箱，用 `run_python` 执行，先看概览：形状、缺失值、每列类型。
3. 依据概览决定清洗动作：去重、填充或删除缺失、类型转换。
4. 需要分组/透视统计时，参考 `read_skill_resource("table-cleanup", "refs/pandas-tips.md")`。
5. 完成后可 `unload_skill("table-cleanup")` 释放上下文。

## 注意
- 读取编码优先 utf-8，失败再试 gbk。
- 大文件先 `nrows=100` 抽样探查，确认无误再全量处理。
