# biological-pathway-illustrator 0.8.0

生物机制图与通路图：区室/分子/交互台账 → 可编辑 SVG → 真实 PDF/PNG → 逐边语义审计与视觉复核。

- 入口：`SKILL.md`
- 生成：`scripts/pathway_to_spec.py`
- 导出：`scripts/svg_export.py`（cairosvg/rsvg/inkscape 优先，matplotlib 图元重绘兜底）
- 审计：`scripts/check_pathway_result.py`
- 模板：`templates/pathway-entities.json`
- 限制：不依赖 python-pptx；无渲染器时只交 SVG + HTML，标 `partial`；不补“经典通路”。
