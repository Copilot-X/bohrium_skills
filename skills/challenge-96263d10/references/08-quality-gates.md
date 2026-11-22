# 提交前自检清单（Quality Gates）

在写最终 `result.json` 前逐项打勾。任何一项不通过，先修复再提交。

## A. 契约一致性

- [ ] `case_id` 与输入完全一致（字符串逐字符）
- [ ] 输入中的版本字段（`paper_id`、`material_version`、`doc_id`）原样回填
- [ ] task.md / output_contract 要求的每个字段都存在
- [ ] 枚举值使用契约规定的词表（`status`、`kind`、`type`）
- [ ] 没有 `TODO` / `TBD` / 占位文本
- [ ] JSON 可被 `json.loads` 解析，UTF-8 无 BOM

## B. 证据完整性

- [ ] 每个关键论断至少 1 条证据
- [ ] 每条证据有定位键（paragraph_id / page / figure_id / slide_no / table_id）
- [ ] quote 逐字来自原文，未翻译、未拼接
- [ ] 数值证据含单位与条件
- [ ] 推断标注为 `inferred`，未伪装成 `reported`

## C. 覆盖度

- [ ] 每个问题/每个要求的字段都有条目
- [ ] 无法回答的条目在 `unanswered_parts` / `missing` / `uncertainty` 中说明
- [ ] `coverage` 或等价字段如实记录已读/未读

## D. 产物

- [ ] 所有 `artifacts[].path` 指向的文件真实存在且非空
- [ ] 可编辑文件可被对应软件打开（pptx/docx/svg/csv）
- [ ] 有渲染预览（PDF/PNG），且来自真实渲染
- [ ] 产物路径相对于 `/app/output`

## E. 诚实性

- [ ] 未伪造运行记录、分数、截图
- [ ] 未声称执行了未执行的步骤
- [ ] 部分完成时 `status=partial`，未写成 `completed`
- [ ] 已知限制写入 `limitations` / `uncertainty`

## F. 预算与稳定

- [ ] 重复运行可复现（seed 固定）
- [ ] 单次运行在 900 秒内完成
- [ ] 结果文件先落盘再迭代，任何时刻可提交

## G. 语言与格式

- [ ] 回答语言按 task.md（默认与问题语言一致）
- [ ] 数字格式统一（小数点、千分位）
- [ ] 中英文标点不混用到影响解析
