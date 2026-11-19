# 阅读家族流程（Reading Track）

覆盖：文献智能问答、科研图表解析、多文档比较与整合、阅读客户端、结论与数据提取、证据溯源与事实核验、公式解析与数学推理、论文复现与方法验证、阅读 Benchmark、数据清洗与标准化。

## 通用流水线

```
盘点输入 → 建段落索引 → 识别问题类型 → 逐题检索证据 → 组答 → 证据回填自检 → 写 result.json
```

### 1. 建索引

对每篇文档输出：

```json
{"doc_id":"...","title":"...","sections":[{"section_id":"...","title":"...","paragraphs":[
  {"paragraph_id":"p1","first_sentence":"...","numbers":["92%","350 °C"],"tables":[],"figures":[]}]}]}
```

索引写到 `/app/work/index.json`，后续检索只读索引 + 按需回读原文。

### 2. 问题分类

| 类型 | 识别信号 | 处理 |
|---|---|---|
| 事实型 | what / which / 数值 | 直接定位，单条证据 |
| 条件型 | under what conditions / 适用条件 | 数值 + 条件一起摘 |
| 比较型 | compare / 相比 / 差异 | 两方各自证据 + 对照维度 |
| 因果型 | why / mechanism | 原文机制表述，禁止自造机理 |
| 推断型 | 是否可推广 / 说明了什么 | `kind=inferred`，给推断链 |
| 缺口型 | 材料未报告 | `status=insufficient_evidence`，说明检索范围 |
| 计算型 | 公式 / 推导 | 展示算式与代入值 |

### 3. 组答

每个问题一条 `answers[]` 记录：

```json
{"question_id":"q1","answer":"……","status":"answered|partial|insufficient_evidence|input_error",
 "conditions":["..."],
 "claims":[{"claim_id":"q1-c1","text":"...","kind":"reported",
            "evidence":[{"paragraph_id":"p12","quote":"..."}]}],
 "unanswered_parts":[]}
```

规则：
- 一个问题可含多个 claim，覆盖子问题。
- `answer` 是给读者的自然语言回答，claim 是给核验的结构化拆解。
- `answer` 中的每个数值都必须出现在某个 claim 的 evidence quote 中。
- 子问题未答完 → `status=partial` + `unanswered_parts`。

### 4. 自检

- 引文逐字比对（`validate_result.py --check-quotes` 可对纯文本材料做子串校验）。
- 每个 question_id 都有条目。
- 每个数值都能回溯到 quote。

---

## 分题型要点

### A. 科研图表解析

1. 先读图注（caption）与坐标轴标签，再读数据。
2. 轴：单位、量纲、对数/线性、截断。
3. 误差：误差棒含义（SD/SEM/CI）、样本量 n、显著性标记。
4. 趋势：单调性、极值点、拐点、平台期。
5. 读图数值必须标 `estimated: true` 与读数误差。
6. 图与正文冲突时，列出双方，不裁决。

输出要点：`figure_id`、`panel`、`axes`、`series[]`、`observations[]`、`uncertainty[]`。

### B. 多文档比较与整合

1. 先建**对照维度表**（研究问题、数据、方法、指标、结论、局限）。
2. 逐文档填表，空格标注 `not_reported`。
3. 只在**同一维度、同一指标定义**下比较；定义不同要显式说明。
4. 整合结论必须能落到具体文档与段落。
5. 冲突结论单列 `conflicts[]`。

### C. 阅读客户端（交互式阅读工具/界面）

产出通常是：可运行的阅读界面或结构化阅读视图 + 说明。

1. 先确认交付形态（HTML/CLI/JSON 视图）。
2. 视图要素：目录、段落、高亮、批注、证据跳转、检索。
3. 数据从 case.json 真实读取，不内嵌假数据。
4. 提供打开方式与截图/预览。

### D. 结论与数据提取

1. 先定义**字段表**（字段名、类型、单位、是否必填）。
2. 逐字段检索；找不到写 `null` + `missing_reason`。
3. 表格跨页/跨列时重建表头映射。
4. 单位缺失时保留原值并标注 `unit_missing`。
5. 数值冲突时保留全部候选 `candidates[]`。

### E. 证据溯源与事实核验

1. 把待核验陈述拆成**原子断言**（一个断言一个事实）。
2. 每条断言标注：`supported` / `contradicted` / `not_found` / `ambiguous`。
3. 给支持与反驳证据各一组。
4. 改写建议：仅在原文支持范围内改写。
5. 引用链：`claim -> evidence -> paragraph -> 原文`。

### F. 公式解析与数学推理

1. 公式转写用 LaTeX，逐符号定义。
2. 推导每步写依据（定义/定理/近似）。
3. 数值代入写清单位与量纲一致性。
4. 近似条件必须标注（小角、稀溶液、稳态等）。
5. 无法解析的符号标 `unknown_symbol`，不臆测。

### G. 论文复现与方法验证

1. 抽取方法要素：数据、预处理、模型、超参、评价、随机种子。
2. 缺失要素列 `reproduction_gaps[]`，评估影响。
3. 能计算就实际计算；不能运行就给出可执行脚本与预期输出。
4. 结果与原文对比：`reported` vs `reproduced`，给相对误差。
5. 不伪造运行结果；未运行写 `not_run` + 原因。

### H. 阅读 Benchmark 与评测工具

1. 明确评测对象、指标、数据划分、基线。
2. 指标定义给公式与实现口径。
3. 提供可运行的评测脚本（stdlib 优先）。
4. 报告结果要含样本量与置信区间。
5. 不声称跑过未跑的实验。

### I. 文献数据清洗与标准化

1. 先做**字段画像**：类型、缺失率、异常值、单位分布。
2. 清洗规则逐条可追溯（规则 id + 影响行数）。
3. 标准化：单位统一、命名规范、编码统一、去重。
4. 保留原始值列与清洗后值列。
5. 输出清洗日志 `cleaning_log[]` 与质量报告。

---

## 输出字段速查

| 题型 | 关键产物字段 |
|---|---|
| 智能问答 | `answers[]`, `coverage` |
| 图表解析 | `figures[]`, `series[]`, `observations[]` |
| 多文档 | `comparison_table[]`, `conflicts[]`, `synthesis` |
| 阅读客户端 | `app`/`artifact`, `features[]`, `usage` |
| 结论提取 | `fields[]`, `records[]`, `missing[]` |
| 事实核验 | `verdicts[]`, `evidence[]`, `rewrite` |
| 公式推理 | `formulas[]`, `derivation[]`, `numeric_check` |
| 复现验证 | `method_spec`, `reproduction[]`, `gaps[]` |
| Benchmark | `metrics[]`, `baselines[]`, `protocol` |
| 数据清洗 | `schema[]`, `cleaning_log[]`, `quality_report` |
