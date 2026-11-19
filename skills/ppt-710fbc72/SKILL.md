---
name: deck-template-adapter
description: 学术 PPT 模板与格式适配：读取真实母版/版式/占位符/主题字体与页面尺寸，把模型写好的页级内容装进真实版式，或把起始 deck 逐页重定向到目标模板。交付可编辑 PPTX、真实 PDF/PNG 预览（无渲染器时如实说明）、逐页证据映射与可复核的 result.json。Use for PPT templates, master/layout adaptation, starting-deck retargeting, enterprise/conference templates, batch format consistency.
---

# 学术 PPT 模板与格式适配

## 用途与触发

输入包含真实 `.pptx/.potx` 模板，要求“按模板做 / 套用母版版式 / 统一格式 /
把起始 deck 适配到目标模板”时使用。核心标准只有三条：

1. 母版、版式、主题、占位符来自真实模板，不是“看起来像”；
2. 页内文字、数字、图号与源材料一致，不覆盖、不改写事实；
3. PPTX 与导出件可打开、可编辑、可逐页复核，过程记录可复跑。

**不使用 python-pptx**：模板解析、装配、校验全部用标准库 OOXML。

## 唯一入口

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -n "$SKILL_DIR" ] || export SKILL_DIR="/skills/weekly-skill"
python3 "$SKILL_DIR/scripts/main.py" --input /input --work /app/work --output /app/output
```

- `--stage plan`：只审计模板、归一化 workorder、生成版式映射和
  `work/deck-spec.skeleton.json`；供模型先看真实版式再写内容。
- `--stage build`（默认）：装配/重定向、校验、渲染、写 `result.json`。
- `--spec PATH`：显式指定模型写的 deck spec；默认读 `work/deck-spec.json`。
- `--no-render`：跳过原生预览；结果会如实标 `partial`，不伪造 PDF。

入口对 `partial` / `input_error` 也写 `<output>/result.json` 并退出 0；
只有输出目录完全不可写才退出 2。

## 输入

| 路径 | 用途 |
|---|---|
| `/input/task.md` | 页数、受众、时长、字体下限、导出要求 |
| `/input/case.json` | `case_id`、`source_materials`、`output_contract` 等原始字段（入口会保留） |
| `/input/artifacts/slide-workorder.json` | 正式工单：`mode`、`target_template`、`starting_deck`、`slide_count`、`edits`、`preserve` |
| `/input/materials/*.pptx` | 目标模板；起始 deck 若存在则由 workorder 指定 |
| 论文/图表/数据 | 模型用平台提供的 PDF 文本/渲染工具读取，不要凭文件名猜内容 |

workorder 优先于文件名猜测；只有缺失时才扫描 `*template*` / `*starting*`。

## 两种模式

- **adapt / retarget**：有 `starting_deck`。保留每页内容、图片和备注，只把
  母版/版式/主题替换为目标模板，并按 `layout_map` 重定向每页；workorder 的
  `edits` 逐条应用，未命中的编辑会写异常，不删内容。
- **build / template+content**：有模板和源材料。模型先把页级内容写成
  `work/deck-spec.json`，入口再把文字装进真实占位符；模板示例页会被移除。

`mode` 以 workorder 为准；两者都存在且未声明时优先 adapt。

## 模型必须写的内容

deck spec 是接口，不是答案文件。正文、图表引用、讲稿和来源定位由模型根据
`task.md` 与真实源材料撰写；禁止把抽取式句子当最终稿。

```json
{
  "title": "整册标题",
  "source": "materials/paper.pdf",
  "slides": [
    {"role": "title", "title": "主张式标题", "subtitle": "副标题",
     "notes": "开场讲稿", "source": "paper title"},
    {"role": "section", "title": "01 问题", "subtitle": "为什么重要"},
    {"role": "content", "title": "结果主张", "bullets": ["要点 1", "要点 2"],
     "notes": "结果讲稿", "source": "Fig. 2a; Table 1"},
    {"role": "figure", "title": "证据图", "image": "materials/fig2a.png",
     "caption": "Fig. 2a …", "source": "Fig. 2a"},
    {"role": "closing", "title": "结论", "subtitle": "边界与下一步"}
  ]
}
```

`role` 可取 `title|section|content|two-column|figure|quote|closing`。可选字段
`bullets_right`、`claims`、`footer`、`layout`（必须来自审计的真实版式名）。
完整字段、记录文件与 result 契约见 `references/artifact_schema.md`。

## 执行步骤

### 第 1 步（约 15%）：审计与规划

```bash
python3 "$SKILL_DIR/scripts/main.py" --input /input --work /app/work \
  --output /app/output --stage plan
```

读取：

- `work/template-audit.json`：真实母版数、每个 layout 的名称/类型/占位符
  `idx/type/box`、主题字体/配色、页面尺寸、示例页、notes master；
- `work/layout-map.json`：每页角色与真实版式；
- `work/ppt-format-spec.json`：角色→版式、字号阶梯、字体策略；
- `work/deck-spec.skeleton.json`：build 模式下的空槽，仅供模型填写，不是答案。

版式映射规则：content 只选带标题+正文占位符的内容版式，**绝不落到
section header**；title/section/figure/two-column/closing 各有独立评分；
找不到时使用最接近的真实版式并在 `exceptions` 记 `layout_fallback`。

### 第 2 步（约 40%）：模型写页级内容

读 `task.md` 和源材料（PDF 用平台 pdf-text-tool / pdf-render-tool），把
研究问题、方法、结果、限制组织成页级主张：

- 每页一个可讲的主张，标题短于正文；
- 数字、百分比、样本量、p 值、单位照抄源材料，并写 `source` 定位；
- 图表用真实图像文件，图号和正文引用一致；
- 每页写 `notes`（讲稿）和至少一个来源定位；
- 不把整段论文粘进占位符，不编造缺失数据。

保存为 `/app/work/deck-spec.json`。adapt 模式可跳过本步。

### 第 3 步（约 25%）：装配与校验

```bash
python3 "$SKILL_DIR/scripts/main.py" --input /input --work /app/work \
  --output /app/output
```

入口会：

1. 复制模板的 master/layouts/theme（build 模式移除模板示例页）；
2. 把文字写入真实占位符；图片等比缩放居中，备注写入 notes；
3. 保留模板主题字体与主题色，**不做静默字体替换**；
4. 生成 `output/presentation.pptx`、`output/result.json`、
   `output/format-check.json`、`output/work/*.json`；
5. 优先用 LibreOffice/soffice 渲染真实 PDF/PNG。没有渲染器时写
   `render.unavailable=true`、`status=partial` 和具体 limitation，不写假 PDF。

### 第 4 步（约 20%）：视觉复核与修复

有原生预览时逐页打开 `output/preview/*.png` 或 PDF，检查：标题是否被裁切、
图片是否压字、页脚/页码、字体回退、模板装饰是否错位。只改 deck spec（措辞、
页序、图片）后重跑，不要手工改 PPTX 或换字体。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 模板继承 | `checks.template.master/layouts/theme/slide_size` 全 true |
| G2 | 版式 | 每页 layout 来自目标模板；content 不落在 section header |
| G3 | 占位符 | 无 `PLACEHOLDER_NOT_IN_LAYOUT`；文字在占位符内 |
| G4 | 字体 | `fonts.outside_template=[]`；缺失字体只记录，不替换 |
| G5 | 字号 | 标题 > 正文；正文 ≥18 pt；图注 ≥12 pt；页脚 ≥10 pt |
| G6 | 内容 | 保留率 ≥0.98；数字不丢；每页有来源定位 |
| G7 | 图片 | 等比缩放；`aspect_deviation` ≤3% |
| G8 | 版心 | 无 `SHAPE_OUT_OF_BOUNDS`；`overflow_risks` 清零或已记录 |
| G9 | 导出 | PPTX 包校验通过；原生 PDF/PNG 与页数一致 |
| G10 | 记录 | 五类记录齐全；result 的官方字段由入口生成 |

## 输出契约（摘要）

```json
{
  "case_id": "与输入一致",
  "status": "answered|partial|input_error|blocked",
  "task_status": "completed|partial|input_error",
  "editable_source": {"path": "presentation.pptx", "format": "pptx", "slide_count": 12},
  "export_artifact": {"path": "preview/presentation.pdf", "format": "pdf", "native": true},
  "content_map": [{"slide": 1, "claim": "…", "source": "Fig. 2a", "locator": "…"}],
  "compliance_checks": {"pptx_valid": true, "template_fidelity": "master+layouts+theme",
                        "placeholder_used": true, "export_signature": "pdf"},
  "exceptions": [],
  "deck": {"template_fidelity": "master+layouts+theme", "aspect_ratio": "16:9"},
  "render": {"engine": "libreoffice", "native": true, "pdf": "preview/presentation.pdf"},
  "checks": {"zip_valid": true, "page_count_match": true},
  "artifacts": [{"role": "editable", "path": "presentation.pptx"}]
}
```

要求：

- `status`、`editable_source`、`export_artifact`、`content_map`、
  `compliance_checks`、`exceptions` 全部由入口生成，模型不要手写；
- `case.json` 的原始字段（`output_contract`、`source_materials` 等）保留；
- `work/plan.json`、`work/claim_source.json`、`work/change_log.json`、
  `work/render_report.json`、`work/time.json` 一并输出并列入 artifacts；
- 没有原生渲染时 `export_artifact.path=null`、`render.unavailable=true`、
  `status=partial`，不把结构预览冒充 PDF。

## 异常处理

| 情况 | 处理 |
|---|---|
| 找不到目标模板 | `status=input_error`，`NO_TARGET_TEMPLATE`，仍写 result 并退出 0 |
| 模板损坏/无 master/theme | `status=input_error`，`TEMPLATE_UNUSABLE`，拒绝套用 |
| 模板只有少量版式 | 用最接近的真实版式，记 `layout_fallback`，不新造版式 |
| 起始稿占位符与新版式不一致 | 只重写 `p:ph` 的 type/idx；无对应槽时保留为自由形状并记录 |
| workorder 编辑未命中 | 不删除内容；`EDIT_NOT_APPLIED` 警告并列出原文 |
| 字体不在本机 | 保留模板字体声明；记 `FONT_NOT_INSTALLED`，不静默替换 |
| 图片比例不符 | 等比缩放居中；>3% 偏差记 `IMAGE_STRETCHED` |
| 无 office 渲染器 | 交付 PPTX + 结构校验；`status=partial`，写 limitation |
| 无 deck spec 且无起始稿 | 生成明确的 partial 骨架，`DECK_SPEC_MISSING`，不冒充完成 |

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/main.py` | 唯一入口：发现输入、规划、装配、校验、渲染、写 result |
| `scripts/spec.py` | workorder 归一化、真实版式映射、deck spec 校验、字体清单 |
| `scripts/deckkit.py` | 标准库 OOXML：审计、build、retarget、edits、原生预览 |
| `scripts/verify.py` | 包结构、模板继承、占位符、字体/字号、保留率、图片、溢出 |
| `scripts/reports.py` | 官方 media 字段、五类记录、result.json 合并 |
| `scripts/selfcheck.py` | 合成模板/起始稿冒烟测试：`python3 scripts/selfcheck.py` |
| `references/artifact_schema.md` | workorder、deck spec、format spec、result 与记录字段 |

不依赖任何外部 Python 包；渲染器可选。

## 最小接口示例（示例，不是正式答案）

```bash
# 1) 看真实模板与版式
python3 "$SKILL_DIR/scripts/main.py" --input /input --work /app/work \
  --output /app/output --stage plan
# 2) 模型写 /app/work/deck-spec.json
# 3) 装配 + 校验 + 原生预览
python3 "$SKILL_DIR/scripts/main.py" --input /input --work /app/work \
  --output /app/output
# 输出：presentation.pptx、preview/presentation.pdf、result.json
```

示例只说明接口和调用顺序，不表示任何正式案例的页数、内容或分数。

## 限制

- 标准库 OOXML 覆盖常见 master/layout/placeholder/notes/media；复杂 SmartArt、
  OLE、动画不会被重建，重定向时原样保留其包部件。
- 多母版/多主题模板只能做 theme-only 降级并在 `exceptions` 说明。
- PDF/PNG 预览依赖 office renderer；没有渲染器时只做结构校验。
- 本 skill 不解析 PDF 正文，也不补造数据；源材料读取由模型用平台工具完成。
