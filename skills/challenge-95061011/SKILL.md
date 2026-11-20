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
