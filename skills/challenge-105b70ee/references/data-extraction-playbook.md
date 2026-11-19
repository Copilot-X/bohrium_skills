# 文献结论与数据提取 Playbook

本 playbook 解决四类高频失败：字段漏抽、数值与条件脱钩、单位被静默假设、缺失与冲突被
“合理猜测”填平。核心原则：**一个 field_id 一条结果；值、单位、统计量、条件、比较对象、
原句定位一起交付；没有就说没有。**

## 1. 输入优先级

| 优先级 | 来源 | 用途 |
|---|---|---|
| 1 | `/input/target-fields.json` | 权威字段表：`field_id`、字段名、类型、期望单位、是否必填 |
| 2 | `/input/task.md` | 输出契约、案例边界、语言与格式要求 |
| 3 | `/input/case.json` | `case_id`、材料清单、段落/表格/图注标识 |
| 4 | 实际论文文件 | 事实依据；PDF 先抽文本，扫描页先 OCR/视觉识别 |

`target-fields.json` 的常见形态：

```json
{"case_id": "c1", "fields": [
  {"field_id": "f1", "name": "conversion", "type": "number",
   "unit": "%", "required": true, "description": "propane conversion"}
]}
```

兼容 `target_fields[]`、`requested_fields[]` 与 `{field_id: {...}}` 映射；若都没有，从
task.md 的字段表读取，并在 `schema[].definition_source` 写明来源。**不得自行缩减或改写
field_id**。

## 2. 字段表先行

先固化 `schema[]`，每条包含：

- `field_id`：逐字复制输入，不改大小写、不加前缀。
- `name`：人类可读字段名。
- `type`：`number|string|boolean|range|date|enum`。
- `unit`：期望单位；无单位写 `null`。
- `required`：输入给了就照抄；没给默认 `true`。
- `definition_source`：`target-fields.json | task.md | inferred`。

字段定义不清时先做最小澄清：指标名、单位、时间点、样本范围、比较基线。**字段表确定后
不再边读边加字段**，否则不同文档的记录不可比。

## 3. 数值三件套与五元组

每条数值结果至少绑定：

```text
value（解析后的数值）
original（原文写法，逐字保留）
unit（单位）
condition（成立条件）
locator（doc_id + paragraph_id/table_id/figure_id + 逐字 quote）
```

再补齐三个语义维度：

- `statistic`：`point_estimate|mean|median|min|max|range|sum|percentage|ratio|not_reported`。
- `comparator`：与谁比、比什么指标；没有比较写 `"none"`，不要留空。
- `sample` / `time_point`：样本量、时间点或随访时长；没有就显式 `not_reported`。

**禁止**把条件塞进 value，禁止把“92%（350 °C）”写成一个字符串值。条件单独成字段，
并给出条件证据；条件来自另一段时，用 `condition_evidence` 指向该段。

### 条件可追溯

条件里的数字也必须能回到原文。若 value 的 quote 不含条件数字，必须另附
`condition_evidence`（同一 doc 的段落/表格/图注 + 逐字 quote）。例如选择性引自 p3，
但“350 °C, GHSV 6000 h^-1”来自 p2：`condition_evidence.paragraph_id = "p2"`。

## 4. 单位规则

1. 期望单位来自 `target-fields.json`；原文单位不同才允许换算。
2. 换算必须记录 `conversion: {from, to, factor, note}`，并保留 `original`。
3. 原文没有单位：`unit: null`、`unit_status: "not_reported"`、`unit_missing: true`，
   同时在 `missing[]` 登记 `kind: "unit_missing"`。**不得按领域常识补单位。**
4. 非数值字段：`unit_status: "not_applicable"`。
5. 百分比与百分点分开：`%` 是比例，`percentage points` 是差值；不得混用。
6. 区间写成 `{"min": ..., "max": ...}`，并保留原文；不要取中点冒充点估计。
7. 误差写成独立字段：`uncertainty: {"type": "sd|se|ci95|range", "value": ...}`；
   不得把 `±` 后面的值并进主值。

## 5. 来源与定位

| 来源 | locator 必填 | 说明 |
|---|---|---|
| 正文 | `paragraph_id` + `quote` | quote 必须是同一段内的逐字连续文本 |
| 表格 | `table_id` + `row_index`/`column` + `quote` | quote 用整行原始文本；跨页表先重建表头 |
| 图注 | `figure_id` + `quote` | 图内读数另标 `read_from: "plot"` 与读图误差 |
| 补充材料 | `supplementary_id` + `quote` | 与正文冲突时进入 `source_conflict` |

规则：

- 定位粒度要能让第二个人独立抽查：只有“Results 部分”不算定位。
- quote 逐字复制，不翻译、不改写、不拼接两个不连续句子；需要省略时用 `ellipsis: true`
  并把两段分别列出。
- **逐字匹配不等于语义正确**：必须填 `semantic_check`，核对主语（材料/样本）、指标
  （metric）、条件与比较对象是否与 field 定义一致。主语或指标不符时，该值只能标
  `extraction_failed`，不能算 `reported`。

## 6. 来源优先级与冲突

1. 同一指标多个值：先确认是否同一条件、同一样本、同一统计量；条件不同不是冲突，分别记录。
2. 条件相同而数值不同：`status: "source_conflict"`，`candidates[]` 保留全部候选，每个
   候选各自带 locator 与 quote；顶层 `conflicts[]` 写清双方来源与差异。
3. 来源优先级：正文结果段 ≈ 表格 > 图注 > 摘要 > 讨论/综述转述 > 补充材料（以 task.md
   为准）。优先级只用于解释，不用于删除候选。
4. 只有给出 `resolution` + `resolution_note` 时才允许在 `values[]` 放选定值；否则
   `values` 留空。**禁止静默择一。**
5. 综述/二次转述不能冒充原始数据；`source_kind: "review"`，并尽量回溯原始研究。

## 7. 表格与跨页

1. 先判表：Markdown 管道表、CSV/TSV、对齐文本表；用
   `scripts/extract_tables.py` 产出 `tables.json` 作为候选索引。
2. 重建表头路径：多级表头记录 `header_path: ["Performance", "Conversion (%)"]`；
   合并单元格记录 `rowspan/colspan`。
3. 跨页续表：续页缺表头时回查上一页；不得把续页第一行当表头。
4. 单位继承：表头单位（如 `Conversion (%)`）作用于整列；单元格后缀单位优先。
5. 逐行 quote 用原始行文本；数值一致性以该行文本为准，不以重新排版的 CSV 为准。
6. 表内合计做本地复算；差额写入 `uncertainty`，**不得为凑平修改原值**。

## 8. 图注与读图

- 图注中的定量表述可抽，locator 用 `figure_id` + quote。
- 从坐标轴读出的值标 `read_from: "plot"`，并给 `axis_calibration`（像素/刻度换算）
  与读数误差。
- 图注只写“显著提高”而无数字时，不得补数字；写 `not_reported` 或只抽定性结论。
- 纯图页/扫描页先判文本层：`page_text_status: "none|text|scanned"`；无文本层且未 OCR
  时整条标 `extraction_failed`，原因写 `page_text_status=none`。

## 9. 状态词表（只能四选一）

| status | 含义 | 必填 |
|---|---|---|
| `reported` | 原文明确报告，且有定位与语义核对 | `values[]`、每值 locator/quote、unit 绑定 |
| `not_reported` | 已检索材料但原文未报告 | `missing_reason`、`search_scope`、`missing[]` |
| `source_conflict` | 同条件同指标出现多个不一致值 | `candidates[]`（≥2）、`conflicts[]` |
| `extraction_failed` | 材料存在但无法可靠抽取（扫描、遮挡、表格错位、权限） | `missing_reason`、`missing[]` |

区分 `not_reported` 与 `extraction_failed` 是评分点：前者是“作者没写”，后者是“我读不到”。
不得把后者写成前者，也不得把前者写成 0 或均值。

## 10. 缺失与冲突登记

`missing[]` 每条：

```json
{"field_id": "f4", "field": "space_velocity",
 "kind": "not_reported|unit_missing|condition_missing|extraction_failed|unreadable",
 "reason": "Results 与表格均未给出 GHSV", "search_scope": "p1-p4, t1"}
```

- 每个 `not_reported`/`extraction_failed` 字段至少一条对应 missing 记录。
- `unit_missing`/`condition_missing` 可与 `reported` 共存，但必须显式登记。
- `conflicts[]` 每条至少两个候选，候选带来源；不得只留“最终采用值”。

## 11. 数字二次核对

重点检查易混字符：`6/8/9/0/3`、`1/7`、小数点、负号、`±`、上标、千分位逗号。
两轮不一致时保留两个候选并标 `digit_check.conflicts`，不静默取一个。
`digit_check.checked=true` 表示已做逐位复核；OCR 结果另标 `ocr_confidence`。

## 12. 失败模式速查

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 字段漏抽 | 请求 f5，结果只有 f1-f4 | 按 field_id 全覆盖，缺的进 missing |
| 条件脱钩 | 有 92%，不知温度/空速 | 补 condition + condition_evidence |
| 静默补单位 | 原文“6000”，结果写 `h^-1` | 改 `unit:null` + `unit_missing:true` |
| 缺失填零 | 未报告字段出现 0/均值 | 改 `not_reported` + reason |
| 冲突静默择一 | 只有一个候选，无 conflicts | 恢复全部候选与来源 |
| 逐字但对错指标 | quote 有数字但主语/指标不符 | `semantic_check` 置 false，改 extraction_failed |
| 跨页表头错位 | 同列出现不同量纲 | 重建 header_path，回查续页 |
| 综述冒充原始数据 | 唯一来源是 review | 标 review，回溯原始研究或降级 |
| 单位换算无记录 | value 与 original 对不上 | 补 conversion{from,to,factor} |

## 13. 交付前检查

1. `extractions[]` 与 `target-fields.json` 的 field_id 一一对应。
2. 每个 `reported` 值都有 value/original/unit/statistic/condition/comparator/locator/quote/
   semantic_check。
3. 条件数字能在 value quote 或 condition_evidence 中找到。
4. 所有 `unit_missing`/`condition_missing`/`not_reported`/`extraction_failed` 都进了
   `missing[]`。
5. 所有 `source_conflict` 都有 ≥2 个候选与 `conflicts[]` 记录。
6. 所有 quote 在输入语料中逐字可查。
7. `summary` 计数与实际一致；`support_files_used` 列出实际调用的文件。
8. 运行 `scripts/check_extraction.py`，`ok=true` 才交付；有 problem 先修，修不了则
   `status=partial` 并在 `uncertainty` 说明。
