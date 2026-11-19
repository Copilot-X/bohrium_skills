---
name: graphical-abstract-designer
description: 图形摘要：围绕一个可证据支持的核心结论，组织 3–5 个角色化面板，交付可编辑 SVG、真实 PDF/PNG、来源映射和覆盖审计。Design a source-bound graphical abstract with one take-home claim, an editable SVG, real PDF/PNG exports and a deterministic audit. Use for 图形摘要、视觉摘要、TOC 图、cover graphic。
---

# Graphical Abstract（图形摘要）

## 用途与触发

task.md 要求“图形摘要 / visual abstract / TOC 图 / cover graphic”时使用。
组织方 workorder 为 `graphical_abstract`。目标只有一句话：

**让读者在 5 秒内看懂“做了什么、发现了什么”。**

评分看四要素（研究问题—方法—核心结果—结论）、实体/关系/数据编码、来源映射、
可编辑源文件与真实导出件。图的精美程度不是判据。

## 唯一入口

Agent 不拼脚本顺序，只调用 `scripts/run_case.py`：

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
python "$SKILL_DIR/scripts/run_case.py" \
  --input /input --out /app/output --work /app/work --json
```

入口内部：输入盘点 → spec 归一化 → SVG 渲染 → PDF/PNG 导出 → 覆盖审计 →
`result_graphics.py` 写 `result.json`。partial / input_error 也写
`result.json` 并退出 0；只有完全无法启动才退出 2。

## 支持文件

| 文件 | 用法 |
|---|---|
| `references/output-contract.md` | result.json 字段、status 词表、退出码 |
| `references/ga-claim-rules.md` | 单一 claim、3–5 元素、关系与版式规则 |
| `templates/ga-spec.schema.json` | `/app/work/ga-spec.json` 的 JSON Schema |
| `scripts/run_case.py` | 唯一入口；官方字段全部由它编排生成 |
| `scripts/svg_gen.py` | spec 归一化（id/canvas/relations）并调用渲染器 |
| `scripts/ga_render.py` | 确定性渲染实现：角色面板、几何图标、claim banner、关系箭头 |
| `scripts/export.py` | 真实 PDF/PNG：cairosvg/rsvg/Inkscape，缺失时 matplotlib 图元重绘 |
| `scripts/audit.py` | SVG XML/文字/外部引用/id 覆盖/导出文件头检查 |
| `scripts/result_graphics.py` | 生成 status、editable_source、export_artifact、content_map、compliance_checks、exceptions |
| `examples/case.json` + `examples/paper.txt` | 可运行合成示例（**synthetic，非正式答案**） |

## 执行步骤

### 1. 读真实输入

读 `/input/task.md` 与 `/input/case.json`，确认 `case_id`、期刊尺寸/字体下限、
语言、材料路径。`run_case.py` 会把真实文件清单写到 `/app/work/inventory.json`；
来源路径只能取清单中存在的文件，找不到就写 `uncertainty`。

### 2. 写绘图 spec

读论文/材料后，按 `templates/ga-spec.schema.json` 写 `/app/work/ga-spec.json`。
规则见 `references/ga-claim-rules.md`。最小形态：

```json
{
  "case_id": "与 case.json 一致",
  "claim": {
    "claim": "Surface oxygen vacancies keep 92% selectivity at 350 °C",
    "support": [{"path": "paper.txt", "locator": "Abstract; Fig.2a"}],
    "numbers": [{"value": "92", "unit": "%", "source": "Fig.2a"}],
    "not_claimed": ["工业可行性"]
  },
  "elements": [
    {"id": "G1", "role": "problem", "concept": "高温下选择性下降",
     "source": {"path": "paper.txt", "locator": "Abstract"}, "kind": "reported"},
    {"id": "G2", "role": "approach", "concept": "氧空位调控吸附",
     "source": {"path": "paper.txt", "locator": "Results"}, "kind": "reported"},
    {"id": "G3", "role": "result", "concept": "350 °C 下 92% 选择性",
     "source": {"path": "paper.txt", "locator": "Fig.2a"}, "kind": "reported"}
  ],
  "canvas": {"width_px": 1200, "height_px": 600, "dpi": 300},
  "journal_spec": {"min_font_pt": 8, "source": "task.md"}
}
```

- 只选一个主结论；并列结论进 `not_claimed`。
- 3–5 个元素；每个元素有 `source.path` + `locator`；数值只保留 1–2 个。
- `relations` 可省略，缺省为相邻面板之间的 `E1..E(n-1)`。
- 没有数据支撑时用 `kind: "schematic"`，不得画具体数值柱状/饼图。
- 没有 spec 时入口会从真实 `case.json`/`paper.txt` 推导，并在
  `uncertainty` 中标注；正式提交前应尽量由 Agent 自己写 spec。

### 3. 运行入口

```bash
python "$SKILL_DIR/scripts/run_case.py" \
  --input /input --out /app/output --work /app/work --json
```

产物：

- `/app/output/figures/ga.svg`：根 `<svg>` + `viewBox`，文字全为 `<text>`，
  每个元素 `<g id="G*">`、每个关系 `<g id="E*">`，无脚本/外部引用。
- `/app/output/figures/ga.pdf`、`/app/output/preview/ga.png`：真实导出。
- `/app/output/result.json`：官方 media 字段由 `scripts/result_graphics.py` 生成。
- `/app/work/coverage.json`：`scripts/audit.py` 原始报告，`coverage` 必须 1.0。

### 4. 视觉复核

真的打开 `preview/ga.png`（或 PDF）检查：claim 是否一句、元素是否 3–5 个、
箭头方向是否正确、数值/单位是否与来源一致、缩到 240–300 px 是否仍可读。
发现问题改 `/app/work/ga-spec.json` 后重跑，不要靠缩小字号硬塞。

只有实际看过预览后，才可重跑并加：

```bash
python "$SKILL_DIR/scripts/run_case.py" ... --visual-reviewed
```

## 输出契约

`result.json` 必须含 `case_id`、`status`、`task_status` 与六个 media 字段：

- `editable_source`：`{path: "figures/ga.svg", format: "svg"}`
- `export_artifact`：真实 PDF（或 PNG）；没有导出时为 `null` 且 status=partial
- `content_map`：每个 `G*` 元素与 `E*` 关系到 `path + locator` 的映射
- `compliance_checks`：XML/文字/外部引用/导出签名/dpi/单 claim/元素数/coverage
- `exceptions`：失败与降级原因；`uncertainty`：尺寸默认、spec 推导等
- `status`：`answered | partial | input_error`

完整字段与语义见 `references/output-contract.md`。Agent 不得手写这些字段。

## 异常处理

| 情况 | 处理 |
|---|---|
| `/input` 或 `case.json` 缺失 | `input_error`，仍写 result.json，退出 0 |
| 材料缺 claim/数值 | `partial` + `claim_missing`；不补造 |
| 元素缺来源 | 补 source 或删除；审计报 `MISSING_IDS`/缺来源 |
| 期刊尺寸未给 | 默认 1200×600 px @300 dpi，写入 `uncertainty` |
| PDF/PNG 导出失败 | 保留 SVG，`partial` + `export_unavailable` |
| SVG 有外部引用/脚本 | 重渲染；审计报 `SVG_NOT_SELF_CONTAINED` |
| 缩略图不可读 | 减元素/缩短 claim，不缩小字号 |
| 只有一个位图成图 | 用几何示意重绘，标 `schematic` |

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 单一 claim | 一句话概括全图，每词有出处 |
| G2 | 元素数 | 3–5 个，阅读顺序明确 |
| G3 | 证据 | 每个元素/数值有 path + locator |
| G4 | 图面 | 标签极简、箭头语义正确、字号 ≥ 期刊下限 |
| G5 | 缩略图 | 240–300 px 宽仍可读主结论 |
| G6 | 导出 | SVG 可解析；PDF/PNG 文件头有效、尺寸真实 |
| G7 | 审计 | `coverage=1.0`，无 `MISSING_IDS`/外部引用/脚本 |
| G8 | 契约 | 六个 media 字段齐全，`compliance_checks` 为 object |

## 示例

`examples/case.json` + `examples/paper.txt` 是一条可运行的**合成**样例
（synthetic，不是正式答案，也不代表任何真实研究结论）：

```bash
python "$SKILL_DIR/scripts/run_case.py" \
  --input "$SKILL_DIR/examples" --out /tmp/ga-out --work /tmp/ga-work --json
```

预期：3 个元素（problem/approach/result）+ 2 条关系；SVG/PDF/PNG 真实生成；
`status=answered`、`coverage=1.0`。示例输出只用于说明接口。

## 版本与限制

- 版本 0.14.0：单入口 + 5 个脚本 + 2 份参考 + 1 个 schema + 合成示例（12 文件）。
- 不依赖网络；不调用 python-pptx；导出优先 cairosvg/rsvg/Inkscape，
  缺失时用 matplotlib 重绘 SVG 图元，失败则诚实降级为 partial。
- 科学判断仍由 Agent/评审完成；本 Skill 只保证来源映射、可编辑性与导出规范。
