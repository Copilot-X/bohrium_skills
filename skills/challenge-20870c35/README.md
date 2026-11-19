# talk-content-organizer

学术汇报内容组织：听众结果 → throughline → 3–5 幕 → 页级 claim/证据/备注 → 时间预算 → Q&A → deck + PDF。

- 入口：`SKILL.md`
- 计划校验：`scripts/check_talk_plan.py`
- 生成：`scripts/make_pptx.py`（标准库 OOXML，不使用 python-pptx）
- 预览：`scripts/deck_pdf.py`、`scripts/pptx_export.py`
- 限制：时间/页数以 task.md 为准；无真实预览时标 `partial`，不伪造运行记录。
