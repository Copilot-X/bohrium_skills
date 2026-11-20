# apparatus-and-route-drawer 0.8.0

实验装置图与技术路线图：部件/阶段/连接/工况/判据台账 → 可编辑 SVG → 真实 PDF/PNG → MECE 与视觉复核。

- 入口：`SKILL.md`
- 生成/编辑：`scripts/build_safe_diagram.py`
- 导出：`scripts/svg_export.py`（cairosvg/rsvg/inkscape 优先，matplotlib 图元重绘兜底）
- 审计：`scripts/check_diagram.py`
- 模板：`templates/apparatus_spec.json`、`templates/route_spec.json`
- 限制：不依赖 python-pptx；无渲染器时只交 SVG + HTML，标 `partial`；不补材料外部件/步骤。
