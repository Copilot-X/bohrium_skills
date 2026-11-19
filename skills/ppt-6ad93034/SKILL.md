---
name: academic-deck-editor
description: 学术 PPT 生成、模板适配与最小编辑。读取真实 task/case/material/workorder，从论文、数据表和真实图片规划页序，生成可编辑 PPTX、真实 PDF/PNG 预览、逐页证据映射与检查记录；保留母版、版式、主题与未点名页面。Use for 学术汇报、PPT 生成、做 slides、PPT 改稿、模板套用、幻灯片修订、deck generation/adaptation/editing.
---

# 学术 PPT：生成、模板适配与最小编辑

把真实论文/材料变成可讲、可编辑、可回查的学术幻灯片。一个入口完成发现、
规划、装配、预览、检查与结果契约；正文与页面取舍由模型或源材料证据决定，
脚本只做装配与校验。

## 快速开始

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output/preview

# 1) 先跑一次：发现输入并写 /app/work/evidence-pack.json
python3 "$SKILL_DIR/scripts/main.py" --input /input --output /app/output --work /app/work

# 2) 推荐：模型读取 evidence-pack，写 /app/work/deck-spec.json（见下方 schema）
#    然后再跑一次，用模型写的正文装配最终稿
python3 "$SKILL_DIR/scripts/main.py" --input /input --output /app/output --work /app/work

# 3) 可选：显式指定
python3 "$SKILL_DIR/scripts/main.py" --input /input --output /app/output --work /app/work \
  --spec /app/work/deck-spec.json --template /input/materials/target-template.pptx
```

`scripts/main.py` 是唯一入口。它在同一进程内调用 `scripts/discover.py`
（输入与证据）、`scripts/build.py`（spec 校验/规划）、`scripts/deckkit.py`
（OOXML 装配/编辑/预览）、`scripts/checks.py`（结构/证据检查）和
`scripts/gold.py`（页级金标准与 diff）。不要分步手工调用这些模块。

## 输入与模式

优先读实际文件，不按文件名猜内容：

- `/input/task.md`：受众、时长、页数、语言、比例、必须覆盖内容。
- `/input/case.json` 与 `/input/material.json`：`case_id`、模板/起始稿路径、
  `output_contract`；**原有根字段原样保留到 result.json**。
- `/input/artifacts/slide-workorder.json`：正式逐页/逐条工单，优先于文件名。
- `/input/materials/**`：PDF/TXT/MD/DOCX/PPTX/CSV/XLSX、真实图片、模板、起始稿。

| 信号 | `workflow_mode` | 行为 |
|---|---|---|
| workorder 有 edits | `edit` | 起始稿只读，只改点名项 |
| 起始稿 + 目标模板 | `adapt` | 内容保留，真实母版/版式/主题 |
| 只有目标模板 | `adapt` | 在模板上生成 |
| 无起始稿/模板 | `generate` | 从材料规划新稿 |

“可编辑 PPTX”是格式要求，不等于 edit 模式。模板/起始稿缺失时退回 generate，
并在 `uncertainty` 记录，不静默假装适配成功。

## 页面规划（详见 `references/slide-planning.md`）

1. 抽研究问题、方法、结果、讨论、局限、结论；每条保留文件 + 定位。
2. 默认 10–14 页，按 task/workorder 页数为准；结果页 ≥20%，方法页 ≤35%。
3. 一页一个结论；标题是 action title；每页 3–5 条要点，每条只含一个 claim。
4. 每页至少一个视觉元素：真实图、统计卡、步骤卡、对照卡或要点。
5. 图只能来自真实图片，或从真实数据表重绘；缺图写 `unavailable`，禁止占位图。
6. 每个内容页写 `notes`（2–4 句讲稿）和 `claims`/`source_ref`（path + locator）。
7. 数字、单位、图号、条件与源文逐字一致；无法回查的数字删除或标 `unavailable`。
8. 语言跟随 task；翻译时保留数字、单位、专有名词，`claims.source` 指向原文。

## deck-spec.json（模型写正文的接口）

写到 `/app/work/deck-spec.json`；重新运行入口即生效。

```json
{
  "title": "汇报题目",
  "size": "16:9",
  "theme": {"name": "scholar"},
  "slides": [
    {"kind": "title", "role": "title", "title": "题目", "subtitle": "组会 · 10 分钟",
     "source": "paper.pdf", "notes": "开场说明问题与主线。",
     "source_ref": {"path": "materials/paper.pdf", "locator": "p.1"}},
    {"kind": "content", "role": "background", "title": "研究缺口",
     "bullets": ["源文句子 1", "源文句子 2"],
     "notes": "讲稿：……。依据：paper.pdf p.2。",
     "source": "paper.pdf · p.2",
     "source_ref": {"path": "materials/paper.pdf", "locator": "p.2"},
     "claims": [{"claim": "…", "source": {"path": "materials/paper.pdf", "locator": "p.2"}}]},
    {"kind": "steps", "role": "method", "title": "方法路线",
     "steps": [{"title": "步骤", "text": "做法与条件"}], "notes": "…"},
    {"kind": "stat", "role": "result", "title": "关键结果",
     "stats": [{"value": "92%", "label": "转化率"}],
     "image": "materials/fig2a.png", "caption": "Fig. 2a · p.5（原图）",
     "notes": "…", "source_ref": {"path": "materials/paper.pdf", "locator": "p.5"},
     "figure_source": {"path": "materials/paper.pdf", "locator": "p.5"}},
    {"kind": "closing", "role": "conclusion", "title": "结论",
     "bullets": ["结论 1", "结论 2"], "notes": "…"}
  ]
}
```

可用 `kind`：title / section / content / steps / stat / comparison / cards /
image-left / quote / statement / closing。布局槽位与上限见
`references/deck-parts.json`；`scripts/build.py` 会按该文件校验并自动拆分过长
bullet、补 grounded notes、解析相对图片路径。

没有模型 spec 时，`scripts/build.py` 会生成 source-grounded 大纲，并在
`exceptions` 标 `auto_plan_origin`；优先用模型写的 spec。

## 模板与编辑纪律

- 有模板：`scripts/deckkit.py` 复制真实 `slideMasters/slideLayouts/theme`，
  按 layout 名称映射角色；不覆盖字体/配色，不新造同名版式。fallback 写入
  `template.layouts_used` 与 `template.fallback_reason`。
- edit：先复制起始稿，只改 workorder 点名的 text run / notes / image；
  未点名幻灯片字节不变，写入 `preserved.untouched_slides`。
- 文本替换优先按 `from` 精确匹配，其次 `target` 序号；找不到就写
  `unresolved_edits`，不新建元素冒充。
- 不支持的重排/删页/加页/跨格式换图不静默执行，如实记录。
- 一次只改点名项；正文变长先压缩或拆页，不缩到不可读。

## 输出与官方字段

`/app/output` 下至少包含：

- `presentation.pptx`：真实可编辑演示文件，含每页 notes 与图片。
- `preview/presentation.pdf` + `preview/slide-NN.png`：真实预览。优先
  LibreOffice/office-renderer 原生转换；无渲染器时用同一 spec 绘制内容预览，
  `render.native=false` 并写 `limitations`，不伪造原生导出。
- `result.json`：官方字段 `status`、`editable_source`、`export_artifact`、
  `content_map`、`compliance_checks`、`exceptions` 全部由入口生成；输入 case.json
  的原有字段保留。
- 记录：`plan.json`、`claim_source.json`、`time.json`、`change_log.json`、
  `render_report.json`、`checks.json`、`gold.json`、`page-diff.json`、
  `deck-spec.json`。

字段语义与完整 schema 见 `references/result-and-checks.md`。

## 检查、状态与失败处理

`references/golden-and-checks.md` 定义页级金标准、数字回查、溢出估算和状态映射。
交付前必须满足：zip/XML 可解析、关系无悬空、页数一致、每页 notes 非空、
无占位垃圾、数字可回查、图片可解析、PDF/PNG 有效。

| 情况 | 处理 |
|---|---|
| 无 case/task/材料/编辑指令 | `status=input_error`，写完整 result，exit 0 |
| 渲染器缺失 | 输出同源预览，`native=false` + limitation，status 可 answered/partial |
| 页数不足/超出 | 合并方法/讨论；保留 title/background/method/result/conclusion |
| 图缺失 | 文字/统计卡替代，`figure_status=unavailable`，不编图 |
| 编辑 target 不存在 | 记 `unresolved_edits`，不新建元素 |
| 模板损坏 | 退回独立版式，`template_fidelity=none` + fallback reason |
| 扫描 PDF 无文本 | 记 uncertainty；OCR 不可用时 partial |
| 未捕获异常 | 兜底写 `partial` + `internal_error`，exit 0 |
| 输出目录不可写 | 唯一 exit 2 的情况 |

## 支持文件

| 文件 | 用途 |
|---|---|
| `scripts/main.py` | 唯一入口：discover → plan → build/edit/adapt → preview → checks → gold → result |
| `scripts/discover.py` | 输入归一化、PDF/DOCX/PPTX/CSV 抽取、证据索引、图/表发现 |
| `scripts/build.py` | spec 校验、source-grounded 规划、真实数据表重绘、deck-parts 规则 |
| `scripts/deckkit.py` | 标准库 OOXML 生成/编辑、模板继承、PPTX 检查、PDF/PNG 预览 |
| `scripts/checks.py` | 结构/关系/页数/备注/图片/占位/数字/证据/溢出检查 |
| `scripts/gold.py` | 页级金标准 `gold.json` 与逐页 diff |
| `references/deck-parts.json` | 页面角色、槽位、上限与必需角色（代码实际读取） |
| `references/slide-planning.md` | 内容取舍、页序、图表与受众规则 |
| `references/golden-and-checks.md` | 金标准、检查阈值、状态映射、人工复核 |
| `references/result-and-checks.md` | result.json 完整契约、记录文件、失败策略 |

## 限制

- 不替代论文精读、统计推断或科学审稿；技术检查通过不等于科学结论正确。
- 无 LibreOffice 时 PDF/PNG 是内容预览，可能与 PowerPoint 原生渲染有差异。
- 扫描 PDF 无文本层时，需平台 OCR；不可用则如实 partial。
- 位图图表不能反推数值；只能引用原图，不能编造数据。
- 模板 layout 名称差异大时使用最接近版式，并记录 fallback。
- 不处理超出 PPTX 范围的宏、嵌对象动画与受保护母版。

## 最小示例（接口示例，不是正式答案）

输入：`task.md` 要求 8 页、16:9；`materials/paper.md` 与一张真实图。
执行：`scripts/main.py --input /input --output /app/output --work /app/work`。
输出：`presentation.pptx` 8 页、8 页 notes、真实图 1 张；`preview/` 下 8 页 PDF/PNG；
`content_map` 每页有 path+locator；`status=answered` 或按检查结果 `partial`。
示例只说明接口，不是任何正式案例的答案。
