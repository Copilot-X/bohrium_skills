# structure-logic-reviewer v0.7.0

论文结构与逻辑审稿 skill：扫描真实稿件，建立章节—段落索引，按 D1–D6 六维输出有原文定位、逐字引文、证据和可执行 edit 的结构审稿；默认 review-only，任务明确要求改稿时才应用 `apply:true` 编辑生成 `revised.md`。

完整的输入输出、调用方式、六维定义、官方字段和异常处理以 `SKILL.md` 为准；问题分类见 `references/issue_taxonomy.md`，编辑动作契约见 `references/edit_action_spec.md`。

## 一个入口

```bash
export SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
python3 "$SKILL_DIR/scripts/main.py" run \
  --input /input --work /app/work --output /app/output
```

`main.py` 驱动 `inventory.py` → `workorder.py` → `diagnose.py` → `actions.py` → `emit.py`；`structure_kit.py` 提供文本、数字/引用保护与章节—段落索引工具。

## 输出

- `/app/output/structure-review.json`：六维 `dimensions`、`findings`、`patterns`、`priority_order`、`not_assessed`、`uncertainty`、`change_log`
- `/app/output/result.json`：任务字段 + 官方别名 `deliverable / preserved_facts / evidence_map / change_log`
- 仅 revise 模式：`/app/output/revised.md`，逐条 `change_log`，数字/条件/引用保护

## 限制

- 启发式诊断只评估可观察结构信号，不判断学术对错；findings 需作者或模型复核。
- PDF 依赖文本层；docx 依赖 Heading 样式。
- 缺稿件时 `status:"input_error"`、六维 `not_assessed`，不编造内容；入口仍写 `result.json` 并退出 0。
- 脚本保持 Python 3 stdlib-only，路径统一通过 `"$SKILL_DIR"` 解析。
