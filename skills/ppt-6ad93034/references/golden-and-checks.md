# 页级金标准与检查

`scripts/gold.py` 生成 `gold.json` 与 `page-diff.json`；`scripts/checks.py` 生成
`checks.json` 与 `result.json` 的 `checks/compliance_checks/exceptions`。
两者都是**审查合同**，不是预制答案；每页的 claim 仍来自运行时源材料。

## 1. 页级金标准（gold.json）

每页记录：

```json
{
  "index": 6,
  "role": "result",
  "title": "Ni-Co 在 750 °C 维持 92% 转化率",
  "bullets": ["..."],
  "notes_required": true,
  "figure_required": true,
  "figure": "/input/materials/fig2a.png",
  "evidence": [
    {"claim": "92% at 750 °C", "path": "materials/paper.pdf",
     "locator": "p.5", "verified_in_source": true}
  ],
  "layout": "content"
}
```

`evidence.verified_in_source` 用源文全文做规范化子串回查；查不到就为 false，
并在 diff 中失败。`gold.json` 只描述“应有什么”，不写入任何正式案例答案。

## 2. 逐页 diff（page-diff.json）

对每页比较：页面存在、标题出现、备注非空、bullet 命中数、图是否嵌入、
证据是否可回查、是否进入 `content_map`。汇总：

```json
{"pages": 10, "passed": 10, "failed": 0, "pass_rate": 1.0}
```

失败页必须保留在 `page-diff.json`，不得只写“全部通过”。

## 3. 结构检查（checks.py）

| 检查键 | 通过标准 | 失败级别 |
|---|---|---|
| `zip_valid` | zip 可解、所有 XML 可解析、必需 part 存在 | error |
| `relationships_resolved` | 每个 `.rels` 内部 target 都存在 | error |
| `slide_count_match` | 实际页数 = spec 页数 = workorder/task 页数 | error |
| `notes_complete` | 每页 notes slide 存在且非空 | error |
| `images_resolved` | 所有图片关系可解析、源图存在 | warning |
| `placeholder_junk` | 无 Click to edit / Lorem / TODO / 占位符 | error |
| `spec_conformance` | 每页标题与至少一条 bullet 出现在 PPTX | warning |
| `numbers_verified` | 内容页数字都能在源文找到 | error |
| `evidence_coverage` | ≥80% 内容页有 path + locator | warning |
| `text_overflow_risk` | 估算行高不超过文本框 108% | warning |
| `visual_reviewed` | PDF + PNG 数量 ≥ min(3, 页数) | 参与状态 |
| `render_available` | PDF 签名有效且 PNG 有效 | 参与状态 |
| `required_roles_present` | 覆盖 deck-parts 必需角色 | warning |
| `edit_scope_ok` | 无 unresolved edits、编辑后结构有效 | error |
| `template_fidelity` | `master+layouts+theme` / `starting-deck-preserved` / `none` | 记录 |

数字检查只扫**主张性文本**：标题、bullets、stats、steps、cards、callout、quote。
页脚定位（`line 12`、`p.5`、`slide 3`）、页码、页码式序号不参与，避免误报。
比较时统一逗号、空格、`μ/µ`，允许 `92.0` 与 `92` 等价。

## 4. 溢出估算

按文本框宽度、字号、字符宽度（CJK 记 2）估算行数与所需高度：
`needed_pt = lines × font_pt × 1.22 + 6`。超过框高 108% 记 warning。
修复优先级：压缩 bullet → 减少 bullet → 拆页；不要缩到 9pt 以下。

## 5. 状态映射

| 条件 | status | task_status |
|---|---|---|
| 有可编辑 PPTX + 有效 PDF/PNG + 无 hard error + 无 unresolved edit | answered | completed |
| 缺渲染、缺页、缺备注、数字未回查、edit 未解决、结构错误 | partial | partial |
| 无 case/task/材料/编辑指令，无法产出 | input_error | input_error |

`partial` 必须写 `status_reason`、`exceptions`、`uncertainty` 和 `limitations`。
`input_error` 也必须写完整 `result.json` 并 exit 0。

## 6. 官方 media 字段

入口必须生成并由 `compliance_checks` 自洽：

- `status`：answered / partial / input_error。
- `editable_source`：`presentation.pptx` 的 path/format/sha256/bytes/slide_count。
- `export_artifact`：PDF 的 path/format/pages/engine/native；不可用时为 null 并写 limitation。
- `content_map`：每页 claim → `{path, locator}`；图片也必须有源定位。
- `compliance_checks`：`pptx_valid / slide_count_match / notes_complete /
  export_signature / template_fidelity / numbers_verified / evidence_coverage /
  render_available`。
- `exceptions`：`{code, severity, detail}`，severity ∈ error/warning/limitation。

## 7. 交付前人工复核

1. 打开 `preview/presentation.pdf` 逐页看：标题不裁切、正文不溢出、图注对得上。
2. 打开 `presentation.pptx` 抽查 2 页：文字可编辑、备注在、版式/主题未被覆盖。
3. 抽查 3 个数字：在 `content_map` 找到 path+locator，再回源文核对。
4. 查看 `page-diff.json` 是否有 failed；不得隐藏。
5. 若 `render.native=false`，在报告里写明原因是“无 office 渲染器”，不得写成原生导出。
