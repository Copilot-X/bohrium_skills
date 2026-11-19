---
name: atomic-assertion-nanopublisher
description: Nanopublication：把论文结论拆成单条可判真假的原子断言，用受控关系词表连接 subject/relation/object，为每条绑定逐字最小证据与段落 locator，补齐论文、作者、年份、DOI、版本等 provenance，交付 nanopublications.json、assertions.json、assertions.csv、assertions.md 与 quality-report.json。Atomic claim decomposition with controlled relations, verbatim evidence and machine-readable nanopublications. Use for Nanopublication、原子断言、断言拆解、证据绑定、来源元数据、受控词表、机器可读导出、assertions.json.
---

# Nanopublication（Atomic Assertion Nanopublisher）

## 用途与触发

当 task.md 要求“把论文结论拆成断言/生成 nanopublication/为断言绑定
证据/输出可机器读取的知识单元/检查断言粒度与来源”，或
`/input/case.json` 的 `output_contract` 出现 `nanopublications`、
`assertions`、`quality_report` 时使用。

适用：论文摘要、结果段、讨论段的结论抽取；需要逐条回溯到原文的
知识结构化任务。不适用：纯翻译、章节摘要笔记（见结构化笔记 Skill）、
没有原文的开放式知识问答。

核心原则：**拆得细、对得上、追得回、读得懂**。
脚本负责段落索引、断言审计、证据回查、nanopublication 序列化与
`result.json`；模型负责判断哪些句子构成可验证断言、主体关系方向是否
正确、哪一段是最小充分证据。**脚本不会替你写断言**；
`work/assertions.json` 必须由模型按本文件契约写出。

## 输入

- `/input/case.json`：`case_id`、可选 `title/doi/authors/year/version`、
  `output_contract`；字段原样保留。
- `/input/task.md`：断言条数下限、必须覆盖的章节、语言、导出要求。
- `/input` 下的论文：`.md/.txt/.tex/.docx/.pdf`。prepare 选择**可读文本
  最长**的文档，按空行/标题切段为 `P001…`；PDF 抽取失败或扫描页无文字层
  会写 warning，不猜文字。
- 可选：已有断言表、领域本体/关系映射表、术语表。只使用输入中真实
  出现的元数据，不联网补全。

运行目录约定：

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 切段、逐字引文校验、数字抽取、`result.json` 信封 |
| `scripts/parsers.py` | Markdown/TXT/TeX/DOCX/PDF 文本提取与 OCR 兜底 |
| `scripts/fam_nanopub.py` | 本任务的段落索引、断言审计、nanopublication 与 CSV/MD 导出 |
| `task.json` | 指定 `module=fam_nanopub`、`profile=reading`、family |
| `templates/assertions.schema.json` | `work/assertions.json` 字段契约（模型必读） |
| `templates/nanopublication.schema.json` | 输出 nanopublication 的字段字典 |
| `references/assertion-rules.md` | 原子性判据、受控词表、证据最小性与失败模式 |
| `examples/synthetic-paper.md` | synthetic 论文，仅演示输入形态（非正式案例） |
| `examples/assertions.example.json` | 与 synthetic 论文配套的断言示例（非正式答案） |

使用顺序：先读 `templates/assertions.schema.json` 与
`references/assertion-rules.md`，再写断言；`examples/` 只用于核对接口
形状，**不得把示例实体、数值或 DOI 当作正式案例答案**。

## 工作流（prepare → 模型写 assertions.json → finalize）

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

在 period4 仓库内本地运行时：

```bash
python -X utf8 period4/shared/scripts/run_case.py prepare \
  --input <input> --work <work> --output <output> \
  --skill-dir period4/skills/product-arena-09-0e8befd4
```

入口会写出：

- `work/inventory.json`、`work/context.json`：输入清单与 case/契约；
- `work/paragraphs.json`：`paragraphs[]`，每段含
  `paragraph_id`（`P001…`）、`locator`（`<path> P001`）、`text`、`page`；
  `metadata` 含 `paper_id/title/authors/year/doi/version/source_path`；
- `work/assertions-template.json`：单条断言的完整骨架与
  `relation_vocabulary`（35 个受控关系）；
- `output/result.json`：`status=partial` 的 provisional 信封。

若没有可读论文，prepare 直接写 `input_error` 并退出 0。不要编造段落或
元数据；入口会在 `result.json` 的 `uncertainty` 说明缺什么材料。

### 第 2 步：模型写 `/app/work/assertions.json`（约 60% 预算）

1. 读 `work/paragraphs.json`，逐段找出可独立判真假的结论句；
2. 把复合句拆成单条 subject–relation–object 断言；
3. 为每条断言选择**同一段内**的最小充分证据；
4. 从 `relation_vocabulary` 选择关系，方向与原文一致；
5. 复制 `metadata` 作为 `source_metadata`（或用 case.json 中更权威的值）；
6. 无证据/无法判断的候选写入 `unresolved`，不要硬凑。

字段契约见 `templates/assertions.schema.json`。最小形态：

```json
{
  "case_id": "与输入一致",
  "source_metadata": {
    "paper_id": "case_id",
    "title": "论文标题",
    "authors": ["A. Author"],
    "year": "2024",
    "doi": "10.xxxx/xxxx",
    "version": "1",
    "source_path": "paper.md"
  },
  "assertions": [
    {
      "id": "A001",
      "assertion": "一条单句、可判真假的断言",
      "subject": {"label": "主体名词短语", "type": "material"},
      "relation": "INCREASES",
      "object": {"label": "客体名词短语", "type": "metric"},
      "conditions": ["350 °C", "1 bar"],
      "evidence": {
        "path": "paper.md",
        "locator": "paper.md P010",
        "quote": "该段中逐字出现的最小证据片段"
      },
      "evidence_type": "direct",
      "confidence": "moderate",
      "limitations": ["证据的边界或未测量项"]
    }
  ],
  "unresolved": [
    {"item": "未能绑定证据的候选断言", "reason": "原文未给出直接支撑",
     "action": "保留为待人工确认"}
  ]
}
```

硬规则（详见 `references/assertion-rules.md`）：

- **原子性**：一条断言只表达一个 subject–relation–object；
  长度 ≤420 字符；避免 `;` 与多个 `and/but/while/whereas/although`
  （2 个及以上会被脚本记为 atomicity warning，并降级为 `partial`）。
- **受控关系**：`relation` 必须是 35 个允许值之一（schema 枚举为大写，
  请直接写大写形式；脚本对大小写不敏感，非字母数字会规范成 `_`）。
  不要发明 `AFFECTS`、`RELATED_TO` 等词。
- **主体/客体必须有 `label`**；方向不能反（谁作用于谁、谁优于谁）。
  `type` 使用 `material|method|finding|metric|organism|process`。
- **证据最小充分**：`quote` 必须是 `locator` 所指段落中的逐字连续片段；
  含主体、方向、关键数值和条件，不整段照抄；不得改写后当 quote。
  `locator` 直接使用 `paragraphs[].locator`（`paper.md P010`）或
  `paragraph_id`（`P010`），不要写页码猜测。
- **单段证据**：一条断言的 quote 应落在同一段内；跨段证据拆成两条断言
  或只保留最决定性的一段，其余写入 `conditions/limitations`。
- **来源真实**：`source_metadata` 只用输入中真实出现的 title/DOI/
  authors/year/version；`path` 用 `paragraphs.source_path`。
- **证据类型与置信度**：`direct|indirect|inferred` 与
  `high|moderate|low` 要能由证据和限制解释；`inferred` 不得写成
  `direct`，相关性不得写成因果。
- **不重复**：同一 subject+relation+object+conditions 只保留一条；
  `id` 用 `A001…` 且唯一。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/assertions.json`；缺失或 `assertions` 为空时写
   `status=partial` 并说明“assertions.json missing or empty”，
   **不生成任何交付物**；
2. 逐条校验：唯一 id、非空 assertion、subject/object label、
   受控关系、`atomicity_issues`、quote 是否能由指定段落支撑；
3. 生成 `output/nanopublications.json`：每条断言一个确定性
   `np:` 标识（`sha256(case_id + id + assertion)` 前 24 位），
   含 assertion、provenance、publication_info；
4. 生成 `output/assertions.json`（含规范化 relation 与
   `atomicity_issues`）、`output/assertions.csv`、
   `output/assertions.md`；
5. 生成 `output/quality-report.json`：problems、atomicity_warnings、
   relation_vocabulary、all_atomic；
6. 写 `output/result.json`，`deliverable` 指向
   `nanopublications.json`。

`status` 取 reading 枚举 `answered|partial|insufficient_evidence|input_error`：
**没有 problem 且没有 atomicity warning** 时为 `answered`；否则
`partial`，并在 `uncertainty[]` 给出可修复动作。DOI 与 title 同时缺失
只记录 uncertainty，不单独降级。

## 输出契约

`/app/output` 下必须存在：

| 文件 | 内容 |
|---|---|
| `nanopublications.json` | 主交付物：`case_id/source_metadata/nanopublications[]/count` |
| `assertions.json` | 规范化断言 + `source_metadata` + `unresolved` |
| `assertions.csv` | 列：`id,subject,relation,object,assertion,evidence_path,locator,quote,evidence_type,confidence` |
| `assertions.md` | 人类可读的逐条断言与证据 |
| `quality-report.json` | `case_id,count,problems,atomicity_warnings,relation_vocabulary,all_atomic` |
| `result.json` | 官方信封 |

单条 nanopublication 结构：

```json
{
  "nanopub_id": "np:0123456789abcdef01234567",
  "assertion": {
    "subject": {"label": "oxygen vacancies", "type": "material"},
    "relation": "INCREASES",
    "object": {"label": "methanol selectivity", "type": "metric"},
    "text": "一条单句断言",
    "conditions": ["350 °C"]
  },
  "provenance": {
    "source_id": "case_id", "title": "...", "authors": ["..."],
    "year": "2024", "doi": "10.xxxx/xxxx", "version": "1",
    "path": "paper.md", "locator": "paper.md P010",
    "quote": "逐字证据", "evidence_type": "direct"
  },
  "publication_info": {
    "case_id": "...", "assertion_id": "A001",
    "confidence": "moderate", "limitations": ["..."]
  }
}
```

`result.json` 关键字段：

```json
{
  "case_id": "与输入一致",
  "status": "answered",
  "task_family": "nanopublication",
  "profile": "reading",
  "deliverable": {"path": "nanopublications.json", "format": "json",
                  "exists": true},
  "result": {
    "summary": "6 atomic assertions with evidence and provenance.",
    "source_metadata": {},
    "checks": {"count": 6, "problems": [], "atomicity_warnings": [],
               "all_atomic": true, "relation_vocabulary": ["..."]}
  },
  "claims": [{"claim_id": "A001", "text": "...", "evidence": {}}],
  "evidence_map": [{"claim_id": "A001", "claim": "...",
                    "source": {"path": "paper.md", "locator": "paper.md P010"},
                    "quote": "...", "supports": "direct"}],
  "preserved_facts": [{"kind": "number", "source": "92%", "target": "92%",
                       "locator": "paper.md P010", "value": "92%", "unit": ""}],
  "uncertainty": [],
  "change_log": [],
  "artifacts": [{"path": "nanopublications.json", "role": "deliverable",
                 "exists": true}],
  "output_contract": {},
  "contract_coverage": [],
  "artifact_paths_valid": true
}
```

`artifacts[].path` 是相对 `/app/output` 的真实路径；`np:` 标识由入口
确定性生成，模型不要自造。`preserved_facts` 记录每条证据 quote 中出现的
数字 token（每条断言最多 20 个），用于数值复核；`evidence_map` 与
`claims` 逐条对应 assertions.json 的顺序。

## 质量闸门

| # | 检查 | 通过标准 | 由谁判定 |
|---|---|---|---|
| G1 | 原子性 | `quality-report.json` 的 `all_atomic=true`，无 warning | finalize（启发式） |
| G2 | 受控关系 | 每条 `relation` 在 35 个允许值中 | finalize 判 problem |
| G3 | 主体/客体 | 两者 `label` 非空，方向正确，type 合理 | finalize 判 label；方向靠模型/人工 |
| G4 | 证据回查 | quote 在指定段落中逐字存在；locator 指向真实段 | finalize 判 quote（命中段落时按段校验；未命中时退化为全文校验且不报错，故 locator 需自检） |
| G5 | 来源完整 | `source_metadata` 有 title 或 DOI；每条 provenance 带 path/locator/quote | finalize 仅在 title 与 DOI 同时缺失时记 uncertainty；完整性靠自检 |
| G6 | 机器可读 | 5 个输出可解析；CSV 列齐全；JSON 中无注释 | 入口生成 |
| G7 | 唯一性 | id 不重复；nanopub_id 确定性且唯一 | finalize 判重复 id |
| G8 | 契约 | result.json 可解析，status 属于 reading 枚举，artifact 路径真实 | 入口生成 |

注意：G1 是启发式（长度/并列连接词/分号），不能识别所有复合语义；
G3 的方向与 G5 的元数据真实性仍需人工复核。

## 异常处理

| 情况 | 处理 |
|---|---|
| 无文本层/无主论文 | prepare 写 `input_error`；不生成伪断言 |
| `assertions.json` 缺失/为空 | finalize 返回 `partial`，不生成交付物；按 schema 重写后重跑 |
| relation 不在词表 | 映射到最接近的允许关系；无法映射则写入 `unresolved`，不要新造词 |
| quote 找不到 | 该条计入 problem → `partial`；从指定段落复制逐字片段 |
| quote 跨段 | 拆成两条断言，或保留单段决定性证据，其余写 conditions/limitations |
| 一条断言含并列/转折多个命题 | 拆分；拆出的每条都要有自己的证据；否则保持 `partial` |
| subject/object 缺失 | 补全名词短语与 type；无客体则改写为单主体断言或 unresolved |
| 论文未给 DOI/title | 保留空串；finalize 记录 `source_metadata` uncertainty，不编造 |
| 扫描 PDF 无文字层 | OCR 失败则 `input_error` 或 `partial`；在 `limitations` 或 `unresolved` 标注页码，入口会记录抽取 warning |
| 时间不足 | 先交付少量高质量断言并 finalize；诚实保留 `partial`，不凑数 |
| 入口脚本异常 | 读 `work/main-error.json`；已写 assertions.json 不丢，修复后重跑 |

## 最小示例（synthetic，不是正式答案）

synthetic 段落：

```markdown
## 3 Results
The uncoated cell retained 71% capacity after 100 cycles. The coated cell
retained 88% capacity under the same conditions.
```

对应 `work/assertions.json` 片段：

```json
{
  "case_id": "SYNTH-NANOPUB-EXAMPLE",
  "source_metadata": {"paper_id": "SYNTH-NANOPUB-EXAMPLE",
                      "title": "Silica Coating Reduces Capacity Fade in LiNiO2 Cathodes",
                      "authors": ["C. Example", "D. Sample"], "year": "2026",
                      "doi": "10.9999/synth.2026.002", "version": "1",
                      "source_path": "paper.md"},
  "assertions": [
    {"id": "A001",
     "assertion": "Silica coating decreases capacity fade in LiNiO2 cathodes after 100 cycles.",
     "subject": {"label": "silica coating", "type": "material"},
     "relation": "DECREASES",
     "object": {"label": "capacity fade", "type": "metric"},
     "conditions": ["100 cycles", "1 C"],
     "evidence": {"path": "paper.md", "locator": "paper.md P008",
                  "quote": "The uncoated cell retained 71% capacity after 100 cycles. ... The coated cell retained 88% capacity under the same conditions."},
     "evidence_type": "direct", "confidence": "high",
     "limitations": ["One coated and one uncoated cell were compared."]}
  ],
  "unresolved": []
}
```

finalize 后应核对：`status=answered`；`deliverable.path` 为
`nanopublications.json`；`count=1`；`all_atomic=true`；CSV 有一行数据。
该示例只演示接口，**不是正式案例答案**。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_nanopub` 模块；prepare 写
  provisional result，官方字段由入口按真实断言生成。
- 原子性、quote 回查、关系词表均为确定性/启发式检查，不能替代领域专家
  对语义、方向与因果强度的复核。
- 段落切分基于空行与标题；PDF 双栏错序可能把两段并成一段，此时 quote
  仍可回查，但 locator 粒度需人工确认。
- 证据校验优先按 locator 对应段落；locator 不在段落索引中时会退化为
  全文 quote 检查，可能通过但 provenance 较弱——提交前必须修正 locator。
- finalize 的 quote 校验不使用 `evidence.path`；path 只写入 provenance，
  因此必须自行确保它等于 `paragraphs.source_path`（通常是 `paper.md`）。
- 不联网、不调用外部本体服务；关系词表固定为 35 个值。
- 示例是 synthetic 接口样例，不是正式案例答案；不要复制其中实体或 DOI。
