---
name: review-logic-review-skill
description: 论文逻辑与论证审查：先运行 logic_review.py 对全文做章节/段落/句子分段，抽取作者主张、引用观点与假设，生成 claim_inventory、cross_section_checks、typed findings 和全文覆盖矩阵；再由模型逐条复核，交付可定位到原句、区分错误/证据不足/建议、含可执行修订与误报检查的报告，以及含官方 reading/writing 字段的 result.json。Use for 论文逻辑与论证审查、logic-review、argument mapping、主张-证据对齐、跨章节一致性、结果复核.
---

# 论文逻辑与论证审查（review track · logic-review）

## 用途与触发

task.md 要求“论证链、前提-结论、因果/相关、反例与逻辑跳跃”，或
`/input/case.json` 给出 `review` / `logic-review` 相关字段时使用。
核心交付不是整篇评语，而是：

- **主张清单**：区分作者主张（author）、引用他人观点（cited）与作者假设
  （assumption），标出类型、强度、所在章节与支持状态；
- **跨章节核对**：引言/摘要承诺是否有方法/结果交付，方法口径与结果、
  摘要与正文结论是否一致；
- **typed findings**：每条问题有 `facet/subfacet`、精确 locator、逐字原文、
  `severity`、`type`、`impact`、可执行 `fix` 与 `false_positive_check`；
- **全文覆盖矩阵**：六类官方检查面 + 六个逻辑专项面都有状态，未检查项
  显式列出，不用“通过”掩盖未查。

本 Skill 面向组织方提供的**实际全文科研文本**，不是短摘录。脚本先做确定性
分段与候选生成，模型再做语义复核；脚本不会替模型下“论文有缺陷”的结论。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代同行评审/
机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成 `uncertainties`。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：本题的具体问题、范围、输出要求与证据范围。
- `/input` 下的材料：`.md/.txt/.json/.csv/.tex/.bib/.ris/.yaml` 直接解析；
  `.pdf/.docx` 由 `prepare` 提取后复用 `work/materials.json` 的文本。
  无文字层 PDF/扫描件如实记录，不补造原文。
- 可选：参考文献表、补充材料、术语表、版本/撤稿通知、已有工作文件。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/logic_review.py` | **本题专项预检**：全文分段、主张抽取、跨章节核对、typed 候选、覆盖矩阵；写 `work/logic-check.json` 等 |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/review-findings.json` 的字段契约 |
| `templates/review-report-template.md` | 审查报告骨架（按此写，不照抄结论） |
| `references/review-rules.md` | 主张/证据/推理链/严重度/误报的可执行规则 |
| `examples/synthetic-input.json` | synthetic 工作文件示意（非正式案例） |

## 工作流

### 第 1 步：prepare（约 5% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会盘点 `/input`、提取文本/JSON/CSV、写 `work/inventory.json` 与
`work/materials.json`，生成 `work/review-findings.json` 骨架和 provisional
`result.json`。先读 `work/materials.json`、`work/inventory.json` 与
`task.md`，确认：全文文件、章节是否齐备、是否有参考文献/补充材料。

### 第 2 步：运行全文逻辑预检（约 10% 预算，必须先做）

```bash
python3 "$SKILL_DIR/scripts/logic_review.py" \
  --input /input --work /app/work --output /app/output
```

脚本对**全文**做章节/段落/句子分段（`P1`/`U1` + 行号 locator），并写出：

- `work/logic-check.json`：完整确定性结果（全文分段、主张清单、跨章节核对、
  候选 findings、六面覆盖、逻辑专项面、证据、不确定性、检查项）；
- `work/review-candidates.json`：可合并的候选数组；
- `output/logic-check.json`：随结果包交付的副本；
- `output/logic-precheck.md`：给人看的预检清单。

常用参数：

```bash
# 将候选合并进 work/review-findings.json（一律写成 needs_recheck）
python3 "$SKILL_DIR/scripts/logic_review.py" \
  --input /input --work /app/work --output /app/output --merge
# 三路 synthetic 自测：成功/空输入/缺章节
python3 "$SKILL_DIR/scripts/logic_review.py" --selftest
```

脚本状态：

| `status` / `engine_status` | 含义 | 后续处理 |
|---|---|---|
| `ok_with_candidates` / `ok` | 抽到全文并产生候选 | 逐条人工复核 |
| `ok_no_issues` / `ok` | 未发现确定性候选 | 仍须做语义复核，不能写成“无问题” |
| `ok_with_candidates` / `partial` | 缺方法/结果等章节 | 相关 facet 标 `partial/not_checked`，只审可确认部分 |
| `no_materials` / `input_error` | 没有可读材料 | 诚实降级，不生成伪主张 |
| `error` | 脚本内部异常 | 阅读 `traceback`，人工接管并记录 |

### 第 3 步：人工复核（约 50% 预算，不可跳过）

对 `work/logic-check.json` 逐项处理：

1. **主张清单**：核对 `speaker`。引用观点不等于作者主张；作者明确标注的
   假设（`assumption`）不是缺陷；不要把“引言承诺”当作“结果结论”。
2. **支持状态**：`support` 只用 `full/partial/none/unverifiable`。
   `none` 只表示“本文材料中没有找到可支撑该强度的证据”，不是自动判错；
   还要看作者是否已在局限段声明。
3. **候选 findings**：逐条回原文确认 `original_quote` 逐字可查；接受、
   改写或删除。`status` 用 `confirmed` 或 `needs_recheck`；脚本的
   `candidate` 不能原样保留。
4. **误报检查**：引用观点、作者假设、学科惯例、作者明确的探索性设计不得
   当作作者错误；无法排除时降为 `suggestion` 或 `needs_recheck`。
5. **补语义问题**：脚本未覆盖的隐含前提、替代解释、循环论证、
   论证跳跃、图表与正文矛盾。
6. **跨章节核对**：`consistency` 用
   `delivered/partial/not_delivered/contradicted/unverifiable`；
   `not_delivered/contradicted` 要有承诺与交付两处证据。

规则与严重度见 `references/review-rules.md`；摘要：

| rule_id / 类型 | 含义 | 典型证据 | 默认严重度 |
|---|---|---|---|
| `mediation_without_analysis` | 正文声称中介/机制，但方法明确未做中介分析 | 主张句 + “did not conduct mediation” | major |
| `causal_from_observational_design` | 因果/证明措辞 vs 横断面/观察性/非随机设计 | 结论句 + 方法设计句 | major |
| `generalization_overreach` | 把有限样本外推到所有人群/场景 | 结论句 + 样本描述 | major |
| `circular_definition` | 构念定义与测量共用同一组词 | 定义句 + 测量句 | major |
| `unsupported_result_claim` | 结果段报告干预/改善，但方法没有干预或对照 | 结果句 + 方法句 | major |
| `randomization_inconsistency` | 称随机分配，方法写交替/按楼层分配 | 两处原文 | major（confirmed） |
| `cited_source_misattributed` | 转述他人研究时使用因果/证明措辞，但条目设计不支持 | 转述句 + 条目原文 | major |
| `abstract_result_direction_conflict` | 摘要称显著改善，结果段首个/主要检验不显著 | 摘要句 + 结果句 | major |
| `promise_not_delivered` | 引言承诺的分析/设计未在方法/结果交付 | 承诺句 + 方法/结果句 | major |

### 第 4 步：写 `work/review-findings.json`（约 20% 预算）

按 `templates/work.schema.json` 写结构化工作文件。必填：
`claim_inventory`, `cross_section_checks`, `findings`, `facets_checked`,
`review_status`, `evidence`。建议同时写 `logic_facets`, `checks`,
`rule_review`, `uncertainties`。

- `claim_inventory[]`：`id/claim/type/section/speaker/support/strength/
  locator/evidence/support_basis`；`speaker` ∈ `author/cited/assumption`；
  `support` ∈ `full/partial/none/unverifiable`；`strength` ∈
  `descriptive/associative/suggestive/causal/absolute/assumption`。
- `cross_section_checks[]`：`promise/promise_locator/delivery/
  delivery_locator/consistency/note/evidence`。
- `findings[]`：`id/facet/subfacet/locator/original_quote/severity/type/
  status/impact/fix/false_positive_check/evidence`；`type` ∈
  `error/insufficient_evidence/norm_violation/suggestion`；
  `status` ∈ `confirmed/needs_recheck`。
- `facets_checked[]`：六个官方面（`fact_consistency`, `citation_support`,
  `method_design`, `statistical_reporting`, `argument_chain`,
  `academic_compliance`）各有 `status` 与 `items`；`partial/not_checked/
  not_applicable` 必须写 `reason`。
- `logic_facets[]`：`claim_extraction`, `evidence_support`, `reasoning_chain`,
  `causal_inference`, `cross_section_consistency`, `severity_and_fix`。
- `review_status`：`pass/issues/needs_recheck/not_checked`，表示稿件结论，
  不是流程状态。
- `evidence[]`：每条 `path + locator + quote`，quote 必须逐字可查。

### 第 5 步：写主产物（约 10% 预算）

- `output/review-report.md`：主交付物，按 `templates/review-report-template.md`
  写；必须包含全文覆盖、主张清单、跨章节核对、逐条问题、错误/证据不足/
  建议分栏、待复核/未检查、限制；
- `output/findings.json`：`findings` 数组，字段与工作文件一致；
- `output/evidence-map.json`：`evidence` 数组，含 path/locator/quote；
- `output/logic-precheck.md` 与 `output/logic-check.json` 由预检脚本生成，
  不要删除或改写脚本原始结果；人工补充另写 `output/manual-review.md`。

### 第 6 步：finalize（约 5% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会读取工作文件、检查必填字段与逐字证据、登记真实产物，并写
`output/result.json`（含 `status/result/claims/uncertainties` 与
`deliverable/preserved_facts/evidence_map/change_log`）。`status` 取
`completed|partial|input_error`；缺字段、证据不匹配、主产物缺失时用
`partial`。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/review-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings` 的投影（claim_id/text/evidence）；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote 与所属 claim；
- `artifacts[]`：主产物、`output/findings.json`、`output/evidence-map.json`、
  `output/logic-precheck.md`、`output/logic-check.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`；
- 顶层同时保留 `claim_inventory`、`cross_section_checks`、`findings`、
  `facets_checked`、`review_status`，便于直接检查。

若 `/input/case.json` 的 `output_contract` 含本题特有键，必须保留原键名并
映射到真实字段或同名产物；未覆盖的键会让 finalize 降级 `partial`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 全文分段 | 已运行 `logic_review.py`；`work/logic-check.json` 存在，记录章节/段落/句子数；不得只审摘要 |
| G2 | 主张清单 | 每条含 speaker/support/strength/locator/逐字证据；区分作者主张、引用观点、假设 |
| G3 | 跨章节 | 承诺-交付核对有承诺与交付两处 locator；`not_delivered/contradicted` 有证据 |
| G4 | 主产物 | `output/review-report.md` 存在、非空、含全文覆盖与逐条问题 |
| G5 | 必填字段 | `claim_inventory`, `cross_section_checks`, `findings`, `facets_checked`, `review_status`, `evidence` 齐全 |
| G6 | typed findings | 每条有 facet/subfacet/locator/original_quote/severity/type/status/impact/fix/false_positive_check |
| G7 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查 |
| G8 | 误报与类型 | 引用观点/作者假设/学科惯例未被当作作者缺陷；错误、证据不足、建议分开 |
| G9 | 覆盖 | 六个官方面与六个逻辑专项面都有状态；未检查项写入 `not_checked/uncertainties` |
| G10 | 契约与产物 | 声明的每个 artifact 真实存在；result.json 可解析，case_id 与输入一致 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 全文分段、主张-证据逐条对齐、跨章节承诺-交付、typed findings、可执行修订 | `review-report.md` + `findings.json` + `claim_inventory` |
| 使用增益 | 可复跑的确定性预检、候选生成/复核记录、同条件对照 | `logic-check.json` + `logic-precheck.md` + `rejected` 记录 |
| 创新增益 | argument mapping、speaker 区分、跨章节一致性、严重度判据 | `references/review-rules.md` + `cross_section_checks` |
| 可靠体验 | 空输入、缺章节、长文、扫描件、脚本异常与待复核 | `uncertainties` + `not_checked` + `result.json` |
| 生态贡献 | SKILL.md、schema、报告模板、规则、synthetic 示例可复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪主张/结论；`logic_review.py` 仍写 `no_materials` 且 exit 0 |
| 只有摘要/部分材料 | 只审可确认部分；正文标“基于摘要”；`status=partial` |
| 缺摘要/方法/结果/讨论 | 对应 facet 标 `partial/not_checked` 并写 reason；不把缺失写成通过 |
| 超长全文 | 按章节分块读取；脚本已输出全部段落索引；报告中写清覆盖范围 |
| 参考文献缺失 | 引用观点标 `unverifiable`；写入 `uncertainties`，不猜来源 |
| 引用观点/作者假设 | speaker 标 `cited/assumption`；不作为作者缺陷 |
| 因果 vs 相关 | 先用方法设计句核对；作者已声明局限时降级或标 needs_recheck |
| 图表无文字层 | 记录未读区域；不把“未见”判成“错误” |
| 脚本内部异常 | 读取 `traceback`；人工接管，仍写合法 `result.json` |
| 证据 quote 找不到 | 回原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 代码执行失败 | 保留日志与失败原因；不伪造输出 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-REVIEW",
  "mode": "logic-review",
  "summary": "Synthetic example only.",
  "claim_inventory": [
    {
      "id": "C1",
      "claim": "synthetic claim",
      "type": "causal",
      "section": "Discussion",
      "speaker": "author",
      "support": "none",
      "strength": "causal",
      "locator": {"path": "synthetic.md", "locator": "P1"},
      "evidence": {"path": "synthetic.md", "locator": "P1",
                   "quote": "synthetic source sentence"}
    }
  ],
  "cross_section_checks": [
    {"promise": "synthetic promise", "promise_locator": "P1",
     "delivery": "synthetic delivery", "delivery_locator": "P2",
     "consistency": "not_delivered",
     "evidence": [{"path": "synthetic.md", "locator": "P1",
                   "quote": "synthetic source sentence"}]}
  ],
  "findings": [
    {
      "id": "L001", "facet": "argument_chain", "subfacet": "causal_inference",
      "locator": {"path": "synthetic.md", "locator": "P1"},
      "original_quote": "synthetic source sentence",
      "severity": "major", "type": "error", "status": "needs_recheck",
      "impact": "Synthetic impact.",
      "fix": "Synthetic fix targeting P1.",
      "false_positive_check": "cited view vs author claim checked",
      "evidence": [{"path": "synthetic.md", "locator": "P1",
                    "quote": "synthetic source sentence"}]
    }
  ],
  "facets_checked": [
    {"facet": "argument_chain", "status": "checked",
     "items": ["claim inventory", "promise-delivery"]}
  ],
  "logic_facets": [
    {"facet": "claim_extraction", "status": "checked", "items": ["speaker"]}
  ],
  "review_status": "needs_recheck",
  "checks": [
    {"item": "synthetic check", "status": "not_checked", "detail": "synthetic only"}
  ],
  "evidence": [
    {"path": "synthetic.md", "locator": "P1", "quote": "synthetic source sentence"}
  ],
  "uncertainties": []
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。完整候选流程见
`examples/synthetic-input.json` 与 `scripts/logic_review.py --selftest`。

## 版本与限制

- 版本 `1.1.0`：prepare/finalize 单入口 + `logic_review.py` 全文分段与
  主张/跨章节/typed 候选 + 模型人工复核 + 证据/字段/产物审计。
- 规则是启发式：引用正则、设计关键词、词项重叠可能漏检或误报；所有候选
  必须人工复核，不能把脚本结论直接当最终审稿意见。
- 不联网：外部文献真实性、DOI、撤稿状态只能在 `/input` 材料范围内核验；
  不伪造在线查询结果。
- PDF/DOCX 依赖 prepare 的文本提取；扫描件无文字层时只能交付可确认部分。
- 示例是 synthetic 接口样例，不是正式案例答案。
