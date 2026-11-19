---
name: minimal-edit-academic-polisher
description: 学术语言润色：对给定稿件做最小编辑，交付润色后的完整文本、逐句 before/after/type/reason 修改表、术语表与保真审计；数字、单位、引用、图表指向和限定强度不漂移，并生成官方 writing 四字段。Minimal-edit academic language polishing with sentence-level change records and preservation checks. Use for 学术润色、语言修改、术语一致、学术语气、语义保持、中式英语.
---

# 学术语言润色（Minimal-Edit Academic Polisher）

## 用途与触发

task.md 要求“润色/改写/校对/语言编辑”“统一术语”“改善学术语气”
“中译英润色”“保留原意的最小修改”，或给出目标期刊/文体时使用。
不适用：纯内容问答、没有原文的开放式写作、要求整段重写且不保留原文。

核心原则：**改得对、说得像、没改坏、讲得清**。
脚本只负责切段、应用编辑、保真检查和 result.json；模型负责判断
“这处该不该改”并写出 before/after/reason。

## 输入

- `/input/case.json`：`case_id`、`genre`/`target_genre`、`output_contract`。
- `/input/task.md`：目标文体（期刊论文/学位论文/基金/审稿回复/摘要）、
  术语表、风格指南、英式/美式、是否要求双语。
- `/input` 下的稿件：`.md/.txt/.tex/.docx/.pdf`；PDF 先由 `parsers.py` 抽取。
- 可选：`work/glossary.json`（作者术语表）、已有润色稿。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run |
| `scripts/skillcore.py` | 切段、分句、数字/引用/情态/术语审计、DOCX/PDF 导出 |
| `scripts/parsers.py` | Markdown/TXT/TeX/DOCX/PDF 提取 |
| `scripts/fam_polish.py` | 本任务的最小编辑应用、修改表与保真检查 |
| `task.json` | family、模块、profile、输出契约 |
| `templates/edit.schema.json` | `work/edits.json` 单条编辑字段 |
| `templates/glossary.schema.json` | 术语表字段 |
| `references/polishing-rules.md` | 最小编辑、语气、语义保持与失败模式 |
| `examples/source.md` | synthetic 原稿（非正式案例） |
| `examples/edits.json` | synthetic 修改计划 |
| `examples/glossary.json` | synthetic 术语表 |

## 工作流（先切段，再逐句编辑）

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口产出：

- `work/inventory.json`：输入文件清单与 sha256；
- `work/segments.json`：`P001…` 段落 + `P001-S001…` 句子 + `locator`；
- `work/glossary-candidates.json`：缩略语与重复术语候选；
- `work/edits-template.json`：编辑计划骨架；
- `output/polished-partial.md`：未润色原文，便于先保留可评阅底稿；
- `output/result.json`：`status=partial` 的 provisional 结果。

### 第 2 步：写 `/app/work/edits.json`（约 60% 预算）

```json
{
  "case_id": "与输入一致",
  "genre": "journal article",
  "edits": [
    {
      "id": "E001",
      "segment_id": "P003",
      "sentence_id": "P003-S002",
      "before": "逐字来自原文的片段",
      "after": "修改后的片段",
      "type": "grammar|term|register|clarity|cohesion|typography",
      "reason": "为什么这处需要改，依据是什么",
      "preserve": ["number", "citation", "hedge"]
    }
  ]
}
```

编辑规则（详见 `references/polishing-rules.md`）：

1. **最小编辑**：优先改词、改搭配、改标点；只有长句确实难读时才拆分。
   不把整段换一种说法，不做无理由的同义替换。
2. **语义保持**：限定强度不变；`may/suggest/indicate` 不升级为
   `show/prove/demonstrate`；`associated with` 不改 `causes`；
   否定范围、条件句、量词不变。
3. **事实保持**：数字、单位、样本量、年份、引用编号、图表/公式指向原样。
4. **术语一致**：同一概念全文一种写法；缩写首次出现形式统一；
   大小写与连字符统一；术语表优先。
5. **语域**：期刊论文避免口语化/宣传化/绝对化；学位论文可略展开；
   基金申请强调意义但不夸大；审稿回复礼貌、逐条对应。
6. **每条都有理由**：`reason` 要说明语法错误、搭配问题、语气不符或
   术语漂移，不能只写“更流畅”。

如果只输出整篇 `output/polished.md` 而不写 `edits.json`，入口会用句级
diff 生成修改表，但 `status` 会更保守；优先写 `edits.json`。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 按 `segment_id`/`sentence_id` 精确应用 `before → after`；`before`
   允许空白折叠（换行/多空格不阻止匹配），但不允许改词或改写；
   找不到或出现多次 → `blocked`，原文不变；
2. 生成 `output/polished.md`（可编辑主稿）；
3. 生成 `output/edit-table.json` / `.csv`（逐条修改记录）；
4. 生成 `output/glossary.json`、`output/preservation-report.json`；
5. 导出 `output/polished.docx`（真实 OOXML，可重新打开）与
   `output/polished-preview.pdf`（文本内容预览，不冒充 Word 排版渲染）；
6. 写 `output/result.json`，官方 writing 四字段由入口生成。

`status` 取 `completed|partial|input_error`。有 blocked、数字/引用/情态
异常或术语未落实时用 `partial`，并在 `uncertainties` 说明。

## 输出契约

`result.json` 顶层必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`{path: "polished.md", format: "md", exists: true}`；
- `preserved_facts[]`：数字/引用/术语，逐条带 `source` 与 `locator`；
- `evidence_map[]`：每条编辑的 `claim_id/claim/source/quote/supports`；
- `change_log[]`：真实发生的 prepare/apply/audit 步骤；
- `artifacts[]`：`polished.md`、`edit-table.json/csv`、`glossary.json`、
  `preservation-report.json`、可选 DOCX/PDF；
- `result`：摘要、应用/阻塞条数、检查结果；
- `output_contract` 原样保留，`contract_coverage` 逐字段说明。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主稿完整 | `polished.md` 覆盖原文全部段落，不是摘要/片段 |
| G2 | 编辑可复核 | 每条有 before/after/type/reason/locator |
| G3 | 数字/引用 | `numbers.ok=true`、`citations.ok=true` |
| G4 | 语义强度 | 否定数与情态等级不变 |
| G5 | 术语 | 术语表 target 在文中出现且无混用 |
| G6 | 可编辑 | DOCX 能重新打开；PDF 预览存在或如实说明不可用 |
| G7 | 契约 | result.json 可解析，artifact 路径真实 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 原文为空/不可读 | `input_error`；不生成伪润色稿 |
| `before` 找不到/出现多次 | 该条 `blocked`，原文不变；重摘逐字锚点 |
| 数字/引用被改 | 恢复原值；在 `preservation-report.json` 记录 |
| 情态被加强/弱化 | 恢复原等级；`hedging.ok=false` 时不得标 completed |
| 术语表冲突 | 以术语表为准并记录冲突；无法确定时保留原文并标注 |
| 目标文体不明 | 按期刊论文默认，写入 `uncertainty` |
| 时间不足 | 先保留 `polished.md` 与已应用编辑，`status=partial` |

## 最小示例（synthetic，不是正式答案）

原文：`The catalyst show a high selectivity at 350 C, and it may be stable.`

```json
{"id": "E001", "segment_id": "P003", "sentence_id": "P003-S001",
 "before": "The catalyst show a high selectivity at 350 C",
 "after": "The catalyst showed high selectivity at 350 °C",
 "type": "grammar", "reason": "主谓一致；保留温度值与单位",
 "preserve": ["number", "hedge"]}
```

注意：`may be stable` 不改成 `is stable`；`350 C` 可规范为 `350 °C`，
但数值与单位不变。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + 本任务模块；最小编辑、逐条记录、
  DOCX 导出与保真审计。
- 保真检查是启发式筛检，不能替代人工语义判断。
- 只处理离线文本/DOCX/PDF 抽取文本；不联网、不调用外部语法服务。
- 示例是 synthetic 接口样例，不是正式案例答案。
