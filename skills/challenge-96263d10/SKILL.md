---
name: journal-format-adapter
description: 学术格式与模板适配：把稿件逐项适配到给定期刊/会议模板，覆盖章节顺序、标题层级、参考文献样式、图表位置、声明字段与字体，并逐条报告 pass/fixed/missing。Adapt a manuscript to a supplied journal/conference template and report every requirement as pass/fixed/missing into manuscript.docx and format-report.json. Use for 模板排版、期刊格式、投稿清单、参考文献格式。
---

# 学术格式与模板适配（Journal Format Adapter）

## 用途与触发

输入含「稿件 + 期刊模板/投稿指南/格式清单」，要求"按模板排版""改成 XX 格式"时使用。
重点是**逐项对照**，不是笼统地说"已按模板调整"。

## 输入

- `/input/task.md`：目标期刊/会议、模板路径、必须满足的格式清单
- `/input/case.json`：`case_id`、`template_id`、`manuscript_id`、`output_contract`
- 模板：`.docx`/`.dotx`/`.tex`/`.pdf` 样式说明、`style-guide.md`
- 稿件：`.md`/`.docx`/`.tex`
- 字体清单：以 `font-discovery` 探测结果为准

## 第 0 步：生成格式适配初稿（推荐）

```bash
python3 "$SKILL_DIR/scripts/adapt_manuscript.py"   --input /input --work /app/work --output /app/output
```

脚本抽取稿件结构，按模板/指南检查章节、摘要/关键词、声明、参考文献与字体，
用 `make_docx.py` 生成真实 `manuscript.docx`，并写
`work/requirements.json`、`output/format-report.json` 与初版 `result.json`。
它只做格式判定与排版，不改科学内容；缺失项必须保留为 `missing`，不能写成 pass。

## 执行步骤

1. **抽取要求**：把模板与指南拆成可判定的条目，写入 `/app/work/requirements.json`：
   ```json
   {"requirements": [
     {"id": "R01", "area": "structure", "rule": "摘要后必须有 Keywords，3–6 个",
      "check": "regex|manual", "source": "style-guide.md §2.1"},
     {"id": "R02", "area": "reference", "rule": "Vancouver 编号制，作者>6 用 et al.",
      "check": "manual", "source": "template.docx#References"}]}
   ```
2. **解析稿件结构**：标题层级、段落、图表、引用、声明字段，生成
   `/app/work/manuscript-structure.json`。
3. **逐项判定**：每条 R 标 `pass` / `fixed` / `missing` / `not_applicable`，
   记录证据（原文片段或结构路径）。**不允许"全部 pass"却没有证据**。
4. **执行修改**：只改格式，不改科学内容；任何文字改动进 `change_log`
   （before/after/reason）。
5. **引用重排**：按目标格式重编号、重排作者、统一 DOI 呈现；正文引用与文末列表
   双向核对，编号连续、无孤儿条目。
6. **生成 DOCX**：构造 `doc.json`（标题/段落/表格/图/分页），调用
   `python3 /skills/weekly-skill/scripts/make_docx.py --spec /app/work/doc.json --out /app/output/manuscript.docx`，
   再 `--check` 验证。
7. **字体处理**：先 `font-discovery` 确认可用字体；模板字体缺失时用最接近的等宽替代，
   写入 `font_substitution`。遵循组织方模板修订：**使用提供的 Liberation Serif**，
   历史稿件的缺字体结果不再作为现状依据。
8. **写报告**：`format-report.json` + `result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "format-adaptation",
  "status": "completed|partial",
  "target": {"journal": "…", "template_id": "…", "style": "Vancouver|APA|Nature|custom"},
  "artifacts": [
    {"role": "editable", "path": "manuscript.docx", "format": "docx"},
    {"role": "report", "path": "format-report.json", "format": "json"}
  ],
  "requirements": [
    {"id": "R01", "area": "structure", "rule": "…", "status": "pass|fixed|missing|not_applicable",
     "evidence": "docx: paragraph 3", "action": "新增 Keywords 行"}
  ],
  "summary": {"total": 0, "pass": 0, "fixed": 0, "missing": 0, "not_applicable": 0},
  "font_substitution": [{"requested": "Times New Roman", "used": "Liberation Serif",
                         "reason": "模板字体未安装"}],
  "change_log": [{"section": "References", "type": "format", "before": "…", "after": "…"}],
  "unmet": [{"id": "R17", "reason": "模板要求双栏，生成器仅支持单栏", "impact": "需人工排版"}],
  "uncertainty": []
}
```

## 常见要求对照表

| 区域 | 典型检查项 |
|---|---|
| 结构 | 章节顺序、编号层级、摘要/关键词、亮点/图形摘要 |
| 文字 | 字数、拼写体系（英式/美式）、缩写首次展开 |
| 引用 | 编号/作者-年份、作者截断规则、DOI/URL、排序 |
| 图表 | 编号连续、图注位置、分辨率、色图/灰度、表格三线 |
| 声明 | 数据可得性、利益冲突、作者贡献、伦理、基金 |
| 排版 | 页边距、行距、字号、字体、页码、行号 |

## 规则

1. 只做格式变换时不得改变数值、术语和结论；必须改时进 `change_log` 并说明理由。
2. 模板未规定的事项保持原样，不擅自"优化"。
3. 无法满足的要求必须进 `unmet`，不能静默跳过。
4. 引用编号以正文出现顺序为准，文末列表同序。
5. 生成 docx 后必须回读：zip 完整、`word/document.xml` 可解析、段落数 > 0。

## 异常处理

| 情况 | 处理 |
|---|---|
| 模板是 PDF 只有样式说明 | 提取可判定条目，无法判定的标 `manual_check` |
| 稿件是扫描 PDF | 先 OCR，再标注"文本来自 OCR，可能存在识别误差" |
| 模板字体缺失 | 替代 + `font_substitution`，不静默换字体 |
| 模板版式超出生成器能力 | `unmet` 记录，交付可编辑的近似版 + 说明 |
| 稿件含修订痕迹 | 保留原修订信息到 `change_log`，不覆盖 |

## 自检

- [ ] 每条要求都有状态与证据
- [ ] 引用双向核对通过（正文 ↔ 文末）
- [ ] 图表编号连续、图注齐全
- [ ] 声明字段齐全或显式标缺失
- [ ] docx 能打开，字体替换有记录
- [ ] 未改动科学内容（或改动均有留痕）

## 快速路径（预算内）

1. **0–2 分钟**：读 task.md、模板与 style guide，先把要求拆成可判定条目 `requirements[]`。
2. **2–7 分钟**：完成结构/标题/引用三项高影响适配，生成第一版 docx 并落盘。
3. **7–13 分钟**：补图表、声明、字体、页边距等条目，逐项写状态与证据。
4. **最后 2 分钟**：回读 docx、双向核对引用，写 `unmet`。

## 方法增强（融合参考实践）

- **要求可判定化**：每条要求写成“区域 + 规则 + 判定方式 + 来源”，避免“按模板调整”这类不可验收表述。
- **模板审计先行**：记录真实版式名、占位符、主题字体/配色、页面尺寸；不凭常见命名猜测。
- **要求矩阵**：每条 `pass/fixed/missing/not_applicable` 都带证据（docx 段落、样式名、原文片段）。
- **引用双向核对**：正文编号 ↔ 文末列表同序、连续、无孤儿；作者截断、DOI 呈现按目标样式。
- **格式-only diff**：只改格式，不改数值、术语与结论；任何文字改动进 `change_log`。
- **字体降级**：模板字体缺失时同族替代，写 `font_substitution`，不静默换字体。
- **docx 回读**：zip 完整、`word/document.xml` 可解析、段落数 > 0，才算生成成功。

### 定向方法增强

- **来自 LaTeX Manuscript Format Converter**：先判定目标框架（期刊类文件 / 会议模板 / 出版商样式 / 通用投稿样式），再逐部件映射：标题页、作者与单位、摘要、关键词、章节、图表、参考文献、补充材料、公式环境。
- **来自 LaTeX Manuscript Format Converter**：严格区分“内容转换”与“纯格式转换”；严格区分“模板对齐”与“完整合规”；缺 `.cls`、`.bst`、参考文献库或图件时不承诺 compile-ready，写入 `compile_boundary`。
- **来自 Paper Reference Formatter**：每条参考文献先解析为结构化字段（type / authors / title / container / year / volume / issue / pages / publisher / place / DOI / URL / access date / language / edition），再做样式渲染。
- **来自 Paper Reference Formatter**：类型保守推断；缺失字段用 `[missing: field]` 或按样式允许省略，绝不编造；GB/T 7714 保留 `[J]/[D]/[C]/[M]/[R]/[EB/OL]` 类型标识；DOI 统一为 `https://doi.org/...`；作者顺序不变；最终列表不混用样式。
- **两篇合取后的改造**：`format-report.json` 增加 `reference_field_audit[]` 与 `compile_boundary`；每条引用同时报告“字段完整性”和“样式合规性”。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 要求覆盖 | 每条 requirement 有状态与证据 | 补证据；无法判定标 `manual_check` |
| G2 | 引用 | 正文与文末双向一致、编号连续 | 修编号；孤儿条目进 `unmet` |
| G3 | 图表 | 编号连续、图注齐全、正文先引后现 | 补引用或重编号 |
| G4 | 声明 | 数据可得性/利益冲突/作者贡献/伦理/基金齐全 | 缺项标 `missing` |
| G5 | 字体 | 无模板外字体残留；替换有记录 | 补 `font_substitution` |
| G6 | 产物 | docx 可打开，段落数 > 0 | 修包；失败则 `status=partial` |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| “全部 pass”无证据 | requirements 全是 pass，无定位 | 逐条补证据，无法判定改 manual |
| 引用孤儿/断号 | 正文 [12] 文末没有 | 重排编号并双向核对 |
| 静默换字体 | 成品字体与模板不符 | 记录替代原因与范围 |
| 版式名靠猜 | mapping 用了模板不存在的版式 | 以审计结果为准 |
| 改格式时改了内容 | 数值/术语发生变化 | 回滚，改动进 change_log |
| 标题/图注溢出 | 版式裁切文字 | 压缩措辞或拆页，不缩字号 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`target`、`requirements[]`、`summary`、`unmet`
- [ ] `manuscript.docx` 可打开且段落数 > 0
- [ ] 每条要求有状态与证据
- [ ] 引用双向核对通过
- [ ] 图表编号连续、图注齐全
- [ ] 字体替换有记录，无模板外字体
- [ ] 未改动科学内容（或改动均有留痕）

## 最小示例（示例）

输入：`manuscript.md` + `template.docx` + `style-guide.md`（要求 Vancouver、关键词 3–6 个）。
输出：`requirements` 12 条（9 pass / 2 fixed / 1 missing）；`format-report.json` 记录
引用由作者-年份改为编号制；`manuscript.docx` 通过 zip 与 XML 校验。
## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/inspect_input.py` | 盘点稿件、模板与格式要求 |
| `scripts/make_docx.py` | 生成/编辑 DOCX 并按模板套版 |
| `scripts/validate_result.py` | 校验格式、引用与 result.json |
| `references/03-writing.md` | 写作与格式适配规范 |
| `references/08-quality-gates.md` | 交付质量闸门 |
| `templates/result_envelope.json` | result.json 信封示例 |

## 本题交付对照

- 目标产物：可编辑的稿件、改稿、引用/格式结果或结构化写作产物；完成标准：满足题目指定体裁、受众和格式。
- 事实与来源映射：数字、术语、引用、图表和关键论断的来源/修改记录；完成标准：可逐条回溯，不新增材料外事实。
- 版本与边界：修改差异、人工确认项、材料缺口和异常；完成标准：结果可继续编辑，未确认部分明确标注。
