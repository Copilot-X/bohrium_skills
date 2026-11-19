# 文献知识图谱规则（可执行版）

本文件是 `work/graph.json` 的判定标准。按顺序执行，每一步都写进图或
`uncertain`，不要跳步。

## 1. 先锁定两篇论文的身份

1. 打开 `work/corpus.json`，逐篇记录：
   - `source_id`（P1、P2）；
   - `doi`、`title`、`authors`、`license`；
   - `paragraphs[].paragraph_id` 与 `text`。
2. 对照 `corpus-manifest.json`：
   - 第一篇的 DOI/title/authors 只能出现在 P1 的节点、边、查询证据中；
   - 第二篇同理；**根 DOI/title/authors 不属于第二篇**。
3. 建立 `paragraph_index` 视图（入口已生成）：键是
   `"P1:jats-p-0002"` 形式。任何证据定位必须能在这个索引里查到。
4. 若两篇的段落数、DOI 或标题异常，先写入 `uncertain`，再继续做可做部分。

## 2. 实体抽取（抽得准）

实体类型与判定：

| type | 判定 | 例子 |
|---|---|---|
| `researcher` | 论文中署名的作者/被引研究者 | Alice Smith |
| `material` | 具体材料、样品、催化剂 | CeO2-x、separate ceria sample |
| `method` | 实验/计算方法 | operando spectroscopy、precipitation |
| `task` | 科研任务/反应 | CO2 hydrogenation |
| `metric` | 测得的指标 | methanol selectivity、conversion |
| `finding` | 论文报告的结论/观察 | formate coverage correlates with vacancies |
| `concept` | 抽象概念/机理词 | oxygen vacancy |
| `condition` | 温度、压力、气氛、空速等 | 350 degrees C、1 bar |
| `dataset` / `model` / `organism` | 数据集/模型/生物体 | 仅当原文明确出现 |
| `other` | 以上都不合适 | 必须在 uncertain 说明 |

规则：

1. `label` 用原文表述，不替换成领域同义词；`aliases` 只放原文出现过的
   别名/缩写/大小写变体。
2. 每个节点至少一条 evidence；`source_ids` 列出证据涉及的论文。
3. 不要把“背景常识”做成节点：例如原文没提的反应式、没测的指标、
   没出现的作者单位，不建节点。
4. 区分作者自述与他人转述：
   - `The authors suggest ...` → 结论强度为 indirect；
   - `We measured ...` / `X reached 18%` → direct。
5. 同一段里的不同数值属于不同条件时，分别建 metric/finding 节点，
   不要把 `12%` 与 `18%` 合成一个“平均”节点。

## 3. 证据绑定（回得去）

每条 evidence：

```json
{"source_id": "P1", "paragraph_id": "jats-p-0002",
 "quote": "CeO2-x reached 18% conversion and 92% selectivity"}
```

1. `source_id` + `paragraph_id` 必须同时存在；入口按
   `"<source_id>:<paragraph_id>"` 查段落。
2. `quote` 必须是该段 `text` 的逐字子串（允许空白/全半角差异）。
   - 可截取一句短引；
   - 不连续片段用 `…`（或 `...`）连接，每段仍需逐字存在；
   - 同义改写、翻译、拼写纠正都不算证据。
3. **不要写 PDF 页码**：本任务只有 JATS 段落 ID；`locator` 若出现
   `page`/`p. 3` 会被判为问题。
4. 一个节点/边可有多条 evidence；跨篇节点必须给出来自各篇的证据，
   `source_ids` 与 evidence 的 `source_id` 对应。
5. 找不到逐字证据时：删掉该节点/边，或把说法降级为 `uncertain`；
   不要用相邻段落或另一篇的句子凑证据。

## 4. 关系抽取（连得对）

关系类型与方向：

| type | 方向 | 用法 |
|---|---|---|
| `uses` | A → B | A 使用/采用 B（方法、材料、数据） |
| `compares` | A → B | 原文明确比较 A 与 B |
| `outperforms` / `underperforms` | A → B | 指标上 A 优于/劣于 B，**必须带条件** |
| `supports` / `contradicts` | 证据 → 结论 | 原文明确支持或反驳某结论 |
| `measured_by` | 对象 → 方法/指标 | 用某方法测得 |
| `part_of` / `is_a` | 子 → 父 / 实例 → 类 | 组成与分类 |
| `predicts` / `causes` | A → B | 仅用于原文明确预测/因果表述 |
| `associated_with` | A ↔ B | 相关，不升级为因果 |
| `reports` | 论文 → 结论 | 转述关系 |
| `evaluated_on` / `trained_on` | 方法/模型 → 数据 | 评测/训练数据 |
| `derived_from` | 结果 → 来源 | 派生关系 |
| `enhances` / `reduces` / `modulates` | A → B | 原文明确增强/降低/调节 B；效应关系不自动升级为 `causes` |
| `interacts_with` | A ↔ B | 物理/化学相互作用 |
| `co_occurs_with` | A ↔ B | 仅共现；不得当作因果或直接支持 |
| `has_part` / `has_property` | 整体 → 部分 / 实体 → 属性 | 组成与属性 |
| `measured_as` | 对象 → 量/单位 | 指标与测量表达 |
| `expresses` / `encodes` | 生物实体 → 分子 | 表达/编码（生医场景） |
| `treats` / `prevents` / `induces` / `represses` | 干预 → 对象/过程 | 生医效应，必须带条件 |
| `related_to` / `other` | 泛化 | 需在 conditions 或 uncertain 解释 |

规则：

1. `source`、`target` 必须是已定义的节点 ID；方向按上表，不因句子语序反转。
2. `conditions` 记录让关系成立的条件（样品、温度、压力、空速、数据子集）；
   没有条件也要写 `[]`，不要省略。
3. `strength`：
   - `direct`：原文直接断言（`reached`、`outperformed`、`measured`）；
   - `indirect`：`may`、`suggest`、`associated with`、综述转述、间接证据。
4. 共现不是关系：同段出现两个词，不等于存在 `causes`/`supports`。
5. 每条边必须有 ≥1 条 evidence；引用综述转述时，`source_id` 指向转述所在
   论文，`strength=indirect`，不得伪装成原始实验。

## 5. 别名归一与合并（对得齐）

判定顺序：

1. **同 DOI + 原文明确等价**（`also called`、`denoted as`、同一缩写定义）→
   可 `merge`，`basis` 写 DOI 与等价句。
2. **跨篇同名通用词语**（如 oxygen vacancy、CeO2）→ 先判断指称：
   - 讨论的是同一概念类别 → 可合并概念节点，但证据分别保留；
   - 涉及具体样品/条件/数值 → `keep_separate`，`conflict` 写差异。
3. **不同物理样品、不同模型实例、不同数据集、不同实验条件** →
   必须 `keep_separate`，即使名称/方法相同。
4. **同名不同对象**（人名、材料牌号、缩写多义）→ `keep_separate`，
   `basis` 说明歧义。
5. 每个候选对都写入 `merges[]`，字段：
   - `candidate_ids`：两个或更多节点 ID；
   - `decision`：`merge` 或 `keep_separate`；
   - `basis`：可复核的依据（DOI、原文等价句、样品差异）；
   - `conflict`：不合并时的冲突点（样品/条件/数值/定义）。
6. 不允许为了“图更整齐”强行合并别名；`keep_separate` 是合法结果。

禁止：

- 把 P1 的 `CeO2` 与 P2 的 `separate ceria sample` 当成同一物理样品；
- 把两篇的不同数值合并成均值、范围或“一致结论”；
- 把 `may`/`suggest` 的机理写成 `causes` 边；
- 把不同温度/压力下的 `outperforms` 边去掉 conditions 后合并。

## 6. 查询（查得到）

至少 3 条，建议覆盖：

1. 单篇事实查询（某材料的指标/条件）；
2. 跨篇对齐查询（同名概念/方法是否同一对象）；
3. 边界查询（哪些结论无法确定、证据强度如何）。

每条查询字段：

```json
{"id": "Q1", "question": "Which material had higher methanol selectivity?",
 "start_node_ids": ["N1", "N2"], "edge_types": ["outperforms"],
 "result_node_ids": ["N1", "N2"],
 "answer": "CeO2-x 92% vs CeO2 78% at 350 degrees C and 1 bar (P1).",
 "evidence": [{"source_id": "P1", "paragraph_id": "jats-p-0002",
               "quote": "CeO2-x reached 18% conversion and 92% selectivity"}],
 "uncertain": []}
```

执行规则：

1. `start_node_ids` 必须存在；`edge_types` 为空表示不按类型过滤。
2. 不写 `result_node_ids` 时，入口从起点出发按边类型做最多 3 跳扩展；
   写 `result_node_ids` 时以显式子图为准（仍会带出起点）。
3. `answer` 只陈述结果子图 + 证据能支持的内容；跨篇不可比时写明
   “不能直接比较”及原因。
4. `uncertain` 记录：无结果、证据只有 indirect、两篇条件不同、源内冲突。
5. 查询无结果时改起点/边类型或显式结果；仍无结果就如实报告，
   不要伪造节点或数值。

## 7. 增量与冲突（如题面要求新论文）

1. 既有节点 ID 保持不变，新节点从当前最大编号继续。
2. 新增论文的 DOI/title/authors 单独记录，不覆盖旧论文元数据。
3. 新论文与旧图冲突时：
   - `contradicts` 边保留双方证据；
   - `merges[]` 写 `keep_separate` 与冲突原因；
   - 在 `uncertain` 记录需要人工裁决的点。

## 8. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 把领域常识写成节点 | G2 失败 | 删除；只保留原文出现的对象 |
| 引文是同义改写 | G3 失败 | 回到段落重新逐字截取 |
| 用 PDF 页码定位 | G3 失败 | 改回 `source_id` + `paragraph_id` |
| 共现当成因果 | G4 失败 | 降为 `associated_with` 或删除 |
| 两篇样品被合并 | G5 失败 | 拆回两个节点并写 `keep_separate` |
| 查询直接给结论无证据 | G6 失败 | 补 `result_node_ids`/`evidence` 或降级 answer |
| 跨篇数值合成结论 | 事实错误 | 分开陈述各自条件与数值 |
| 导出文件缺失 | G7/G8 失败 | 重跑 finalize，检查 artifact 路径 |

## 9. 提交前自查

- [ ] `graph.json` 的 `papers` 中两篇 DOI/title/authors 正确分离。
- [ ] 每个 node 有合法 type、非空 label、source_ids、≥1 条证据。
- [ ] 每条 evidence 的段落键能在 `corpus.json.paragraph_index` 查到。
- [ ] 每条 quote 逐字存在；没有页码 locator。
- [ ] 每条 edge 的 source/target 已定义、type 合法、有 conditions 与 evidence。
- [ ] 每个 merge 候选都有 decision 与 basis；不同样品/条件/数值未合并。
- [ ] ≥3 条查询可执行，answer 与 uncertain 不越界。
- [ ] output 目录中 7 个图谱文件与 `result.json` 均存在。
