# 结构化笔记执行规则

本文件是 `work/notes.json` 的写作与自检规则；与
`templates/notes.schema.json` 配套使用。规则按“准备 → 写卡片 → 写章节 →
写知识点 → 审计”的顺序执行。

## 1. 读取骨架，建立章节映射

1. 读 `work/outline.json` 的 `sections[]` 与 `all_segments[]`。
2. 为每个**内容章节**（Abstract、Introduction、Methods、Results、
   Discussion、Conclusion 等）建立一条 notes section；保留 `id`。
3. `heading` 去掉 Markdown `#` 前缀（例如 `## 2 Methods` → `2 Methods`），
   因为 finalize 会自行加 `###`。
4. 章节顺序必须与论文一致；不得把 Introduction 与 Discussion 合并，
   也不得按“背景/创新/结论”等通用模板重排。
5. `Front matter` 不单独成节；标题与作者信息写入 `paper_card`。
6. References 节可省略；若保留，给它一条指向参考文献条目的锚点。
7. 图表、公式、表注按论文位置归入所属章节；不要为凑数新建
   “图表摘要”章节。

自检：`notes.json` 的 section id 集合应覆盖 outline 中除 Front matter /
References 外的全部内容章节。

## 2. 写 paper_card（跨论文可比较的字段）

| 字段 | 必填 | 写什么 | 不写什么 |
|---|---|---|---|
| `research_question` | 是 | 论文要回答的核心问题 | 泛化的领域背景 |
| `data` | 是 | 样本、材料、数据集、规模与来源 | 未经论文报告的数据量 |
| `methods` | 是 | 方法链 + 关键条件 | 只有方法名没有条件 |
| `findings` | 是 | 每条一个发现，带数值和条件 | 把整段摘要塞成一条 |
| `limitations` | 是 | 因果边界、未测量变量、重复数、外推风险 | “样本量小”这类无依据套话 |
| `conditions` | 建议 | 温度、压力、进料比、时间、pH 等 | 与结论无关的实验细节 |
| `doi`/`year`/`authors` | 建议 | 材料中真实出现的值 | 猜测或从记忆补全 |
| `title` | 可选 | 论文标题，用作 notes.md H1 | 自拟标题冒充原文 |

规则：

- 论文未报告必需字段时，填 `Not reported in the paper`（或
  `Not reported in the provided material`），并在 `open_questions` 或该
  字段内注明未报告；该字符串非空，可满足 finalize 的必需字段检查，
  但不得写成事实。`result.json` 的 `uncertainty` 由入口的检查生成，
  模型不能直接写入。
- `findings` 与 `limitations` 必须至少各 1 项；findings 中的每个数字
  都要能在论文中找到。
- `findings`/`limitations` 的数组元素**不要以句号结尾**：finalize 用
  `; ` 连接数组项，带句号会渲染成 `...。; ...`。condensed 写法更易读。
- 情态保持：原文 `may/suggest/consistent with` 不得升级为
  `prove/demonstrate/causes`。

## 3. 写章节笔记（purpose / methods / results / conclusions）

- **purpose**：本节为什么存在、要解决哪个子问题。
- **methods**：本节实际用的方法或数据（非 Methods 节可留空）。
- **results**：本节报告的观察、数值、比较；不要混入 Discussion 的解释。
- **conclusions**：作者本节的结论或过渡；保留限定词。
- 每节至少 1 条 `anchors`；优先选能支撑该节核心句的最小原文片段。
- 字段可以为空串，但不能用空字段替代锚点。
- 不在笔记中新增“我认为”“值得注意”等评价；评价放入
  `open_questions` 且标明是待验证问题。

## 4. 锚点（anchor）格式与最小性

每条 anchor：

```json
{"path": "paper.md", "locator": "paper.md P010", "quote": "逐字原文片段"}
```

- `path`：使用 `outline.source_path`；主论文之外的材料不能作为章节锚点。
- `locator`：使用 `outline.all_segments[]` 中对应段的 `locator`
  （`<path> Pxxx`）或 `Pxxx`。finalize 对 notes 只机器校验 path 与 quote，
  locator 不会被自动比对，因此必须人工按 outline 填对。
- `quote`：
  1. 必须是该段文本的连续子串（空白、全半角、连字符差异会被归一化）；
  2. 最小充分：包含主体、方向、关键数值与条件，不要整段照抄；
  3. 需要连接两处原文时，用 `...`（或 `…`）分隔；每一段都必须逐字存在；
  4. 不得改写、概括、翻译后再当 quote；不得用省略号隐去否定词或限定词。
- 判定口诀：把 quote 放回原文，读者能只凭这句话核对该节结论。
- 注意：finalize 只验证 quote 在**整篇论文**中存在，不验证它属于
  `locator` 所指段落；段落归属与最小性由你自检，复核时必须两者同时核对。

跨页/跨栏被切碎的段落：选包含核心断言的那一段；若证据确实分散，
写两条 anchors，不要拼成一条不存在的句子。

## 5. 知识点（knowledge_points）

- `id`：`K1…Kn`，唯一；不要复用。
- `type`：`finding | method | limitation | concept | variable | condition | claim`。
- `statement`：一条可检索命题，尽量单句；保留主体、方向、数值、条件。
- `evidence`：至少 1 条 anchor，规则同第 4 节；每个知识点必须有证据。
- `tags`：2–5 个稳定标签（概念、指标、方法），便于索引；不要写句子。
- 去重：同一主体+同一关系+同一结论只留一条；同义术语合并到 tags，
  在 statement 中使用论文主术语。
- 矛盾：论文前后不一致时，分别记录两条并在 statement 中注明出处章节，
  或写入 `open_questions`，不要静默取一条。

## 6. 数字与引用

- 笔记正文中的数字必须来自论文；禁止把 `92%` 写成 `≈90%`、
  把 `300–400 °C` 写成 `室温–400 °C`、把 `three times` 写成 `n=5`。
- 范围、单位、有效数字保持原样；`°C` 与 `℃`、`%` 与 `percent` 可统一
  写法，但数值不变。
- 引用编号 `[1]`、图表编号 `Table 1`、公式编号 `Eq. (1)` 原样保留；
  不要把引用编号当作知识点编号。
- finalize 的 `number_presence_missing` 按数值集合比较：
  任何在论文中不存在的数值都会触发 `partial`。自行换算/四舍五入会失败。

## 7. 运行与审计顺序

1. `prepare` 后先看 `work/outline.json` 的章节数、段落数是否合理；
2. 写 `work/notes.json`；
3. 自查：必需字段非空、每节有 anchor、每点有 evidence、数字可回查；
4. 跑 `finalize`；
5. 读 `output/result.json`：
   - `status=answered` 且 `checks.problems=[]` → 可提交；
   - `status=partial` → 按 `uncertainty[].action` 修复后重跑；
   - `status=input_error` → 检查材料是否可读，不要编造。

## 8. 失败模式与处置

| 失败 | 症状 | 处置 | 状态影响 |
|---|---|---|---|
| 通用摘要替代章节笔记 | section 与论文结构不对应 | 按 outline 重建 section | 完成度下降（不自检发现不了） |
| 空锚点/锚点缺失 | `section Sx has no anchors` | 补逐字 quote | `partial` |
| quote 被改写 | `part not found: ...` | 从段落原文复制 | `partial` |
| path 写错 | `path not found ...` | 改用 `outline.source_path` | `partial` |
| 知识点无证据 | `knowledge point Kx has no evidence` | 补 evidence 或移入 open_questions | `partial` |
| 卡片字段空 | `paper_card.xxx is missing` | 如实填写或写未报告 | `partial` |
| 编造/换算数字 | `notes contain numbers that are absent from the paper` | 恢复原文值 | `partial` |
| notes.json 缺失 | `notes.json missing or empty` | 按 schema 重写 | `partial`，无交付物 |
| 无主论文 | prepare `input_error` | 说明输入限制，停止 | `input_error` |
| DOCX 失败 | `docx_export`/`docx_reopen` | 保留 notes.md | 不影响 answered，但需说明 |

## 9. 最小提交前检查清单

- [ ] `case_id` 与 `/input/case.json` 一致；
- [ ] 内容章节全覆盖且顺序一致，heading 无 `#`；
- [ ] paper_card 五个必需字段非空，findings/limitations 为数组；
- [ ] 每节 ≥1 anchor，每点 ≥1 evidence，quote 逐字；
- [ ] 所有数字可在论文中定位；
- [ ] `notes.md`、`paper-card.json`、`knowledge-points.json`、
      `notes-index.json` 存在且非空；
- [ ] `result.json` 可解析，`status` 属于 reading 枚举，
      `deliverable.path=notes.md`，`artifact_paths_valid=true`。
