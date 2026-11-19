# paper-to-deck-builder

论文 → 页级 claim 台账 → 结论式标题/证据/备注/时间预算 → 可编辑 PPTX → 真实 PDF 预览。

- 入口：`SKILL.md`
- 生成：`scripts/make_pptx.py`（标准库 OOXML，不使用 python-pptx）
- 预览：`scripts/deck_pdf.py`、`scripts/pptx_export.py`
- 模板：`scripts/audit_ppt_template.py`、`scripts/adapt_deck.py`
- 限制：没有 office 渲染时，同源 PDF 是预览，不冒充原生渲染；缺失图表不编造。
