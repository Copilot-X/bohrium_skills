---
name: evidence-driven-writer
description: 文献证据驱动写作：模型先写出完整可评阅的 text.md，再由唯一入口把核心断言绑定到可定位来源（source_id/locator/quote），生成 claim-evidence.json 与含官方四字段 deliverable/preserved_facts/evidence_map/change_log 的 result.json；数字、引用与 quote 均可回查，不支持的句子删除或标 marked_as_reasoning。Evidence-driven writing with source locators, verbatim quote checks and citation audits. Use for 证据驱动写作、文献综述、可溯源改写、引用支撑写作、evidence-grounded writing.
---

# 文献证据驱动写作（Evidence-Driven Writer）

## 触发与适用

task.md 出现“每条论断要有出处”“不得超出材料”“依据文献写作”“保留引用和数字”，
或需要写综述/摘要/讨论段、把草稿改成可溯源版本、交付完整正文 + 论断-证据映射 +
引用审计时使用；也用于区分观测/拟合/模型/作者解释、处理相关与因果、证据强度、
冲突结论和材料缺口。不适用：纯创意写作、无需来源的文案、材料缺失仍要求编造。

## 核心原则

1. **正文是主交付物，`text.md` 必须由模型亲自写完整**。本 Skill 不生成抽取式
   保底稿，也不会把拼接材料冒充 completed。
2. 证据表是正文的投影：先有正文，再由入口重建 claim/evidence，而不是先写 JSON
   台账、再倒推正文。
3. 数字、单位、条件、样本量、引用编号和 quote 都要能回查到具体 `path + locator`。
4. 没有任何来源支持的句子：含数字的删除；不含数字的仅保留并标
   `marked_as_reasoning`，不得计入证据链。
5. 官方字段 `deliverable` / `preserved_facts` / `evidence_map` / `change_log`
   一律由入口从真实文件和真实台账生成。

## 输入与输出

- 输入：`/input/task.md`（约束以此为准）、`/input/case.json`（`case_id`、
  `task_instruction`、`output_contract`、可选的 `sources` 提示）、`/input/` 下
  的材料。以实际读到的文件内容为准，不按文件名猜内容。
- 工作区：`/app/work`；交付到 `/app/output`：

| 输出 | 内容 |
|---|---|
| `text.md` | 完整、可编辑、可评阅的主稿（模型写） |
| `claim-evidence.json` | sources / claims / sentences / number_index / citation_index / 审计 |
| `citation-audit.json` | 未知编号、缺条目、孤儿、顺序、句内绑定 |
| `quote-audit.json` | 每条 quote 的逐字/空白折叠/省略号分段核对 |
| `result.json` | 官方四字段 + 状态、覆盖、不确定性、修改记录 |

## 路径解析（先做）

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

后续命令一律使用 `"$SKILL_DIR/..."`。

## 工作流（正文优先）

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/main.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口依次调用 `scripts/inventory.py`（扫描 `/input`，记录路径、sha256、角色、
可读性；Markdown/TXT/JSON/CSV/DOCX 用 stdlib，PDF 依赖运行时 `pdftotext`）和
`scripts/extract_sources.py`（生成 `work/sources.json`：`source_id`、`path`、
`kind`、`scope`、`title`、**`citation_number`**、`text_path`、带行号/页码的
`units[]`）。缺 DOI/年份/作者时只记缺口，不编造。

若此时还没有 `text.md`，入口写 `status=partial` 的 `result.json` 并正常退出，
提示继续写稿；**不会**生成替代正文。

### 第 2 步：写完整 `text.md`（约 60% 预算，最重要）

打开 `work/sources.json`，按其中分配的 `citation_number` 写
`/app/output/text.md`：

- 覆盖 task.md 的每个章节/问题，达到目标篇幅、体裁和受众要求；
- 每个关键数字、单位、条件、样本量、比较对象都能回指某个 source unit；
  材料没有的数字不写，材料不足写“现有材料未报告……”；
- 引用写成 `[n]`，`n` 必须是 `work/sources.json` 里的 `citation_number`，
  并放在支撑该句的位置；文末条目可由入口补齐；
- 相关不写因果，单篇小实验不写领域共识，摘要不写“全文表明”；
- 冲突结论并列呈现各自条件，不取平均、不选边；
- 推断用“可能/提示/尚不能排除/may/suggest”等限定语。

写正文时直接引用原文措辞（尤其中英文数字与条件），便于后续逐字核对。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/main.py" finalize \
  --input /input --work /app/work --output /app/output \
  --draft /app/output/text.md
```

入口会：

1. 为正文引用但缺条目的来源补 `[n]` 材料定位条目（只写标题/路径，不编造作者、
   期刊或 DOI）；
2. 用 `scripts/claims.py` 重新分句并绑定 `source_id + locator + quote`；含数字且
   无来源的句子删除，不含数字且无来源的句子保留但标 `marked_as_reasoning`；
3. 用 `scripts/citecheck.py` 写 `citation-audit.json`，用
   `scripts/verify_quotes.py` 写 `quote-audit.json`；
4. 用 `scripts/build_result.py` 写 `work/evidence-map.json`、
   `output/claim-evidence.json` 和 `output/result.json`，并校验所有 artifact 路径。

`run` 等价于 `prepare + finalize`，适合一次跑完：

```bash
python3 "$SKILL_DIR/scripts/main.py" run \
  --input /input --work /app/work --output /app/output
```

## 支持文件（相对 `$SKILL_DIR`）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `scripts/main.py` | 唯一入口 | `prepare` / `finalize` / `run`，只做编排 |
| `scripts/evidence_kit.py` | 任务专用共享库 | 被其余脚本 `import`；提供解码、分句、引用/数字解析、概念匹配、DOCX/PDF 抽取 |
| `scripts/inventory.py` | 读输入 | 可单跑：`--input /input --work /app/work` |
| `scripts/extract_sources.py` | 来源台账与 locator | 可单跑：读 `work/inventory.json`，写 `work/sources.json` |
| `scripts/claims.py` | 断言拆分/证据绑定 | 通常由入口调用；也可 `--work --output --text` 单跑 |
| `scripts/citecheck.py` | 引用审计 | 由入口调用；也可 `--work --output` 单跑 |
| `scripts/verify_quotes.py` | quote 逐字核对 | 由入口调用；也可 `--work --input --output` 单跑 |
| `scripts/build_result.py` | 官方字段投影 | 由入口调用；也可 `--work --output --input` 单跑 |
| `references/claim-splitting.md` | 断言如何拆、证据如何分层 | 写正文前和复核 `claims[]` 时按节查阅 |
| `references/hedging-language.md` | 相关 vs 因果、证据强度与限定语 | 决定动词强度和结论边界时查阅 |
| `references/self-check.md` | 交付前逐项复核 | finalize 后按清单核对，不整段抄进正文 |

## 官方输出契约

`result.json` 保留输入字段，并保证以下顶层字段真实：

```json
{
  "case_id": "与输入一致",
  "task_family": "evidence-driven-writing",
  "status": "completed|partial|input_error",
  "deliverable": {"path": "text.md", "format": "markdown", "language": "zh",
                  "word_count": 0, "genre": "review", "sections": []},
  "preserved_facts": [{"fact_id": "C01", "text": "…", "value": "92",
    "unit": "%", "condition": "350 °C; 1 bar",
    "source": {"path": "materials/paper.md", "locator": "L12"},
    "citation_numbers": [1], "in_draft": true}],
  "evidence_map": [{"claim_id": "C01", "status": "supported",
    "evidence": [{"source_id": "SRC01", "source": {"path": "materials/paper.md",
      "locator": "L12"}, "quote": "…", "supports": "direct"}]}],
  "change_log": [{"step": 1, "action": "claims_built", "reason": "…", "evidence": "…"}],
  "uncertainty": [], "output_contract": {}, "contract_coverage": []
}
```

规则：

- `deliverable.path` 必须指向真实 `text.md`；`artifacts[]` 每个路径必须存在；
- `preserved_facts` 覆盖正文关键数字、单位、条件、样本量与引用编号，逐条带
  source `path + locator`；
- `evidence_map` 每条 evidence 必须有 `source.path + locator + quote`；
- `change_log` 只记录真实发生的动作（inventory、绑定、删除/标记、补条目、审计）；
- `status=completed` 要求没有 unsupported、citation error、quote error；否则用
  `partial` 并在 `uncertainty` 写原因、影响和下一步；
- `input_error` 表示没有可读材料：`deliverable=null`，不生成正文、不编造引用；
- `output_contract` 原样复制；`contract_coverage` 逐字段说明是否满足。

## 命令与退出码

- `prepare` / `finalize` / `run` 在 partial 或 input_error 时**仍写
  `result.json` 并 exit 0**；只有完全无法启动（参数错误、安装不可用）才 exit 2。
- `citecheck` / `verify_quotes` 发现问题只影响 `status` 与 `uncertainty`，不阻塞
  主稿和 `result.json` 落盘。

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料只有摘要 | 只写摘要能支撑的内容；正文标“基于摘要”；不写“全文表明” |
| 材料为空或路径缺失 | `status=input_error`、`deliverable=null`、不生成正文、不编造 |
| 文献结论冲突 | 正文并列 + 条件对比；人工登记 `conflicts[]`；不取平均、不选边 |
| task.md 要求超出材料 | 该部分写缺口，进入 `unsupported[]` / `uncertainty`；不用常识补全 |
| PDF/Office 无法抽取 | 记录 `readable=false`；用运行时 PDF/OCR 工具；关键数字人工复核 |
| 引用编号错乱/quote 非逐字 | 按审计报告重编号、改回原文或删句，改动写入 `change_log` |
| 句子无来源且含数字 | 删除；不含数字则保留并标 `marked_as_reasoning` |
| 入口内部异常 | 先保留已落盘文件，写 `status=partial` 的 `result.json`；重跑 finalize |

## 最小调用示例（示意，不是正式答案）

输入形状（由平台提供，此处仅示意）：

```json
{"case_id": "CASE-X", "task_instruction": "根据材料写 400 字综述",
 "output_contract": {"deliverable": "…", "preserved_facts": "…",
                     "evidence_map": "…", "change_log": "…"}}
```

```bash
python3 "$SKILL_DIR/scripts/main.py" prepare \
  --input /input --work /app/work --output /app/output
# 读完 /app/work/sources.json 后，模型写 /app/output/text.md
python3 "$SKILL_DIR/scripts/main.py" finalize \
  --input /input --work /app/work --output /app/output \
  --draft /app/output/text.md
```

示意输出（字段级别，数值随案例变化）：`status=completed`；
`deliverable.path=text.md`；`preserved_facts[]` 含数字与 `source.locator`；
`evidence_map[]` 含 `quote + supports`；`citation-audit.json` 与
`quote-audit.json` 的 `ok=true`。示例只说明接口，不冒充正式案例结果。

## 自检

- [ ] `text.md` 是模型写的完整正文；不是提纲、不是抽取式拼接；
- [ ] `result.json` 可解析、`case_id` 正确、官方四字段齐全且路径真实；
- [ ] `claim-evidence.json` 至少覆盖核心断言，每条 claim 有 evidence；
- [ ] 关键数字/单位/条件/引用可回查；`number_index` 中 `found=false` 为 0；
- [ ] `citation-audit` 无 unknown / missing entry；`quote-audit` 无 not_found；
- [ ] 不支持句已删除或标 `marked_as_reasoning`；
- [ ] 缺口/冲突/未解决问题写入 `uncertainty`。

## 版本与限制

- 版本：`0.14.0`（最小包、单入口、模型写正文、证据/引用引擎只做校验与投影）。
- 脚本 stdlib-only、无网络；PDF 抽取依赖运行时 `pdftotext`，不可用则只记缺口。
- 证据绑定基于引文编号、数字和双语关键词/概念重叠，可审计但不等于语义蕴含；
  quote 存在也不自动证明 claim 正确，必须人工回读。
- `conflicts[]` 不做自动语义判定；冲突证据需人工登记。
- 任何脚本/路径/模板失败都不阻塞主稿与 `result.json` 落盘；审计结论写入
  `uncertainty`，不伪造通过记录。
