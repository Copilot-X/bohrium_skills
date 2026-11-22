---
name: graphical-abstract-designer
description: Design a graphical abstract — one take-home claim, three to five visual elements in a left-to-right reading order, minimal labels, journal-compliant canvas — as editable SVG with a real rendered preview and an explicit claim-to-source map. Use when the task asks for a graphical abstract, cover figure or visual summary.
---

# Graphical Abstract（图形摘要）

## 用途与触发

task.md 要求"图形摘要""cover graphic""visual abstract""图文摘要"时使用。
组织方口径（硬约束）：workorder 为 `graphical_abstract`；
**必须交付真实可编辑的 SVG/PNG/PDF**。

图形摘要只做一件事：**让读者在 5 秒内看懂"做了什么、发现了什么"**。
它不能承载方法细节，也不能暗示论文没有的结论。

## 输入

- `/input/task.md`：期刊/尺寸（如 1200×600 px、13×5 cm）、字体下限、格式、语言
- `/input/case.json`：`case_id`、`workorder`、`paper`、`key_figures[]`
- 材料：摘要、结论段、主图、关键数值
- 可选：期刊模板/配色规范

## 执行步骤

1. **提炼一句话主张**：从摘要 + 结论中取**一个**可被数据支持的句子，
   写入 `/app/work/claim.json`：
   ```json
   {"claim": "Surface oxygen vacancies make catalyst A keep 92% selectivity at 350 °C",
    "support": [{"path": "materials/paper.pdf", "locator": "Abstract; Fig.2a; §4.1"}],
    "strength": "reported",
    "not_claimed": ["工业可行性", "成本优势"]}
   ```
   若结论有多个并列点，选最能代表全文的一个；其余不得挤进图里。
2. **选 3–5 个视觉元素**：按"问题 → 手段 → 结果"或"输入 → 过程 → 输出"编排：
   ```json
   {"elements": [
     {"id": "G1", "role": "problem", "concept": "选择性随温度下降",
      "source": "Fig.1b", "kind": "reported"},
     {"id": "G2", "role": "approach", "concept": "氧空位调控表面吸附",
      "source": "§3.2", "kind": "reported"},
     {"id": "G3", "role": "result", "concept": "350 °C 下 92% 选择性",
      "source": "Fig.2a", "kind": "reported"}]}
   ```
   超过 5 个元素就说明还没想清楚，删而不是缩小。
3. **定阅读顺序**：默认从左到右；竖向版式从上到下。
   元素之间用箭头/流程线连接，箭头方向即叙事方向（不表示因果时用无箭头分隔）。
4. **写规格** `/app/work/figure-spec.json`：
   - 画布按期刊要求（像素或 mm），留安全边距；
   - 每元素一个 `group`，节点少而有层级；
   - 文字只保留：1 句主张（顶部，≤ 12 词）+ 每元素 ≤ 3 个短标签；
   - 数值只放最关键的 1–2 个。
5. **生成**：
   ```bash
   python3 /skills/weekly-skill/scripts/make_schematic.py \
     --spec /app/work/figure-spec.json --out /app/output/figures/ga.svg --json
   ```
   需要示意图标（烧瓶、分子、细胞）时用基础几何图形组合，
   不贴位图、不用 emoji、不描摹受版权图。
6. **一致性核对**（关键）：
   - 图中每个数值/方向与正文一致；
   - 视觉暗示不超出证据（如只做了实验室实验，不画工厂）；
   - 颜色语义全文一致（同一物质同一颜色）；
   - 写入 `/app/work/consistency.json`。
7. **缩略图测试**：把图缩到 300 px 宽，检查：
   - 主张句仍可读；元素仍可辨；无糊成一团的细节。
   不通过则减少元素/放大字号，而不是加标注。
8. **校验与导出**：
   ```bash
   python3 /skills/weekly-skill/scripts/svg_lint.py /app/output/figures/ga.svg --json
   ```
   PNG（≥300 dpi 或按期刊像素）+ PDF；回读尺寸并记录。
9. **落盘**：`figures/ga.svg`、`figures/ga.pdf`、`preview/ga.png`、
   `claim.json`、`result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "graphical-abstract",
  "workorder": "graphical_abstract",
  "status": "completed|partial",
  "graphical_abstract": {
    "claim": "Surface oxygen vacancies keep 92% selectivity at 350 °C",
    "reading_order": "left-to-right",
    "elements": 3,
    "canvas": {"width_px": 1200, "height_px": 600, "dpi": 300},
    "journal_spec": {"min_font_pt": 8, "source": "task.md"}
  },
  "artifacts": [
    {"role": "editable", "path": "figures/ga.svg", "format": "svg"},
    {"role": "export", "path": "figures/ga.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/ga.png", "format": "png",
     "width_px": 1200, "height_px": 600, "dpi": 300}
  ],
  "elements": [
    {"element_id": "G1", "role": "problem", "concept": "…",
     "source": {"path": "materials/paper.pdf", "locator": "Fig.1b"},
     "kind": "reported"}
  ],
  "claim_support": [
    {"claim_part": "92% selectivity", "source": "Fig.2a", "value": "92", "unit": "%"}
  ],
  "not_claimed": ["工业可行性", "成本优势"],
  "consistency": {"numbers_match": true, "color_semantics_stable": true,
                  "no_overclaim": true, "thumbnail_legible": true},
  "checks": {"xml_valid": true, "text_as_text": true, "external_refs": 0,
             "export_from_source": true},
  "uncertainty": [],
  "change_log": []
}
```

## 设计规范

- **层级**：主张句 > 3–5 个元素 > 标签；任何细节都不应比主张更抢眼。
- **留白**：元素之间至少 1 个字高的间距；四周安全边距 ≥ 画布短边的 5%。
- **字号**：正文标签 ≥ 8 pt（或缩略图下仍可读）；主张句 1.3–1.6 倍标签字号。
- **配色**：≤ 5 色，色盲友好；同义同色；背景纯白或极浅灰。
- **箭头**：只表示流程/转化；纯并列关系用分隔线或留白。
- **文字**：不写完整句子（除主张句）；不用缩写除非全文通用。
- **禁止**：3D、阴影、渐变、poster 式堆叠、与结论不符的"应用前景"画面。

## 异常处理

| 情况 | 处理 |
|---|---|
| 论文有多个并列结论 | 选一个主结论，其余进 `not_claimed`，不硬塞 |
| 期刊尺寸未给 | 默认 1200×600 px @300 dpi，并在 `uncertainty` 说明 |
| 无合适主图可引用 | 用几何示意重绘，标 `schematic` |
| 需要期刊模板配色 | 按模板取色，缺色值则用中性色并记录 |
| 缩略图不可读 | 减元素/加字号，不靠放大画布解决 |
| 导出工具缺失 | 交付 SVG + 说明，`status=partial` |

## 自检

- [ ] 只有一句主张，且每个词都有出处
- [ ] 3–5 个元素，阅读顺序明确
- [ ] 数值与正文一致，未夸大
- [ ] 缩略图测试通过
- [ ] 字号、画布、格式符合 task.md
- [ ] SVG 文本可编辑，PNG/PDF 真实渲染
- [ ] `not_claimed` 明确写出边界

## 最小示例（示例）

输入：摘要 + Fig.1b + Fig.2a，期刊要求 1200×600 px。
输出：`ga.svg` 3 元素（问题/手段/结果）+ 1 句主张；
`preview/ga.png` 1200×600；`consistency` 4 项全 true；`status: "completed"`。
## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/inspect_input.py` | 盘点论文、图注与数据 |
| `scripts/make_schematic.py` | 生成分层 SVG/PNG/PDF 图 |
| `scripts/make_chart.py` | 生成数据图与误差线 |
| `scripts/svg_lint.py` | 检查 SVG 文本、实体与引用 |
| `scripts/validate_result.py` | 校验 result.json 与产物 |
| `references/04-graphics.md` | 图形摘要与视觉规范 |
| `references/08-quality-gates.md` | 交付质量闸门 |
| `templates/result_envelope.json` | result.json 信封示例 |

## 本题交付对照

- 可编辑源文件：示意图、机制图、流程图或图表的分层源文件；完成标准：实体、关系、图例和数据映射正确。
- 目标格式、尺寸、分辨率、字体和色彩检查；完成标准：可在目标场景打开和使用。
- 来源与修改记录：元素来源、版本、数据映射和人工确认；完成标准：关键图面可核验，异常有说明。
