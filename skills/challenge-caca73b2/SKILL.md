---
name: results-discussion-writer
description: 结果与讨论写作：从原始数据、表格与图写 Results 和 Discussion，结果只报告实测值（含单位、n、误差类型、显著性），讨论在限定范围内解释且不越界因果。Write Results and Discussion from raw data with exact values and bounded interpretation, producing results-discussion.md and a claim table. Use for 结果段、讨论段、数据解读、论文核心章节。
---

# 结果与讨论章节写作（Results & Discussion Writer）

## 用途与触发

输入含原始数据/图表/统计结果，task.md 指定写"结果""讨论"或两者时使用。
这两节最容易被扣分的地方：把趋势写成因果、把未检验的差异写成"显著"、
讨论里冒出结果中没有的数据。

## 支持文件（按需使用，不阻塞）

先解析技能目录，不要照抄字面量 `{SKILL_DIR}`：

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
test -n "$SKILL_DIR" && echo "SKILL_DIR=$SKILL_DIR"
```

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/results-discussion-playbook.md` | 建结果台账、写 Results/Discussion 前 | 先读；按分离/强度/局限规则执行 |
| `templates/results-discussion-plan.json` | 计划与 claim 表骨架 | 复制后填 results/interpretations/limitations |
| `scripts/check_results_discussion.py` | 可选审计 | `python3 "$SKILL_DIR/scripts/check_results_discussion.py" --result /app/output/result.json --manuscript /app/output/results-discussion.md --root /app/output --json` |

校验器检查数值的 n/误差/来源、解释的 based_on 与强度、局限是否具体、section/artifact。
**它是可选审计，不是闸门**：失败时继续手工核对，把 `audit_unavailable` 写入
`uncertainty`；不要为了让校验通过而补造 n 或 p 值。

## 输入

- `/input/task.md`：章节范围、字数、是否合并、引用格式、语言
- `/input/case.json`：`case_id`、`data_files[]`、`figures[]`、`output_contract`
- 数据：`.csv/.xlsx/.json`；图：`.png/.pdf/.svg` + 图注
- 可选：统计输出（检验方法、p 值、置信区间）、方法节

## 执行步骤（交付物优先）

1. **读任务与数据（约 15% 预算）**：读 task.md/case.json，确认章节范围、字数、
   语言、是否合并 Results/Discussion。解析原始表，写 `/app/work/data-profile.json`
   （列名、单位、组别、n、缺失值、异常值）。
2. **建数据-正文对照表（约 20% 预算）**：每条可报告的结果一行，先落盘
   `/app/work/result-table.json`：
   ```json
   {"result_id": "R01", "quantity": "conversion", "value": 92.1, "unit": "%",
    "group": "catalyst A", "condition": "350 °C, 1 bar", "n": 3,
    "error": {"type": "SD", "value": 1.8},
    "test": {"name": "one-way ANOVA", "p": 0.003, "posthoc": "Tukey"},
    "source": {"path": "materials/results.csv", "locator": "rows 4-6"},
    "figure": "Fig.2a"}
   ```
   没有的字段写 `null`，**不猜**；误差类型不确定时写 `"type": "not_stated"`。
3. **先写完整 Results（约 30% 预算）**：只陈述，不解释。
   - 顺序：先主结果，再次要结果，再阴性/异常结果；
   - 每段结构：变量 → 数值（±误差）→ 条件 → 对照/统计；
   - 每段至少引用一张图表，且与图表编号一致；
   - 不出现“表明”“提示”“由于”等解释性词。
   落盘 `/app/output/results-discussion.md`（至少包含完整 Results 部分）。
4. **写 Discussion 并补全（约 25% 预算）**：
   - 第 1 段：用一句话回答引言的核心问题（只用 Results 有的数据）；
   - 第 2–3 段：与文献比较（引用必须真实存在）；
   - 第 4 段：机制解释，明确标为推测；
   - 第 5 段：局限性（样本量、条件范围、未测变量、统计功效）；
   - 第 6 段：结论与意义，不超出数据边界。
5. **一致性与结构化结果（约 10% 预算）**：Results 的每个数值在 Discussion 复现时
   逐字一致；Discussion 不出现 Results 没有的新数据。写 `claim-table.json`、
   `result.json`，包含 `deliverable`、`preserved_facts`、`evidence_map`、`change_log`。
6. **可选审计（约 5% 预算）**：运行 `check_results_discussion.py`；失败不阻塞，
   记录 `audit_unavailable`，手工核对数值的 n/误差/来源、解释的 based_on/强度、
   局限具体性与 section/artifact。

## 官方输出契约（必须补齐）

官方 `example-input` 的 `output_contract` 要求顶层字段：`deliverable`、`preserved_facts`、`evidence_map`、`change_log`。
题目原有字段全部保留；交付前运行：

```bash
python3 "$SKILL_DIR/scripts/emit_official.py" \
  --result /app/output/result.json --out /app/output/result.json \
  --profile writing --json
```

命令失败时手工补 `deliverable/preserved_facts/evidence_map/change_log` 四个字段即可，
不影响交付；`validate_result.py` 也是可选审计。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "results-discussion",
  "status": "completed|partial",
  "sections": [
    {
      "id": "results",
      "path": "results-discussion.md#results",
      "words": 0,
      "results": [
        "R01",
        "R02"
      ],
      "figures": [
        "Fig.2a",
        "Fig.2b"
      ]
    },
    {
      "id": "discussion",
      "path": "results-discussion.md#discussion",
      "words": 0,
      "answers": "Q1",
      "citations": [
        "ref3",
        "ref7"
      ]
    }
  ],
  "results": [
    {
      "result_id": "R01",
      "statement": "…",
      "value": "92.1",
      "unit": "%",
      "condition": "350 °C, 1 bar",
      "n": 3,
      "error": {
        "type": "SD",
        "value": "1.8"
      },
      "significance": {
        "test": "Tukey",
        "p": "0.003",
        "mark": "**"
      },
      "source": {
        "path": "materials/results.csv",
        "locator": "rows 4-6"
      },
      "figure": "Fig.2a",
      "kind": "reported"
    }
  ],
  "interpretations": [
    {
      "interpretation_id": "I01",
      "text": "…",
      "based_on": [
        "R01",
        "R04"
      ],
      "strength": "supported|suggested|speculative",
      "citations": [
        "ref3"
      ]
    }
  ],
  "limitations": [
    "仅测试 300–400 °C 区间",
    "n=3，未做功效分析"
  ],
  "artifacts": [
    {
      "role": "editable",
      "path": "results-discussion.md",
      "format": "markdown"
    },
    {
      "role": "evidence",
      "path": "claim-table.json",
      "format": "json"
    }
  ],
  "support_files_used": [
    "references/results-discussion-playbook.md",
    "scripts/check_results_discussion.py"
  ],
  "uncertainty": [
    {
      "item": "误差类型",
      "reason": "图注未说明",
      "impact": "写作中标注 not_stated"
    }
  ],
  "change_log": [],
  "deliverable": {
    "path": "…",
    "format": "markdown"
  },
  "preserved_facts": [],
  "evidence_map": []
}
```

## 写作规则

1. 数值格式：`92.1% ± 1.8 (SD, n=3)`；百分比与百分点分清。
2. 统计：只有材料给了检验才写"显著"，并给检验名与 p 值；否则写"数值上更高"。
3. 相关≠因果；机制句一律 `speculative` 或 `suggested`。
4. 阴性结果照写，不藏；异常值说明处理方式（保留/剔除及理由）。
5. 比较对象必须同条件；跨条件比较要显式说明不可直接比较。
6. 图表引用用编号，不用"下图""左图"。
7. 讨论中的每个论断在 `interpretations` 里有 `based_on`。

## 异常处理

| 情况 | 处理 |
|---|---|
| 数据表无单位 | 保留原值，标 `unit: "not_stated"`，正文不自行补单位 |
| 只有图没有原始数据 | 读数 + `estimated: true` + 误差范围 |
| 统计输出缺失 | 只描述数值差异，不做显著性断言 |
| 重复测量/嵌套设计 | 说明结构，提示需混合模型；不简化成 t 检验 |
| 结果与图注冲突 | 并列记录，正文采用数据表值并注明 |

## 自检

- [ ] 每个数值有来源定位、条件、n、误差类型
- [ ] Results 无解释性词，Discussion 无新数据
- [ ] 显著性陈述与统计输出一致
- [ ] 每个解释都有 `based_on` 与 strength
- [ ] 局限性具体（限制 + 影响 + 缓解），不是“样本量有限”套话
- [ ] 图表编号与正文一致
- [ ] 主交付物 `results-discussion.md` 已完整落盘（Results + Discussion）
- [ ] `check_results_discussion.py` 为可选审计；失败时已记录 `audit_unavailable`

## 快速路径（预算内）

1. **0–2 分钟**：读数据/图/表，建结果台账（数值+条件+n+误差）。
2. **2–7 分钟**：先写完整 Results，只报告不解释，落盘。
3. **7–13 分钟**：写 Discussion，逐条解释并对照文献；补全主稿。
4. **13–14 分钟**：写 `claim-table.json`、`result.json` 与官方别名。
5. **最后 1 分钟**：可选跑 checker；核对数值与图表一致。

## 方法增强（融合参考实践）

- **结果台账**：每图每表每条数值记 `value / unit / n / error_type / condition / source`；写作只从台账取数。
- **Results 只报告**：现象、数值、条件、对照；机制与文献比较一律留到 Discussion。
- **Discussion 三段式**：主要发现 → 与文献一致/不一致及可能原因 → 局限与意义。
- **解释分层**：`data_supported`（数据直接支持）/ `literature_supported`（有文献）/ `speculative`（推测，须明示）。
- **对照文献**：一致处说一致，冲突处给条件差异，不选择性忽略反例。
- **局限具体**：写清样本、条件、方法学限制及其对结论的影响，不写“样本量有限”套话。
- **claim 表**：每条结论挂结果 id 与证据强度，供全文复用。

### 定向方法增强

- **来自 Discussion Section Architect**：Discussion 固定六段——重述问题与主发现（2–3 句）→ 机制/理论解释 → 与文献一致或冲突 → 理论/实践意义 → 局限与未来方向 → 结论；每段只做一件事。
- **来自 Discussion Section Architect**：局限按“限制 → 对解释的影响 → 缓解/未来方向”三件套写，既诚实又不自我否定；意外结果与阴性结果必须显式解释，不得只报显著结果。
- **来自 Discussion Section Architect**：Draft → Check（7 项）→ Revise（只改失败段落）→ Re-check；7 项含“Results 每个发现都被讨论、论断有引用或推理、未引入新数据、限定语适当、结论回到原问题”。
- **来自 paper-writer**：无法核实的来源写 `[需要来源]`，不得编造 DOI 或文献；Discussion 中不得出现 Results 没有的新数据；合规/核验信息放在回复里，不写进论文正文。
- **两篇合取后的改造**：`claims[]` 增加 `source_status ∈ {verified, source_needed, reasoning}`；新增“无新数据”闸门与“阴性/意外结果已解释”闸门。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 数值 | 每个数值有单位、n、误差类型 | 补全或删除该数值 |
| G2 | 分离 | Results 无机制解释，Discussion 有解读 | 移动句子到正确章节 |
| G3 | 强度 | 因果结论有实验支持，否则降级 | 改“相关/提示”并标 speculative |
| G4 | 对照 | 与文献的一致/冲突均有讨论 | 补对照或说明无文献 |
| G5 | 局限 | 具体到因素与影响 | 替换套话为具体限制 |
| G6 | 一致 | 正文数值与图表一致 | 逐处核对并统一精度 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| Results 里讲机制 | 出现“因为/说明” | 移入 Discussion |
| 数值缺 n/误差 | 只有均值 | 补 n、SD/SEM、检验方法 |
| 因果越界 | 无干预却写“导致” | 降级为相关并限定 |
| 只报显著结果 | 阴性/异常结果缺失 | 补报或说明排除理由 |
| 局限写成套话 | “样本有限”无影响分析 | 写清影响与边界 |
| 与图表不一致 | 正文 92.1% 图注 92% | 统一精度并逐处核对 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`sections`、`results[]`、
      `interpretations[]`、`limitations[]`、`artifacts`、`support_files_used`
- [ ] `results-discussion.md` 与 `claim-table.json` 真实存在
- [ ] 每个数值含单位、n、误差类型
- [ ] Results 与 Discussion 职责分离
- [ ] 因果结论均有实验支持或已降级
- [ ] 与文献的一致/冲突均有讨论
- [ ] `check_results_discussion.py` 为可选审计；失败时已记录 `audit_unavailable`
- [ ] 局限具体且说明影响
- [ ] 官方 writing 别名 `deliverable/preserved_facts/evidence_map/change_log` 存在

## 最小示例（示例）

输入：`results.csv`（3 催化剂 × 3 温度 × n=3）+ `Fig.2`。
输出：Results 3 段（每段引用 Fig.2 的一个 panel），Discussion 5 段；
`results` 9 条、`interpretations` 4 条（2 supported、1 suggested、1 speculative）；
`limitations` 3 条。

## 版本与限制

- 版本：0.5.0（Results 正文优先；数据表与 checker 改为可选/不阻塞）。
- 没有统计输出时，不写“显著”；误差类型未说明时写 `not_stated`。
- 文献引用必须真实存在；无法核实时标 `source_needed`，不编 DOI。
- 平台可能把技能装到题目专属目录；必须先解析 `$SKILL_DIR`。
