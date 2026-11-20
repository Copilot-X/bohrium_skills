# 证据与溯源（Evidence & Provenance）

## 1. 定位键

| 材料类型 | 首选定位键 | 备选 |
|---|---|---|
| 带段落编号的论文 | `paragraph_id` | `section_id` + 段内序号 |
| PDF | `page` + `paragraph_id` | `page` + 首句 |
| 表格 | `table_id` + `row` + `column` | `table_id` + 单元格文本 |
| 图 | `figure_id` + `panel` | `figure_id` + 图注句 |
| 幻灯片 | `slide_no` + `shape_id` | `slide_no` + 文本 |
| 数据集 | `dataset_id` + `row_id` + `column` | 主键 + 字段名 |

**一个证据对象至少包含定位键和一个可逐字比对的片段。**

## 2. 引文选取

好的引文：
- 逐字复制，不改时态、单复数、单位符号。
- 长度足以独立支撑论断（通常 8–40 词）。
- 包含关键数值、条件、比较对象。

不合格引文：
- 只有 "the results show that" 之类无信息句。
- 跨句拼接（用 `...` 省略时必须保证语义未被扭曲）。
- 翻译后回填（原文是英文就必须引英文）。

## 3. 论断分类

| kind | 定义 | 要求 |
|---|---|---|
| `reported` | 原文直接陈述 | 必须给 evidence |
| `derived` | 由原文数值计算 | 给 evidence + `formula` 字段 |
| `inferred` | 推断/外推 | 给 evidence + `rationale` + `confidence`(0–1) |

推断不得伪装成 `reported`。这是本题最常见的扣分点。

## 4. 数值处理

- 保留原文精度，不擅自四舍五入。
- 单位必须与原文一致；换算时写出换算系数。
- 百分比与百分点区分。
- 区间值写成 `{"min":..., "max":..., "unit":...}`。
- 原文只给图（无数字）时，读图需标注 `read_from_figure: true` 与估计误差。

## 5. 条件绑定

科研结论高度依赖条件。每条数值型论断尽量绑定：

```json
"conditions": {
  "temperature": "350 °C",
  "sample": "Ni/Al2O3",
  "duration": "4 h",
  "dataset": "test split"
}
```

材料未给的条件写 `null`，不要用常识补全。

## 6. 冲突处理

发现材料内部矛盾时：

```json
{"claim_id":"c7","text":"报告的反应温度存在冲突","kind":"reported",
 "evidence":[{"paragraph_id":"p12","quote":"...350 °C..."},
             {"paragraph_id":"p31","quote":"...400 °C..."}],
 "conflict":{"detected":true,"resolution":"unresolved",
             "note":"正文与补充材料不一致，未找到裁决依据"}}
```

优先保留冲突，而不是静默选一个。

## 7. 覆盖度

`coverage` 至少包含：

```json
{"documents_read":["doc1"],"documents_skipped":[],
 "sections_read":["Abstract","Methods"],
 "sections_skipped":[{"section":"Appendix C","reason":"材料未提供"}],
 "notes":"..."}
```

覆盖度是评分项，写实情，不要虚报。

## 8. 溯源链

每个最终产物中的事实，应能沿以下链路回到材料：

```
result.json -> artifact -> source_map -> evidence -> paragraph_id -> 原文
```

链路中断处必须在 `uncertainty` 中登记。
