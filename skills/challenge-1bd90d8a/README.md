# source-value-chart-builder

把给定数据表转成出版级图表：列索引 → 行级来源 → 可编辑 SVG → 真实 PDF/PNG → 数值回读与视觉复核。

- 入口：`SKILL.md`
- 脚本：`scripts/make_chart.py`、`scripts/svg_export.py`、`scripts/check_chart_result.py`
- 模板：`templates/chart-spec.json`
- 限制：不依赖 python-pptx；无渲染器时只交 SVG 并标 `partial`；不编造数据点或显著性。
