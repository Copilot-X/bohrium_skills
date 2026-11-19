# 输出契约（apparatus / technical route）

`scripts/result_graphics.py` 是唯一写 `result.json` 的模块。模型不得手写或改写
官方字段；只写 `/app/work/spec.json`，然后由 `scripts/run_case.py` 装配。

## 1. 文件落盘

```
/app/output/result.json              # 结构化结果
/app/output/figures/<figure>.svg     # 可编辑源文件（分层命名组，文字为 <text>）
/app/output/figures/<figure>.pdf     # 真实矢量导出
/app/output/preview/<figure>.png     # 真实栅格预览（默认 300 dpi）
/app/work/inventory.json             # 真实输入盘点
/app/work/normalized-spec.json       # 标准化后的中间表示
/app/work/render.json                # 渲染统计/错误
/app/work/export.json                # 各渲染器探测与产物校验
/app/work/coverage.json              # 覆盖/来源/导出审计报告
```

`result.artifacts[]` 与 `editable_source` / `export_artifact` 中的路径一律相对
`/app/output`。

## 2. 顶层字段

| 字段 | 含义 |
|---|---|
| `case_id` | 输入 `case.json` 的 `case_id`（缺失时用输入目录名） |
| `task_family` | 固定 `apparatus-route` |
| `workorder` | 固定 `apparatus_and_technical_route` |
| `status` | `completed` / `partial` / `input_error` |
| `figure` | `figure_id`、`mode`、组件/阶段/决策数、`canvas_mm`、`panels` |
| `artifacts` | `role=editable/export/preview` 的真实文件清单 |
| `elements` | 组件/阶段/终点，逐项 `source {path, locator}` |
| `connections` | 连接：`from`、`to`、`medium`、`direction`、端口与来源 |
| `conditions` | 工况：`location`、`params`、来源 |
| `decisions` | 判据：`after`、`question`、`yes`、`no`、`criterion_source` |
| `checks` | SVG/覆盖/流向/导出检查的布尔结果与计数 |
| `sources` | 去重后的 `{doc_id, locator}` 证据清单 |
| `inputs` | 真实输入文件（相对路径、字节数、sha256） |
| `uncertainty` | 材料缺失、审计警告、推断元素 |
| `change_log` | 可复核的执行步骤与证据路径 |

题目原有 v0.3 字段 `elements` / `connections` / `conditions` / `decisions` /
`checks` 必须保留，不得被 media 字段替代。

## 3. 官方 media 字段

| 字段 | 结构 | 说明 |
|---|---|---|
| `status` | 字符串 | 与顶层 status 一致；`completed` 才表示全部闸门通过 |
| `editable_source` | `{path, format:"svg", paths[], panels, layers[]}` | 分层可编辑 SVG；每个节点/边/工况/决策有命名 `<g id>` |
| `export_artifact` | `{path, format, paths[], dpi, png?}` | 真实 PDF（首选）或 PNG；PDF 缺失时降级并写 `exceptions` |
| `content_map` | 数组 | 每个元素/连接/工况/决策一条：`id`、`kind`、`label`、`source`、`rendered`、`media:"svg"` |
| `compliance_checks` | 数组 | `{id, check, ok, evidence}`；来自 `scripts/audit.py` 的 C1–C10 |
| `exceptions` | 数组 | `{code, detail, impact, fallback?}`；partial/input_error 必须说明限制 |

`content_map` 的 `rendered` 由审计确认（对应 id 在 SVG 中存在命名组），
不是由模型自报。审计覆盖率为：

```
coverage = (已渲染且来源可解析的元素/连接/工况/决策数) / 总数
```

`completed` 要求 `coverage = 1.0`、C1–C10 全部 ok、PDF 与 PNG 文件头有效。

## 4. 状态与退出码

| 情况 | status | run_case 退出码 | 说明 |
|---|---|---|---|
| 全部闸门通过 | `completed` | 0 | SVG + PDF + PNG + result 全部真实存在 |
| 图已生成但有缺口 | `partial` | 0 | 缺来源/未渲染/导出失败等，写入 exceptions |
| 没有可用 spec 或输入不可读 | `input_error` | 0 | 仍写 result.json，列出缺失项 |
| 连 result.json 都无法写 | — | 2 | 只有这一种情况 exit 2 |

`partial` 不是失败：保留已生成的真实产物，逐条写 `exceptions` 和
`uncertainty`，不得用占位文件或抽取式保底稿冒充完成品。

## 5. 禁止项

- 不写 `expected-*`、不打包预生成答案；示例仅 `examples/case.json` +
  `examples/paper.txt`，且声明 synthetic。
- 不在 result 中伪造分数、运行时间、评审意见或未实际生成的导出件。
- 不把 `<image>` 位图当作“可编辑源文件”。
- `criterion_source` 缺失时只能写 `not_provided`，不得编造阈值。
