# 学术写作赛道通用流程（Writing Playbook）

适用于：论文写作、格式适配、证据驱动写作、客户端、结构优化、结果与讨论、审稿回复、
翻译与双语、基金申请、开题报告、学位论文长文档、成果转化、写作 Benchmark、思维导图。

## 0. 一句话原则

**写的每个事实都必须能指回一份材料；指不回去的，要么标 `inferred`，要么不写。**
写作类评分的第一大项是「实际效果 40 分」，其中结果正确性与可核验性占 20 分——编造一句
顺畅的话，扣分远大于少写一句。

## 1. 开工前：材料盘点（必须做，且要落到文件）

1. 读 `/input/task.md`，抽出：体裁（article/review/grant/proposal/thesis/response/…）、
   受众、语言、字数/页数、章节清单、引用格式、必须包含的声明字段。
2. 读 `/input/case.json`，记录 `case_id` 与所有版本字段（`paper_id`、`material_version`、
   `template_id`、`workorder_id`）。
3. 若存在 `output_contract`（在 case.json、task.md 或单独的 `output-contract*.json` 中），
   **以它为准**：字段名、枚举值、必填项一律照抄，不自造同义词。
4. 扫描 `/input` 下全部材料，生成 `/app/work/material-index.json`：

```json
{
  "materials": [
    {"path": "materials/fig1.png", "kind": "figure", "id": "Fig.1",
     "caption": "…", "usable_for": ["results", "discussion"]},
    {"path": "materials/experiment-log.csv", "kind": "data", "id": "run-A",
     "columns": ["condition", "value", "unit"], "rows": 42}
  ],
  "missing": ["作者单位信息", "基金编号"],
  "conflicts": [{"a": "正文 n=24", "b": "表1 n=26", "topic": "样本量"}]
}
```

盘点结论直接决定后面能不能写——`missing` 里的条目不允许被"合理推测"补上。

## 2. 证据台账（Evidence Ledger）

在动笔前建立 `/app/work/evidence-ledger.json`，一条事实一行：

```json
{
  "entries": [
    {"fact_id": "F001", "statement": "处理组转化率比对照高 18.2 个百分点",
     "value": "18.2", "unit": "百分点", "condition": "350 °C, 1 bar, n=3",
     "source": {"path": "materials/results.xlsx", "locator": "Sheet2!B4:C6"},
     "kind": "reported", "quote": "…原文/原表片段…"}
  ]
}
```

规则：

- `kind` 只取 `reported`（材料明写）/ `derived`（由材料数值算出，必须给算式）/
  `inferred`（推理，必须给依据链）。三者不得互相冒充。
- 数值必须带单位与成立条件；条件缺失时写 `condition: "not_stated"`，不要留空。
- 同一事实在正文、表格、图注中数值不一致 → 记入 `conflicts`，正文用正文值并标注冲突，
  **不擅自裁决**。
- 采样量、误差类型（SD/SEM/CI）、显著性标记必须与材料一致，不得四舍五入改变结论。

## 3. 结构规划（Outline First）

先写 `/app/work/outline.json`，再写正文：

```json
{
  "genre": "research-article",
  "sections": [
    {"id": "abstract", "title": "Abstract", "target_words": 250,
     "claims": ["F001", "F004"], "must_include": ["主要结论", "方法一句话", "意义"]},
    {"id": "results-1", "title": "3.1 Effect of temperature", "target_words": 400,
     "claims": ["F001", "F002"], "figures": ["Fig.2a"]}
  ],
  "unsupported_sections": [{"id": "discussion-mechanism", "reason": "材料无机理数据"}]
}
```

- 每个 section 绑定 claim id；没有 claim 可绑的段落，要么删，要么明确写"材料未提供"。
- `unsupported_sections` 是有价值的诚实输出，不要为了"完整"硬写。

## 4. 写作执行

1. **按 outline 逐节写**，一次一节，写完立刻把该节的 fact_id → 句子映射追加到
   `/app/work/sentence-map.json`。
2. **术语统一**：先建 `/app/work/terms.json`（`全称 / 缩写 / 首次出现位置`），全文只用一种写法；
   缩写首次出现给全称。
3. **时态与语态**按体裁固定：方法/结果用过去时，普遍结论用现在时；中文学术写作避免口语。
4. **数字格式**统一：小数点用 `.`，千分位不混用，单位与数值间留空格（`18.2 pp`），
   范围用 `–`。
5. **引用**：每条引用必须绑定到具体论断；作者/年份/期刊/DOI 与材料一致，
   查不到 DOI 就写 `doi: "not_available"`，**绝不编造**。
6. **声明字段**（数据可得性、利益冲突、作者贡献、伦理审批、基金）：
   材料给什么写什么，没给的写 `not_provided_in_materials`，不留空、不虚构。

## 5. 交付物

| 体裁 | 主产物 | 伴生产物 |
|---|---|---|
| 论文/综述/章节 | `manuscript.md`（或 `.docx`） | `source-map.json`、`change-log.json` |
| 格式/模板适配 | `manuscript.docx` | `format-report.json`（逐项对照模板） |
| 审稿回复 | `response-letter.md` + `revised-manuscript.md` | `point-by-point.json` |
| 翻译/双语 | `bilingual.md`（段级对照） | `translation-notes.json` |
| 基金/开题 | `proposal.md` | `compliance-checklist.json` |
| 长文档 | `thesis.md`（分章） | `chapter-index.json` |
| 思维导图 | `mindmap.json` + `mindmap.svg` | `node-source-map.json` |
| Benchmark | `benchmark.json` + `scorer.py` | `rubric.json`、`report.json` |

`.docx` 用 `scripts/make_docx.py` 由结构化 JSON 生成（无第三方依赖）；生成后必须回读校验
（zip 完整性 + `word/document.xml` 可解析），不能只声明"已生成"。

## 6. 修改留痕（Change Log）

任何"改写/润色/结构调整"都要留可核对的差异：

```json
{
  "changes": [
    {"id": "C1", "section": "3.2", "type": "rewrite|move|delete|insert|format",
     "before": "原句（截断到必要长度）", "after": "新句",
     "reason": "与 Fig.2b 数值不一致", "evidence": "F007"}
  ],
  "untouched_claims": ["F001", "F002"]
}
```

审稿回复与结构优化题尤其看重这个：**没有 before/after 就等于没改**。

## 7. 语言与受众

- 语言按 task.md；未指定时跟随材料主语言。
- 受众决定术语密度与背景铺垫：同行专家可省定义，跨学科读者必须解释缩写。
- 双语写作：中英不是逐字对译，术语按领域通行译法；数字、单位、公式、引用编号保持同源。

## 8. 常见坑

| 坑 | 后果 | 正确做法 |
|---|---|---|
| 用"显著"描述无统计检验的差异 | 结论失真 | 只按材料给的检验结果写 |
| 把相关性写成因果 | 审稿必拒 | 用"associated with"，并说明未验证因果 |
| 摘要数字与正文不一致 | 可核验性扣分 | 摘要只从 ledger 取数 |
| 模板适配只改样式不改字段 | 格式项全丢 | 逐项对照 `format-report.json` |
| 长文档一次性生成 | 超时/截断 | 分章落盘，先写完再合并 |
| 思维导图只有层级没有来源 | 不可核验 | 每个节点带 `source` |
