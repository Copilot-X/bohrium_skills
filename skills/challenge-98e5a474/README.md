# scientific-schematic-editor 0.8.0

科研示意图生成与编辑：实体—关系—证据台账 → 可编辑 SVG → 真实 PDF/PNG → 逐元素来源与视觉复核。

- 入口：`SKILL.md`
- 生成：`scripts/build_schematic.py`
- 导出：`scripts/svg_export.py`（cairosvg/rsvg/inkscape 优先，matplotlib 图元重绘兜底）
- 审计：`scripts/check_schematic_result.py`、`scripts/compare_svg.py`
- 模板：`templates/entities.json`
- 限制：不依赖 python-pptx；无渲染器时只交 SVG + HTML，标 `partial`；不编造实体/数值/来源。
