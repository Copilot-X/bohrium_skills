# 学术 PPT 赛道通用流程（Slides Playbook）

适用于：论文转 PPT、PPT 生成与编辑、汇报内容组织、模板与格式适配。

## 0. 一句话原则

**一页一个主张，每个主张都有出处。** 评分表的三项产物要求是：可编辑演示文件、
页级证据映射、渲染与版本记录；并且明确"技术性文件检查不证明演示的科学性与质量"。

## 1. 输入盘点

1. `/input/task.md`：受众、时长、页数上限、语言、必须覆盖的内容、模板要求、讲稿要求。
2. `/input/case.json`：`case_id`、`template_id`、`deck_id`、起始稿路径、`workorder`。
3. 模板：母版/版式（`.pptx` 或 `layout.json`）、字体清单、配色、占位符定义。
4. 源材料：论文全文/图表、数据、已有 deck（若要求"在起始稿上改"）。
5. 生成 `/app/work/deck-spec.json`，再生成文件。

## 2. 叙事结构（Storyline）

先定主线，再排版：

```json
{
  "audience": "领域同行（15 分钟组会）",
  "duration_min": 15,
  "target_slides": 12,
  "throughline": "高温下催化剂 A 的选择性优势来自表面氧空位",
  "slides": [
    {"no": 1, "role": "title", "title": "…", "claims": []},
    {"no": 2, "role": "motivation", "title": "问题：选择性随温度下降",
     "claims": ["F010"], "visual": "Fig.1b"},
    {"no": 6, "role": "result", "title": "A 在 350 °C 保持 92% 选择性",
     "claims": ["F001", "F004"], "visual": "Fig.2a", "notes": "讲稿 40 秒"}
  ]
}
```

规则：

- `role` 取 `title / outline / motivation / method / result / discussion / limitation / conclusion`。
- 每页 `claims` 至少一条（title/outline 除外）；`visual` 必须指向真实存在的图/表。
- 页数服从 task.md；超限时先砍 `method` 细节，不砍 `result` 与 `limitation`。
- 15 分钟 ≈ 12–15 页，每页讲稿 30–60 秒。

## 3. 页面写作规范

- 标题是**结论句**，不是话题词（"A 在 350 °C 保持 92% 选择性" 优于 "结果"）。
- 每页 ≤ 6 行、每行 ≤ 12 词；细节进讲稿，不堆在页面。
- 数字与单位与源材料完全一致；不得为了排版改动数值。
- 图表用原始图，不二次渲染走样；必须裁切时保留轴标签与图例。
- 引用格式统一：页脚 `Author et al., Journal Year, Fig.X` 或编号 `[12]`。
- 讲稿（notes）写"为什么这页重要 + 怎么过渡"，不重复页面文字。

## 4. 模板适配

1. 读模板的版式清单（`slide_layouts`）与占位符（`title`、`body`、`pic`、`notes`）。
2. 建 `layout-map.json`：`role → layout_name`，并记录占位符索引。
3. **不得**新增模板外字体；字体缺失时用 `font-discovery` 找替代并记录替换表。
4. 保留母版的配色主题与页脚；不自创版式。
5. 若要求"在起始稿上编辑"：保留原页码与节结构，改动逐页记录 before/after。

## 5. 生成

- 主产物：`deck.pptx`（可编辑，母版/版式结构保留）。
- 生成器：`scripts/make_pptx.py`（纯标准库 OOXML，不依赖 python-pptx）。
  输入为 `deck-spec.json`，输出 `.pptx` + 结构报告。
- 编辑/巡检：`scripts/pptx_edit.py`
  （`inspect` 审计母版版式主题 / `list` 列出每页文本 run / `set-text` 精确改 run /
  `set-notes` 改讲稿 / `set-image` 换图 / `apply-template` 套用真实母版版式 /
  `verify` 校验包与关系）。编辑只重写点名的 `<a:t>`，其余 XML 原样保留。
- 生成后**回读校验**：
  ```bash
  python3 /skills/weekly-skill/scripts/pptx_edit.py verify /app/output/deck.pptx
  python3 /skills/weekly-skill/scripts/validate_artifacts.py --result /app/output/result.json
  ```
  `verify` 检查：zip 完整、`ppt/presentation.xml` 与每页 `slideN.xml` 可解析、
  页数、每页有标题文本、图片与关系目标存在；`validate_artifacts` 检查交付契约
  （`case_id`/`status`/`artifacts`、路径不越界、视觉件有预览）。
- 预览：用 `office-renderer`（LibreOffice）导出 `preview/deck.pdf`，再 `pdf-render-tool`
  出 `preview/slide-01.png…`。渲染失败必须在 `status` 与 `uncertainty` 中说明。

## 6. 输出契约

```json
{
  "case_id": "…",
  "deck": {
    "path": "deck.pptx",
    "slide_count": 12,
    "template_id": "…",
    "aspect_ratio": "16:9",
    "language": "zh"
  },
  "slides": [
    {"no": 6, "title": "A 在 350 °C 保持 92% 选择性", "role": "result",
     "claims": [{"claim_id": "F001", "text": "…",
                 "source": {"path": "materials/paper.pdf", "locator": "Fig.2a"}}],
     "visual": {"path": "materials/fig2a.png", "source_locator": "Fig.2a"},
     "notes": "讲稿…", "layout": "Title and Content"}
  ],
  "artifacts": [
    {"role": "editable", "path": "deck.pptx", "format": "pptx"},
    {"role": "render", "path": "preview/deck.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/slide-06.png", "format": "png"}
  ],
  "checks": {"zip_valid": true, "slides_parse": true, "page_count_match": true,
             "fonts_declared": ["Liberation Sans"], "template_layouts_used": 4},
  "uncertainty": [{"item": "第 9 页示意图", "reason": "论文无对应图", "impact": "改为文字要点"}],
  "change_log": [{"slide": 6, "action": "改写标题为结论句", "before": "结果", "after": "…"}]
}
```

## 7. 异常处理

| 情况 | 处理 |
|---|---|
| 模板占位符不足 | 用最接近的版式 + 文本框补齐，记录 `layout_fallback` |
| 图表无法从 PDF 提取 | 用 `pdf-render-tool` 整页渲染后裁切，标注来源页 |
| 页数超限 | 合并 method 细节页，保留结果与局限，记录合并映射 |
| 字体缺失 | 替代 + `font_substitution` 表 |
| 起始稿损坏 | 重建同结构 deck，`status=partial` 并说明 |
| 渲染工具不可用 | 交付 pptx + 结构校验报告，明确"未渲染"，不伪造预览 |

## 8. 自检

- [ ] 每页标题是结论句，且能指回 claim
- [ ] 页数、时长、受众符合 task.md
- [ ] 数值/图与源材料一致，未二次加工走样
- [ ] pptx 能被解析，页数与规格一致
- [ ] 有真实渲染的 PDF/PNG 预览
- [ ] 模板字体与版式未被破坏
