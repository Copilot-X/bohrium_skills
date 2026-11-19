# 引言与相关工作执行规则

本文件是 `SKILL.md` 的可执行补充。所有规则都能对应到
`scripts/fam_intro.py` 的检查或 `work/writing-plan.json` 的字段。

## 1. 证据底稿

1. 以 `work/evidence.json` 的 `segments[]` 为唯一稿件证据源；
   `locator` 形如 `manuscript.md P004`，引用时逐字复制该 locator。
2. 背景断言只使用稿件已有事实；不得把领域常识写成本文事实。
3. 引用元数据只来自 `work/bibliography.json` 的 `key/title/year/authors/doi`；
   不补造作者、年份、DOI。
4. 主稿件与文献库可能来自不同文件；文献键区分大小写，必须逐字一致。

## 2. 引用标记（决定审计能否通过）

| 写法 | 引擎行为 | 建议 |
|---|---|---|
| `[@smith2020]` | 提取单键 `smith2020` | **首选**；每个括号只放一个键 |
| `[@smith2020][@wang2021]` | 提取两个键 | 多篇同句时使用 |
| `\cite{smith2020,wang2021}` | 逗号拆分，两个键 | TeX 稿首选 |
| `[@a; @b]` | 拆成 `a` 与 `@b`，误报未知键 | **禁止** |
| `[1]` / `[1,2]` | 数字引用，按去重后文献库序号映射 | 仅在模板要求数字时使用 |
| `(Smith et al., 2020)` | 记录 token 但不带 key | 不能证明 gap 来源被引用 |

数字引用规则：

- 文献库先按 DOI 去重，其次按“规范化标题+年份”去重；编号从 1 开始，
  按去重后仍保留条目的顺序。
- `[n]` 的 `n` 必须落在 `1..len(unique_entries)`；否则 `missing_numeric`
  非空，`status=partial`。
- 数字引用本身会被当作正文数字；稿件中没有该数字时，`numbers.ok` 可能
  变为 false，因此除非模板强制数字样式，优先用 `[@key]`。
- 文献键不要以数字开头（如 `2020smith`），否则键内年份会被当成正文数字。
- 不要手工维护文末编号；本 Skill 只写正文，编号由入口映射并审计。

## 3. 研究背景

第一段（3–6 句）必须包含：

1. **对象**：具体材料/系统/任务，而不是“某领域”；
2. **瓶颈**：一个可以被文献定位的具体未解决问题；
3. **为什么现在**：新数据、新方法、新约束或矛盾证据；
4. **本文动作**：在什么条件下比较/验证什么。

禁止句式（出现即重写）：

- “近年来，X 受到广泛关注”“X 具有重要意义”“随着……的发展”。
- 只有“已有研究表明”而没有具体结论、条件或 limiter 的句子。

`background.sources` 至少给 1 个 locator；每条 claim 对应一个来源。

## 4. 相关工作分组

1. 组数 2–4；每组 `works` 至少 2 篇；同一组只用一个 `dimension`：
   `problem`（问题设定）、`method`（方法路线）、`data`（数据/场景）、
   `metric`（评价方式）。
2. `works[].summary` 写三件事：该工作做了什么、结论是什么、在什么条件下成立。
3. `works[].limitation` 必须与同组 `dimension` 对齐；例如方法组写
   “未控制空速”，不要写“作者来自某机构”。
4. `synthesis` 比较取舍：哪些组合同一维度、共同缺口是什么；
   禁止“A 做了……B 做了……”式罗列。
5. 正文与计划必须一致：正文出现的每篇工作都要在某个组的 `works` 中，
   且正文比较维度与 `dimension` 一致。

## 5. 研究空白

`gap` 必须满足：

1. `statement` 非空，句式包含“已有什么 → 仍缺什么 → 本文如何补”；
2. `derived_from` 至少 2 个真实文献键；
3. `derived_from` 中每个键都能在 `introduction.md` + `related-work.md`
   的引用 token 中找到；找不到会进入 `gap_issues`；
4. `evidence` 写支撑该空白的稿件 locator 或具体文献局限；
5. 空白不能是“据我们所知无人研究”这类无法核验的断言，除非稿件明确支持。

## 6. 贡献表述

1. 1–4 条，`id` 从 `C1` 顺序编号；
2. 每条写“做了什么 + 在什么条件下 + 得到什么可核对结果”；
3. `linked_sections` 指向本文真实章节（如 `Results §2`、`Fig. 2`）；
4. `evidence` 写稿件 locator 与可核对数值/图表；
5. `linked_sections` 与 `evidence` 不能同时为空，否则计
   `weak_contributions` 并强制 `status=partial`；
6. 不使用 `first`、`novel`、`unprecedented` 等词，除非稿件有直接证据；
   贡献范围不得超出测试条件。

## 7. 数字与事实

- 正文所有数值必须在主稿件文本中出现；引擎按数值集合比对，
  多写一个百分比、年份或样本量都会使 `numbers.ok=false`。
- 引用键中的年份（`[@smith2020]`）不会被当作正文数字；
  但写成 `Smith et al. (2020)` 会产生数字 token，可能触发新增数字。
- 不写材料没有的统计量（p 值、样本量、增长率、倍数）。
- 列表序号（`1.`、`2.`）和标题编号（`## 1 Introduction`）同样计入数字；
  稿件没有对应数字时改用 `-` 无序列表或去掉标题编号。
- 单位、范围符号（`–`/`-`）、百分号保持稿件写法。

## 8. 单文件与双文件

- 首选 `output/introduction.md` + `output/related-work.md`；
- 单文件兜底：只写 `output/sections.md`，必须同时含
  `# Introduction` 与 `# Related Work` 标题；缺一个标题会导致另一节为空；
- **切分边界是下一个 Markdown 标题**：`# Related Work` 下的第一个
  `##` 子标题会终止切分，其后的正文与引用全部丢失。有分组子标题时
  必须用双文件；单文件模式只能写连续段落；
- 不要同时维护双文件和 `sections.md`，finalize 会重写前两者并重新合并，
  重复内容会进入 `sections.md`。

## 9. 失败模式

| 失败 | 引擎信号 | 修复 |
|---|---|---|
| 引用键拼错 | `unknown_keys` | 对照 `bibliography.json` 改键 |
| 多键写进一个 `[@…]` | 未知键以 `@` 开头 | 拆成多个 `[@key]` |
| 数字引用越界 | `missing_numeric` | 改用键或按去重后顺序重编 |
| 正文新增数字 | `numbers.invented` 非空 | 删数字或改用稿件原值 |
| 相关工作逐篇罗列 | `group_issues` 或人工复核 | 按维度重分组、补 synthesis |
| 空白未由已引工作推出 | `gap_issues` | 补 `derived_from` 并在正文引用 |
| 贡献无证据 | `weak_contributions` | 补 `linked_sections`/`evidence` |
| 只写一个章节 | 任一章节为空 → `partial` | 两个文件都写 |

## 10. 交付前自检

1. `citation-audit.json`：`unknown_keys=[]`、`missing_numeric=[]`、
   `numbers.ok=true`、`group_issues=[]`、`gap_issues=[]`、
   `weak_contributions=[]`。
2. `result.json`：`status=completed`（材料完整时）；
   `deliverable.path="sections.md"` 且 `exists=true`。
3. `artifacts[]` 逐项存在；`sections.docx` 能重新打开；
   `sections-preview.pdf` 存在或已记录 `pdf_preview` uncertainty。
4. 人工复核每条引用是否真的支持该句；机器只核对键与数字，不判断语义。
