---
name: multi-document-synthesizer
description: Compare and integrate multiple scientific documents in a fixed case bundle along explicit dimensions (question, data, method, metric, conclusion, limitation), preserving conflicts and evidence locations. Writes /app/output/result.json with comparison_table[], synthesis, conflicts[] and sources. Use when several papers or documents must be compared or combined.
---

# 多文档比较与整合（Multi-document Synthesizer）

## 用途与触发

输入含 `documents[]` / 多篇论文 / 多份材料，要求「比较」「整合」「综述」「异同分析」时使用。

## 执行步骤

1. **盘点文档**：`doc_id → 标题 → 类型（研究/综述/标准/数据）→ 可读范围`。
2. **定对照维度**（默认六维，可按 task.md 增删）：
   - 研究问题、数据/样本、方法、评价指标、主要结论、局限与边界
3. **逐文档填表**：每个单元格 = `值 + 定位`；未报告写 `not_reported`，不要留空。
4. **口径对齐**：比较前检查指标定义、单位、样本是否一致；不一致必须显式说明。
5. **整合**：先给「一致结论」，再给「分歧结论」，最后给「证据缺口」。
6. **回填来源**：每条整合结论挂到具体文档 + 段落。

## 输出契约

```json
{
  "case_id": "...",
  "documents": [{"doc_id": "...", "title": "...", "type": "research|review|standard|dataset"}],
  "comparison_table": [
    {
      "dimension": "method",
      "cells": [
        {"doc_id": "d1", "value": "...", "locator": {"paragraph_id": "p4"}, "status": "reported"},
        {"doc_id": "d2", "value": null, "status": "not_reported"}
      ]
    }
  ],
  "synthesis": {
    "agreements": [{"text": "...", "support": [{"doc_id": "d1", "paragraph_id": "p4"}]}],
    "divergences": [{"text": "...", "sides": [{"doc_id": "d1", "claim": "..."},
                                               {"doc_id": "d2", "claim": "..."}],
                     "resolution": "unresolved|explained_by_condition"}],
    "gaps": [{"text": "缺少跨数据集验证", "affected_dimension": "data"}]
  },
  "conflicts": [{"item": "...", "positions": [], "note": "..."}],
  "uncertainty": [],
  "change_log": [{"step": 1, "action": "...", "evidence": "..."}]
}
```

## 比较规则

1. 只在**同维度、同口径**下比较；口径不同先换算或声明不可比。
2. 「A 优于 B」必须给出指标名、数值、条件；否则只能写「A 在某条件下报告了更高值」。
3. 综述类文档不能当作原始证据；引用其结论时标注 `source_type: review`。
4. 分歧不强行调和；能由条件差异解释的写 `explained_by_condition` 并说明条件。
5. 整合结论不得引入任何单篇文档都没有的信息。

## 异常处理

| 情况 | 处理 |
|---|---|
| 文档不可读 | 表格中标 `unreadable`，说明原因，不推断内容 |
| 文档语言不同 | 保留原文引文，回答语言按 task.md |
| 文档数量超过预算 | 按与问题的相关性排序，优先读方法/结果/结论段 |

## 自检

- 每个维度都有全部文档的单元格（含 not_reported）
- 每条 synthesis 有 support 定位
- 没有无出处的比较级表述

## 最小示例（示例）

三维度表：方法 / 指标 / 结论；两篇文档在「指标」维度口径不同，标 `incomparable`，synthesis 中列为 gap。
## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/inspect_input.py` | 盘点多文档输入与可读范围 |
| `scripts/validate_result.py` | 校验 comparison_table、synthesis、conflicts 与 result.json |
| `references/00-protocol.md` | 统一运行契约 |
| `references/01-evidence.md` | 证据定位与逐字引用规则 |
| `references/02-reading.md` | 多文档阅读与对照维度 |
| `references/08-quality-gates.md` | 交付质量闸门 |
| `templates/result_envelope.json` | result.json 信封示例 |

## 本题交付对照

- 阅读/解析结果：按题目要求的答案、结构化字段、翻译或知识结构；完成标准：覆盖任务要求且能回到材料定位。
- 证据与边界：页码、段落、图表、字段或公式位置；未报告和不确定项；完成标准：关键结论可复核，无法判断不被写成事实。
- 运行状态：成功、部分完成、无法判断或拒答及原因；完成标准：异常原因和可人工接管位置清楚。
