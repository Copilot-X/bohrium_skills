---
name: deck-template-adapter
description: Fit academic content onto a supplied presentation template — audit masters, layouts, fonts and theme colours, map slide roles to real layouts, build the deck and adopt the template package, then report fidelity and every font substitution. Use when the task requires template-compliant formatting.
---

# 学术 PPT 模板与格式适配（Template & Format Adaptation）

## 用途与触发

task.md 给出**模板**（`.pptx`/`.potx`）并要求"按模板做""适配格式""套用母版"时使用。
组织方口径（硬约束）：`actual master/layout-based editable template`；
**模板的母版、版式、主题、字体、配色必须真实被采用**，不是"看起来像"。

## 输入

- `/input/task.md`：模板要求、页数、版式规定（标题页/章节页/内容页）、字体下限
- `/input/case.json`：`case_id`、`template`、`source_materials[]`、`output_contract`
- 模板文件：`.pptx` / `.potx`
- 内容：论文/提纲/数据

## 执行步骤

1. **模板审计**：`/app/work/template-audit.json`
   ```bash
   python3 /skills/weekly-skill/scripts/pptx_edit.py inspect materials/template.pptx --json
   ```
   记录：`slide_size`、`aspect_ratio`、母版数、每个版式的名称与占位符、
   主题字体（major/minor）、主题配色、媒体文件。
2. **可用性判定**：
   - 版式齐全（标题页 + 内容页 + 章节页）→ 可直接映射；
   - 只有母版没有内容版式 → 用最接近版式 + 记录 `layout_fallback`；
   - 模板损坏/无主题 → 停止套用，按默认主题生成并 `status=partial`。
3. **角色 → 版式映射**：`/app/work/layout-map.json`
   ```json
   {"mapping": [
     {"role": "title", "layout": "Title Slide", "placeholders": ["title", "subtitle"]},
     {"role": "section", "layout": "Section Header", "placeholders": ["title"]},
     {"role": "content", "layout": "Title and Content", "placeholders": ["title", "body"]}],
    "fallbacks": []}
   ```
   映射必须基于审计出的**真实版式名**，不凭模板常见命名猜测。
4. **字体核查**：
   ```bash
   font-discovery --list | head -50
   ```
   模板字体不在系统 → 选同族替代（衬线↔衬线、无衬线↔无衬线、等宽↔等宽），
   写入 `font_substitution`：`{original, substitute, reason, where}`。
5. **内容适配**：按模板版式容量重排内容——
   - 标题长度受版式宽度限制，超长先压缩措辞，不缩字号；
   - 正文字号 ≥ 模板规定下限（通常 18–20 pt）；
   - 图片按占位符比例裁切，不拉伸。
6. **生成基础 deck**：
   ```bash
   python3 /skills/weekly-skill/scripts/make_pptx.py \
     --spec /app/work/deck-spec.json --out /app/work/deck_base.pptx --json
   ```
   `make_pptx.py` 把标题/副标题/正文写成**真实占位符**
   （`<p:ph type="title|ctrTitle|subTitle">` / `<p:ph idx="1">`），
   不是一排绝对坐标文本框；基础 deck 自带 fallback 几何，套模板后由版式接管。
7. **套用模板**（关键步骤）：
   ```bash
   python3 /skills/weekly-skill/scripts/pptx_edit.py apply-template /app/work/deck_base.pptx \
     --template materials/template.pptx --layout "Title and Content" \
     --out /app/output/deck.pptx --json
   ```
   这一步不是只改 `slideLayout` 关系：脚本复制模板的母版/版式/主题，
   修正 presentation 的母版 `r:id`（避免悬空关系），并把 slide 中带
   `<p:ph>` 的形状的显式 `<a:xfrm>` 删除，使**几何由模板版式真实继承**。
   返回 `fidelity` 与 `placeholder_inheritance`：
   - `master+layouts+theme` = 完整继承；
   - `theme-only` = 只继承主题（模板多母版/多主题时），必须在 `uncertainty` 说明；
   - `placeholder_inheritance.count` = 真正交给版式定位的占位符数；
     为 0 表示模板没有可匹配的占位符，必须在 `format_check.violations` 记录，
     不能声称"已套用版式"；
   - `ok=false` = 套用会破坏包，已自动回滚，此时交付基础 deck 并 `status=partial`。
8. **格式合规核对**：`/app/work/format-check.json`
   ```json
   {"slide_size_match": true, "layouts_used": ["Title Slide", "Title and Content"],
    "fonts_used": ["Liberation Sans"], "fonts_outside_template": [],
    "min_font_pt": 18, "theme_colors_used": ["1F3864", "595959"],
    "violations": []}
   ```
9. **渲染核对**：`office-renderer` → PDF → `pdf-render-tool` 出 PNG；
   检查标题未被版式裁切、图片未压字、页脚完整。
10. **落盘**：`deck.pptx`、`template-audit.json`、`layout-map.json`、
    `format-check.json`、`preview/`、`result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "template-adaptation",
  "status": "completed|partial",
  "deck": {"path": "deck.pptx", "slide_count": 12,
           "template": "materials/template.pptx",
           "template_fidelity": "master+layouts+theme",
           "placeholder_inheritance": {"count": 24,
                                       "unique": ["title:None", "body:1"]},
           "aspect_ratio": "16:9"},
  "artifacts": [
    {"role": "editable", "path": "deck.pptx", "format": "pptx"},
    {"role": "audit", "path": "template-audit.json", "format": "json"},
    {"role": "mapping", "path": "layout-map.json", "format": "json"},
    {"role": "render", "path": "preview/deck.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/slide-01.png", "format": "png"}
  ],
  "template": {
    "slide_size": {"cx": 12192000, "cy": 6858000},
    "masters": 1,
    "layouts": [{"name": "Title Slide", "placeholders": ["title", "subtitle"]}],
    "theme_fonts": ["Liberation Sans"],
    "theme_colors": ["1F3864", "595959"]
  },
  "font_substitution": [
    {"original": "Arial", "substitute": "Liberation Sans",
     "reason": "Arial 不在评测镜像字体清单", "where": "theme.minor"}
  ],
  "format_check": {"slide_size_match": true, "fonts_outside_template": [],
                   "min_font_pt": 18, "violations": [],
                   "layouts_used": ["Title Slide", "Title and Content"]},
  "checks": {"zip_valid": true, "relationships_resolved": true,
             "page_count_match": true, "render_ok": true},
  "uncertainty": [],
  "change_log": []
}
```

## 适配规范

- 版式只减不增：不新造版式，不把母版元素复制到页面里"伪造"。
- 颜色：优先用主题色；确需新色时记录 HEX 与用途，不引入与主题冲突的高饱和色。
- 字体：全文不超过 2 个字体族；标题/正文按模板定义，不自定义混搭。
- 字号：标题 ≥ 模板规定；正文 ≥ 18 pt；页脚 ≥ 10 pt。
- 图片：保持原始宽高比；占位符裁切用 `object-fit: cover` 语义，不拉伸。
- 页脚/页码：沿用模板位置，不遮挡内容。
- 图表：从源材料重新导出高分辨率版本，不放大位图。

## 异常处理

| 情况 | 处理 |
|---|---|
| 模板只有 1 个版式 | 全部内容用该版式，记 `layout_fallback`，`status=partial` |
| 模板字体全部缺失 | 统一替代并写 `font_substitution`，正文用系统无衬线 |
| 模板与要求比例冲突 | 以 task.md 为准；无规定则以模板为准 |
| 套用后包损坏 | 自动回滚，交付基础 deck，`fidelity: "none"`，说明原因 |
| 占位符容量不足 | 缩短文案，不缩字号；仍不足则拆页 |
| 渲染工具不可用 | 交付 pptx + 结构校验，不伪造预览 |

## 自检

- [ ] 版式名来自真实审计，不是猜测
- [ ] `template_fidelity` 如实填写
- [ ] `placeholder_inheritance.count > 0`，且能在 deck 中查到 slide 占位符无显式 xfrm
- [ ] presentation 的 `sldMasterId r:id` 在 rels 中真实存在（无悬空关系）
- [ ] 字体替换表完整，无模板外字体残留
- [ ] 字号不低于下限
- [ ] 图片未拉伸、未遮挡文字
- [ ] `verify` 通过，无悬空关系
- [ ] 有真实渲染预览

## 最小示例（示例）

输入：模板（1 母版 / 11 版式 / Arial 主题）+ 论文 12 页内容。
输出：`layout-map.json` 3 个角色映射；`font_substitution` 1 条（Arial→Liberation Sans）；
`apply-template` 返回 `master+layouts+theme`；`format_check.violations = []`；
`preview/deck.pdf` 12 页；`status: "completed"`。
## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/inspect_input.py` | 盘点论文、模板与旧 deck |
| `scripts/make_pptx.py` | 按页级计划生成可编辑演示文件 |
| `scripts/pptx_edit.py` | 编辑现有 deck 并保留版式 |
| `scripts/validate_result.py` | 校验页级结构、证据与 result.json |
| `references/05-slides.md` | 幻灯片模板与格式规范 |
| `references/08-quality-gates.md` | 交付质量闸门 |
| `templates/result_envelope.json` | result.json 信封示例 |

## 本题交付对照

- 可编辑演示文件：页级结构、主张、图表、讲稿/备注和模板结果；完成标准：能围绕论文主线讲清楚。
- 页级证据映射：每页内容对应论文图、表、章节或数据；完成标准：引用准确，未改写事实。
- 渲染与版本记录：源文件、导出件、字体/模板和修改记录；完成标准：可打开、播放、编辑并复核。
