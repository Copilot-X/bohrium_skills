---
name: research-translation-writer
description: 科研成果转化写作（体裁优先）：先按 task.md 判定体裁、读者与篇幅（科普文章/项目申报/impact statement/技术简报/投资 memo），由模型写出一篇可直接使用的完整改写稿，保留关键数字、单位、条件、样本量、图表与引用及其论断强度，再由入口脚本生成 DOCX、逐条来源映射与官方四字段 deliverable/preserved_facts/evidence_map/change_log。Use for 成果转化、科普写作、项目申报、影响力陈述、技术简报、产业化写作、受众适配改写。
---

# 科研成果转化写作（Genre-First Research Translation Writer）

## 触发

task.md 要求把论文、专利、实验数据或技术材料改写成“科普文章”“项目申报材料”
“impact statement”“技术简报”“投资 memo”“产业化前景”，或要求同一成果换受众/
换体裁重写并保留事实与引用时使用。

核心原则：**体裁和读者由 task.md 决定，不默认写商业计划；主稿必须由模型按
体裁写出完整文章，脚本只做取证、检查、装配与落盘；科学结论可以引用，市场
判断不能编。**

## 路径与工作区

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
echo "SKILL_DIR=$SKILL_DIR"
```

- 输入：`/input/task.md`、`/input/case.json`、`/input/**` 下的真实材料。
- 工作区：`/app/work`（inventory/workorder/evidence/检查报告）。
- 交付：`/app/output/<genre>.md`、同名 `.docx`、`result.json`、`evidence.json`、
  `citation-map.json`、`numbers-check.json`。

## 工作流（三步，主稿优先）

### 第 1 步：prepare（读真实文件、判体裁、建证据账本）

```bash
python3 "$SKILL_DIR/scripts/main.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口依次调用 `scripts/inventory.py`、`scripts/workorder.py`、`scripts/evidence.py`，
并生成初始 `citation-map.json`/`numbers-check.json`。核心输出：

- `work/inventory.json`：真实文件、sha256、可读文本；DOCX 用 stdlib 解压，PDF
  尝试运行时转换器，失败标 `readable=false`，不猜内容。
- `work/workorder.json`：体裁判定（含置信度与依据）、受众、语言、篇幅、
  文件名、必需章节、must_keep/must_not_add、`output_contract`。
- `work/evidence.json`：可定位断言、数字/单位/条件/样本量、比较对象、
  图表与引用、受限句（`disclosable=false`）。

**prepare 不写任何保底正文。** 主稿只能由模型按体裁写出。

### 第 2 步：写完整主稿（模型的主要预算）

按 `work/workorder.json` 的 `genre`、`audience`、`length_limit` 与
`deliverable_name`，把完整文章写到 `/app/output/<deliverable_name>`。
**必须是可直接使用的完整文章/材料，不是提纲、写作建议或片段润色。**

| 体裁 | task.md 关键词 | 默认文件名 | 必需结构 |
|---|---|---|---|
| `popular_article` | 科普、公众、大众、非专业 | `article.md` | 为什么重要→做了什么→看到什么→意味着/不意味着→待回答问题 |
| `project_application` | 项目申报、申报书、立项、可行性 | `proposal.md` | 问题→目标/指标→技术路线→现有基础→阶段→风险→预期成果 |
| `impact_statement` | impact statement、影响力陈述 | `impact-statement.md` | 问题→贡献→受益对象→证据→边界 |
| `tech_brief` | 技术简报、技术报告、技术摘要 | `technical-brief.md` | 摘要→事实/指标→成熟度→对比→风险/合规→下一步 |
| `investment_memo` | 投资 memo、商业计划、融资 | `investment-memo.md` | 摘要→技术/成熟度→IP→场景/合作→证据/风险→里程碑 |
| `generic_rewrite` | 以上都不明确 | `rewrite.md` | 背景→发现→意义/条件→边界→下一步 |

写作规则：

- 覆盖 task.md 的全部要求；体裁不需要的章节（如科普里的 TRL、融资、市场规模）
  不要硬加。
- 保留关键数字、单位、实验/样本条件、样本量、比较基准、图表编号与引用；
  不新增材料外事实。材料不足写“材料未提供……”。
- 术语首次出现展开；同一概念全文一个说法；类比要标注类比。
- 论断强度与源文一致：相关不写因果，初步不写定论，实验室不写量产；
  外推必须写假设与待验证路径。
- 主稿写完立即落盘；后续检查失败不阻塞交付。

### 第 3 步：finalize（回源检查、DOCX、result.json）

```bash
DRAFT="/app/output/$(python3 -c 'import json;print(json.load(open("/app/work/workorder.json"))["deliverable_name"])')"
python3 "$SKILL_DIR/scripts/main.py" finalize \
  --work /app/work --output /app/output --draft "$DRAFT"
```

入口依次调用：

1. `scripts/citation_map.py`：主稿段落→evidence 的 `path+locator`，输出
   `evidence_map`；无来源段落单独列出。
2. `scripts/numbers_check.py`：核对数字/单位/条件/引用编号；硬错误=材料外数字、
   单位不符、保密泄漏、占位符；缺关键事实、条件不在附近、篇幅偏差为警告。
3. `scripts/docx_out.py`：stdlib OOXML 输出可编辑 DOCX；尝试 LibreOffice 转 PDF，
   失败只记 limitation。
4. `scripts/result_writing.py`：写 `result.json`（官方四字段 + 契约覆盖 + 来源 +
   检查 + 不确定性）。

正常路径使用模型主稿。若主稿缺失或输入不完整，入口仍写出 `status=partial` 的
`result.json` 并 **exit 0**；只有工作区完全不可写才 exit 2。

## 官方输出契约（result.json）

```json
{
  "case_id": "与输入一致",
  "status": "completed|partial",
  "genre": "popular_article|project_application|impact_statement|tech_brief|investment_memo|generic_rewrite",
  "deliverable": {"path": "article.md", "format": "markdown",
                  "genre": "popular_article", "docx": "article.docx"},
  "preserved_facts": [
    {"fact_id": "E001", "text": "350 °C、1 bar 下转化率 92%",
     "value": "92", "unit": "%", "condition": "350 °C；1 bar",
     "source": {"path": "materials/paper.md", "locator": "L12-L14"},
     "in_draft": true}
  ],
  "evidence_map": [
    {"claim_id": "C001", "claim": "…", "source": {"path": "…", "locator": "…"},
     "quote": "…", "draft_locator": "§3 ¶2", "status": "mapped"}
  ],
  "change_log": [
    {"section": "看到了什么", "action": "rewrite", "before": "源文…",
     "after": "成稿…", "reason": "面向公众改写；保留 92% 与条件"}
  ]
}
```

规则：

- `deliverable.path` 指向真实主稿；`artifacts` 中每个路径必须存在。
- `preserved_facts` 至少覆盖正文关键数字、单位、条件、样本量与引用编号。
- `evidence_map` 只写有来源定位的论断；市场判断不得写成 fact。
- `change_log` 记录实际改动（keep/compress/rewrite/add/delete + 理由）。
- `status=partial` 必须在 `uncertainty` 或 `limitations` 给出原因。
- `output_contract` 原样复制；`contract_coverage` 逐字段说明是否满足。

## 支持文件（相对 `$SKILL_DIR`）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `scripts/main.py` | 唯一入口 | `prepare` / `finalize` / `run`；只做编排 |
| `scripts/transfer_kit.py` | 任务专用共享库 | 被其他脚本 import；文本/JSON/编号/分句工具，不单独运行 |
| `scripts/inventory.py` | 读真实输入 | 由 `main.py prepare` 调用；也可单独 `--input --work` |
| `scripts/workorder.py` | 判体裁/读者/契约 | 由 prepare 调用；体裁判定与约束落到 `workorder.json` |
| `scripts/evidence.py` | 建证据账本 | 由 prepare 调用；输出可定位断言与受限句 |
| `scripts/citation_map.py` | 断言→来源 | 由 finalize 调用；输出 `evidence_map` |
| `scripts/numbers_check.py` | 数字/单位/条件 | 由 finalize 调用；硬错误与警告分开 |
| `scripts/docx_out.py` | 可编辑 DOCX | 由 finalize 调用；stdlib OOXML，可选 PDF 预览 |
| `scripts/result_writing.py` | 写 result.json | 由 finalize 调用；生成官方四字段与契约覆盖 |
| `references/transfer-rules.md` | 体裁/受众/强度/数字/取舍规则 | 动笔前按节查阅；主规则文件 |
| `references/result-schema.md` | workorder/evidence/result 字段 | 需要手写或调试 JSON 时查字段 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 体裁不明确 | `generic_rewrite` + `uncertainty`；不做商业计划，不硬加 TRL/IP |
| 材料缺关键数据 | 主稿写“材料未提供”，`preserved_facts` 不补造 |
| 数据自相矛盾 | 保留各自条件、并列披露；写入 `uncertainty` |
| 引用缺失 | 保留原句并标 `citation_missing`；不补造参考文献 |
| 市场数据无来源 | `market_data: not_available`，只做定性判断 |
| 专利状态不明 | `ip.status: not_provided`，提示核实 |
| 材料涉密/未公开 | 不写入主稿；列入 `restricted`（只留 id/原因/定位） |
| PDF/图片不可读 | 记录 `readable=false`；用运行时 PDF/OCR 工具；不猜内容 |
| 篇幅冲突 | 先保关键证据，压缩背景；写 `change_log` |
| 主稿未写/检查失败 | 写 `status=partial` 的 `result.json` 并 exit 0；不伪造完成 |
| 工作区不可写 | 只有这种情况 exit 2 |

## 自检

- [ ] 主稿是完整文章/材料，不是提纲或建议。
- [ ] `workorder.genre` 与 task.md 一致；没有硬加体裁不需要的章节。
- [ ] 关键数字/单位/条件/样本量/图表编号/引用保留。
- [ ] 没有材料外事实；推断/推测有标记与假设。
- [ ] `deliverable.path` 真实存在；DOCX 可打开。
- [ ] `preserved_facts`、`evidence_map`、`change_log` 非空。
- [ ] `numbers-check.json` 无 hard error；警告已人工确认。
- [ ] 保密内容未进主稿；已列入 `restricted`。
- [ ] 所有缺口写入 `uncertainty` / `limitations` / `unresolved_questions`。

## 版本与限制

- 版本：0.14.0（单入口；体裁判定落 workorder；模型写完整主稿；官方四字段；
  stdlib only，无网络）。
- 市场数据无来源时只做定性判断，不推算。
- PDF/OCR 依赖运行时工具；不可用时只记录缺口，不伪造正文。
- 平台可能把技能装到题目专属目录；命令必须先解析 `$SKILL_DIR`。
