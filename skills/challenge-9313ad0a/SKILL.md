---
name: mindmap-builder
description: 思维导图构建：从论文、提案、实验记录或笔记中抽取概念、证据与关系，生成层级清晰、节点带类型与来源、关系有依据的可编辑 SVG/JSON 思维导图，并输出官方 deliverable/preserved_facts/evidence_map/change_log 四字段。Convert research material into an evidence-traced, editable mind map with typed nodes, sourced cross-links, SVG + JSON outline and a contract-compliant result.json. Use for 思维导图、概念图、知识结构图、大纲可视化、论文梳理、研究规划。
---

# 思维导图（Mind Map Builder）

## 1. 用途与触发

当任务要求“画思维导图 / 知识结构图 / 概念图 / 梳理逻辑框架 / 把论文或笔记整理成图”时使用。
核心是**结构 + 来源 + 可编辑**：每个节点能回到原文，每条关系有依据，交付物能继续改。

适用：论文梳理、综述结构、实验方案、项目路线、会议决策、概念体系。
不适用：无材料的自由联想、只需要不可编辑位图的场景。

## 2. 输入与输出

输入（评测时）：

- `/input/task.md`：中心主题、分支数、节点上限、层级、语言、导出格式；
- `/input/case.json`：`case_id`、`title`、`source_documents[]`、`output_contract`；
- `/input/materials/` 或 case.json 声明的路径：`.md/.txt/.tex/.json/.csv/.tsv/.yaml/.html/.xml`；
  `.pdf` 用标准库做尽力文本抽取，失败标 `ocr_needed`，不伪造内容。

输出（写到 `/app/output`）：

| 文件 | 作用 |
|---|---|
| `result.json` | 官方结果：`deliverable/preserved_facts/evidence_map/change_log` + 结构视图 |
| `mindmap.svg` | 首选可编辑交付物：真实 `<text>`，节点/边带来源，无脚本与外链 |
| `mindmap.json` | 结构源文件：单根、parent 链、cross_links、coverage |
| `mindmap-spec.json` | 渲染规格：坐标/尺寸/样式/边路径，便于二次编辑 |

工作文件写 `/app/work`：`material.json`、`mindmap.json`、`mindmap-spec.json`、
`check-report.json`、`check-report-final.json`。

## 3. 单入口调用

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output

python3 "$SKILL_DIR/scripts/main.py" run \
  --input /input --work /app/work --output /app/output \
  --layout tree --max-nodes 40 --json
```

`scripts/main.py` 是唯一入口；其余脚本由它调用，也可在调试时单独运行：

```bash
python3 "$SKILL_DIR/scripts/main.py" extract --input /input --work /app/work --json
python3 "$SKILL_DIR/scripts/main.py" build --input /app/work/material.json \
  --out-outline /app/work/mindmap.json --out-spec /app/work/mindmap-spec.json \
  --out-svg /app/output/mindmap.svg --json
python3 "$SKILL_DIR/scripts/main.py" check --material /app/work/material.json \
  --outline /app/work/mindmap.json --spec /app/work/mindmap-spec.json \
  --svg /app/output/mindmap.svg --result /app/output/result.json \
  --root /app/output --json
python3 "$SKILL_DIR/scripts/main.py" result --input /input --work /app/work \
  --output /app/output --json
```

退出码：`0` 已写出 `result.json`（`completed` 或 `partial`，输入错误也算）；
`2` 仅当 work/output 目录都无法创建、进程完全无法启动。中途任何一步失败都会
降级继续，并把问题写进 `uncertainty` 与 `change_log`，不静默丢结果。

## 4. 支持文件（相对 `$SKILL_DIR`）

| 路径 | 作用 |
|---|---|
| `scripts/main.py` | 唯一入口：extract → build → check → result → final check |
| `scripts/mindmap_kit.py` | 任务专用标准库：文本切分、来源归一、标签压缩、约束解析、路径发现 |
| `scripts/extract_material.py` | 读 task/case/材料，抽节点、关系、数字事实、证据映射 |
| `scripts/build_mindmap.py` | 校验单根/父链/无环，布局并渲染 SVG + JSON + spec |
| `scripts/check_mindmap.py` | 结构、节点、关系、SVG、官方字段与真实路径检查 |
| `scripts/make_result.py` | 投影官方四字段，磁盘校验 artifact，失败降级 partial |
| `resources/mindmap.schema.json` | 大纲/节点/关系字段约束；产出前对照 |
| `resources/evidence_map.schema.json` | `claim_id/claim/source{path,locator,quote}` 约束 |
| `resources/node_types.md` | 节点类型、kind 语义、关系词表 |
| `resources/checklist.md` | 交付检查单，与 checker 规则一致 |

所有脚本只用 Python 3.9+ 标准库，不联网、不读作者个人路径。

## 5. 流水线与职责

1. **extract**：解析任务约束（分支 3–7、节点上限、深度、语言）；按标题与语义线索
   归类句子；标签取原句短子串（不改写）；数字句进 `preserved_facts`，claim/evidence
   进 `evidence_map`；来源写相对 `/input` 的真实路径与 `§章节 L行号`。
2. **build**：保证单根、父链存在、无环；无来源的节点标 `inferred`；生成
   `mindmap.json`、`mindmap-spec.json` 与 `mindmap.svg`；默认左右树，`--layout radial` 可切径向。
3. **check**：检查结构（单根/环/深度/分支/同级重复）、节点（类型/kind/来源/推断比例）、
   关系（端点/关系词/来源）、SVG（viewBox/`<text>`/覆盖/外链）与官方字段、真实路径。
4. **result**：投影四字段并保留结构视图；SVG 缺失时降级交付 `mindmap.json` 并标 `partial`；
   `change_log` 记录读入 → 抽取 → 渲染 → 校验 → 落盘 → 人工确认项。

需要模型润色标签时，可在 `extract` 之后直接编辑 `/app/work/mindmap.json` 的
`label/detail/type/cross_links`，再用 `main.py build --outline ...` 重渲染；不得添加
材料中不存在的概念或关系。

## 6. 节点、关系与来源模型

节点最小字段（完整约束见 `resources/mindmap.schema.json`）：

```json
{
  "node_id": "n8",
  "parent": "b4",
  "label": "表面氧空位浓度增加提升选择性",
  "type": "result",
  "detail": "对原句的压缩说明；必要时保留原句",
  "source": {"path": "materials/results.md",
             "locator": "§结果 L3", "quote": "逐字原句"},
  "kind": "reported",
  "importance": "core"
}
```

- `type`：`topic/concept/question/method/evidence/result/claim/risk/gap/task`，
  语义见 `resources/node_types.md`。
- `kind`：`reported`（材料原文，必须有 path+locator）、`task`（task.md 的组织性要求）、
  `inferred`（推断，必须在 detail 写依据，总量 ≤20%）。
- 概念与关系只来自材料；证据不足时删除节点或标 `inferred`，不得编造。
- 跨分支关系写 `cross_links[]`：`from/to/relation/source`；关系词限定为
  `supports/contrasts/depends/explains/causes/precedes/part_of/conflicts/cites/link`。
- 关系只在“证据句可归属某节点 + 与另一节点共享 ≥4 字术语 + 有明确关系词”时生成；
  标签未逐字出现在证据句时标 `kind=inferred`，宁缺毋滥。

## 7. 官方四字段（result.json）

```json
{
  "case_id": "原样保留",
  "deliverable": {"path": "mindmap.svg", "format": "svg", "role": "editable",
                  "nodes": 16, "edges": 17, "editable": true},
  "preserved_facts": [{"fact_id": "f-num-1", "text": "在 350 °C 下选择性达到 91%…",
    "value": "91%", "source": {"path": "materials/results.md",
    "locator": "§结果 L3", "quote": "…逐字原句…"}}],
  "evidence_map": [{"claim_id": "n8", "claim": "表面氧空位浓度增加提升选择性",
    "node_type": "result", "kind": "reported",
    "source": {"path": "materials/results.md", "locator": "§结果 L3", "quote": "…"}}],
  "change_log": [
    {"step": 1, "action": "读取输入与材料", "evidence": "input/case.json, input/task.md"},
    {"step": 2, "action": "抽取概念与关系", "evidence": "work/material.json"},
    {"step": 3, "action": "生成可编辑导图", "evidence": "mindmap.svg"},
    {"step": 4, "action": "结构、来源与官方字段校验", "evidence": "work/check-report.json"},
    {"step": 5, "action": "写入 result.json 并保留 case_id", "evidence": "output/result.json"}
  ]
}
```

要求：`case_id` 与输入完全一致；`deliverable.path` 必须是磁盘上真实存在、优先 SVG 的
可编辑文件；`preserved_facts` 覆盖数字、条件、术语与核心节点；`evidence_map` 覆盖
claim/evidence/result 等关键节点；`change_log` 不得为空且步骤真实。
可选字段：`status`、`mindmap`、`nodes`、`edges`、`cross_links`、`coverage`、
`artifacts`、`uncertainty`、`warnings`、`output_contract`（从 case.json 原样保留）。

## 8. 可编辑 SVG 规范

- 文本必须是 `<text>/<tspan>`，不转曲；每个节点一个 `<g data-node-id data-type
  data-kind data-source>`，每条边一个 `<path data-edge-id data-kind data-source>`。
- 实线=层级，虚线=交叉关系/推断；颜色按分支区分且灰度可辨；核心节点加粗。
- 无 `<script>`、`<image>`、外部 `href`、`url(http…)`、`@import`；不嵌入外部字体。
- 画布有数值 `viewBox`；字号 ≥10；长标签换行，不压线、不出画布。
- `mindmap.json` 与 SVG 节点一一对应，可用 `build_mindmap.py --outline` 重新渲染。
- 不伪造 PNG/PDF 预览；SVG 可直接在浏览器或矢量编辑器中打开。

## 9. 异常与降级

| 情况 | 处理 |
|---|---|
| 材料缺失/为空 | 写 root-only 可编辑 SVG/JSON，`status=partial`，`uncertainty` 说明 |
| 输入目录不存在 | 仍写 root-only 交付物与 `result.json`，退出 0，标 `input` 不确定项 |
| 扫描 PDF/无文本层 | 标 `ocr_needed`，不猜正文；提示先 OCR |
| 不支持的后缀 | 在 `sources[].status=unsupported` 与 `uncertainty` 中列出 |
| 数值冲突 | 保留双方数值，写入 `uncertainty`，不擅自选一个 |
| 节点超预算 | 按分数与分支配额截断，优先核心/数字节点，不删证据行 |
| 分支少于 3 | 保留真实结构并警告，不编造分支凑数 |
| SVG 生成失败 | 降级为 `mindmap.json` 交付，`status=partial`，不假装 SVG 存在 |
| 检查失败 | 仍写 `result.json`，`status=partial`，problems 写入不确定性与 change_log |

## 10. 最小接口示例（synthetic，非正式案例）

输入 `/input/case.json` 只需保留原标识与契约，材料放 `/input/materials/`：

```json
{
  "case_id": "EXAMPLE-NOT-A-TEST-CASE",
  "title": "合成接口示例",
  "source_documents": ["materials/notes.md"],
  "output_contract": {
    "case_id": "原样保留",
    "deliverable": "可编辑的思维导图",
    "preserved_facts": "数字、条件、术语和来源不可凭空改变",
    "evidence_map": "关键论断到来源定位的映射",
    "change_log": "修改动作、原因、人工确认项和未解决问题"
  }
}
```

调用 `main.py run --input /input --work /app/work --output /app/output` 后，
`/app/output` 得到 `mindmap.svg`、`mindmap.json`、`mindmap-spec.json`、`result.json`。
示例输出仅示意接口，不是任何正式案例的答案。

## 11. 依赖、版本与限制

- 版本 1.1.0；Python 3.9+ 标准库；无网络、无第三方包、无字体嵌入。
- PDF 仅做尽力文本抽取，不做 OCR；语义抽取是规则启发式，长文/多文档需人工复核
  `coverage.not_covered` 与 `uncertainty`。
- 默认 ≤40 节点、深度 ≤4；task.md 有明确数量/分支/深度时以 task.md 为准。
- 交付前逐项核对 `resources/checklist.md`。
