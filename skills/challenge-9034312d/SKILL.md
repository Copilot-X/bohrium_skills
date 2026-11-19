---
name: apparatus-and-route-drawer
description: 实验装置图与技术路线图：装置模式画组件、连接、介质流向、端口与工况；路线模式画阶段、输入输出与判定分支；单入口生成可编辑 SVG 与真实 PDF/PNG，并输出逐元素来源映射和官方 media 字段。Draw a source-bound experimental apparatus schematic or technical-route flowchart as editable SVG with real PDF/PNG exports. Use for 实验装置、技术路线、方法路线图、工艺流程图。
---

# 实验装置与技术路线图

## 用途与触发

task.md 要求“实验装置图 / 技术路线图 / 工艺流程 / 研究方法框架图”时使用。
组织方 workorder 为 `apparatus_and_technical_route`。硬约束：

- 交付**真实可编辑 SVG + 真实 PDF/PNG**，不是截图或文字描述；
- 图上每个部件/阶段/连接/工况/判据都能追溯到输入材料；
- 材料没给的部件、参数、阈值不补；
- 装置与路线都要求时输出 `(a)` / `(b)` 双 panel。

## 输入

- `/input/task.md`：图型、必须出现的部件/阶段、条件、图幅、语言。
- `/input/case.json`：`case_id`、`workorder`、`source_materials[]`；
  若自带 `figure_spec`（或 `/app/work/spec.json`）则直接使用。
- `/input` 下的真实材料：方法段、装置清单、工艺参数、路线描述、已有图。

## 支持文件

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
```

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：盘点 → 标准化 → 渲染 → 导出 → 审计 → result.json |
| `scripts/svg_gen.py` | 规格标准化与双 panel 合成；调用 `make_schematic.py` |
| `scripts/make_schematic.py` | 确定性 SVG 渲染器（命名组、`<text>`、工况引出线、端口小字） |
| `scripts/export.py` | 真实 PDF/PNG：cairosvg/rsvg/inkscape，缺失时 matplotlib 重绘 |
| `scripts/audit.py` | SVG/文字/外链/组件/阶段/连接/工况/判据/导出文件头审计 |
| `scripts/result_graphics.py` | 唯一写 result.json 与六个官方 media 字段的模块 |
| `templates/spec.schema.json` | 规格 schema 与字段约定 |
| `templates/result-graphics.template.json` | result.json 骨架（含 v0.3 字段与 media） |
| `references/output-contract.md` | 输出契约、状态与退出码 |
| `references/apparatus-route-rules.md` | 抽取/端口/流向/工况/判据/布局规则 |
| `examples/case.json` | 合成双模式示例输入（synthetic，非正式答案） |
| `examples/paper.txt` | 示例材料，所有示例来源都指向它 |

## 工作流（模型只写 spec）

### 第 1 步：读真实材料，判定模式

- 有部件/管路/介质/工况 → `apparatus`；
- 有阶段/输入输出/判据/交付物 → `route`；
- 两者都有 → `both`（两个 panel）。

先列出材料中的原文条目，再建 spec。**不要用领域常识补全清单。**

### 第 2 步：写 `/app/work/spec.json`

字段见 `templates/spec.schema.json`。最小骨架：

```json
{
  "figure_id": "Fig.4",
  "mode": "apparatus",
  "width_mm": 180,
  "height_mm": 110,
  "font_size_pt": 8,
  "nodes": [
    {"id": "A1", "label": "Gas manifold", "shape": "rounded",
     "source": {"path": "paper.txt", "locator": "§1.1"}},
    {"id": "A2", "label": "Mass flow controller", "sublabel": "50 mL/min",
     "shape": "rounded", "source": {"path": "paper.txt", "locator": "§1.2"}}
  ],
  "edges": [
    {"id": "K1", "from": "A1", "to": "A2", "kind": "flow",
     "medium": "CO2/H2", "label": "CO2/H2", "direction": "forward",
     "from_port": "out", "to_port": "in", "route": "orthogonal",
     "source": {"path": "paper.txt", "locator": "§4 K1"}}
  ],
  "conditions": [
    {"id": "P1", "location": "A2", "params": {"flow": "50 mL/min"},
     "source": {"path": "paper.txt", "locator": "§2.2"}}
  ],
  "legend": [{"kind": "flow", "label": "material flow"}],
  "footnote": "Synthetic example only."
}
```

路线模式用 `stages[]`（或 nodes）+ `decisions[]`：

```json
{
  "mode": "route", "direction": "TB",
  "stages": [
    {"id": "S1", "label": "S1 Prepare catalyst", "order": 0,
     "source": {"path": "paper.txt", "locator": "§3.1"}},
    {"id": "S2", "label": "S2 Activity test", "order": 1,
     "source": {"path": "paper.txt", "locator": "§3.1"}},
    {"id": "S3", "label": "S3 Stability test", "order": 2,
     "source": {"path": "paper.txt", "locator": "§3.2"}}
  ],
  "decisions": [
    {"id": "D1", "after": "S2", "question": "Conversion ≥ 80%?",
     "yes": "S3", "no": "S1", "criterion": "conversion ≥ 80% after 12 h",
     "criterion_source": {"path": "paper.txt", "locator": "§3.2"}}
  ]
}
```

`mode: "both"` 时给 `panels: [{...apparatus panel...}, {...route panel...}]`，
两个 panel 的 id 全局唯一；`svg_gen.py` 合成为一张 SVG。

### 第 3 步：运行唯一入口

```bash
python3 "$SKILL_DIR/scripts/run_case.py" \
  --input /input --output /app/output --work /app/work --dpi 300
```

入口自动完成：

1. 盘点真实输入并写 `work/inventory.json`；
2. 读取 `--spec` > `work/spec.json` > `case.json:figure_spec`；
3. 标准化 spec、补默认来源路径、写 `work/normalized-spec.json`；
4. 渲染 `output/figures/<figure>.svg`；
5. 导出 `figures/<figure>.pdf` 与 `preview/<figure>.png`；
6. 审计并写 `work/coverage.json`；
7. 由 `result_graphics.py` 写 `output/result.json`。

### 第 4 步：看 result 再决定是否补图

- `status=completed`：C1–C10 全 ok、coverage=1.0、PDF/PNG 有效。
- `status=partial`：看 `exceptions[]` 与 `checks`，补来源/补节点/重跑；
- `status=input_error`：没有可用 spec 或输入不可读，仍会写 result.json。

半成品先保留，逐步补全；不要删元素或删连接来抬高覆盖率。

## 规格要点

### 装置
- `nodes[]`：组件；`id` 唯一，`label` 与材料一致。
- `edges[]`：`from`/`to` 是介质实际流向；`medium` 必填；
  `direction ∈ {forward, reverse, undetermined}`；`from_port`/`to_port` 有依据才写。
- `conditions[]`：`location` 指向真实节点，`params` 保留原文与单位。
- 分流/废料必须有节点或明确出口；不允许断头箭头。

### 路线
- `stages[]`：阶段；`order` 决定自上而下顺序。
- `decisions[]`：`after`、`yes`、`no` 指向真实节点；菱形节点可显式给出，
  也可由脚本按声明生成；`criterion_source` 必须来自材料或写 `not_provided`。
- 回流/返工边加 `feedback: true`（自动虚线），不计入 rank。

### 来源
- 每个元素/连接/工况/决策都有 `source {path, locator}`；
- `path` 必须是 `/input` 下真实文件；`locator` 用章节/图号/端口表编号；
- 材料未给：不画、不猜，写入 `exceptions` / `uncertainty`；
- 明确推断：`kind: "inferred"` 或 `direction: "undetermined"` + 虚线 + 说明。

## 输出契约（摘要）

`result.json` 同时包含：

- 任务字段：`elements[]`、`connections[]`、`conditions[]`、`decisions[]`、
  `checks{}`、`sources[]`、`uncertainty[]`、`change_log[]`；
- 官方 media 字段：`status`、`editable_source`、`export_artifact`、
  `content_map`、`compliance_checks`、`exceptions`；
- `artifacts[]`：`figures/<figure>.svg`（editable）、`.pdf`（export）、
  `preview/<figure>.png`（preview, 300 dpi）。

完整字段、状态与退出码见 `references/output-contract.md`；绘图/抽取规则见
`references/apparatus-route-rules.md`。

## 退出码

| 退出码 | 含义 |
|---|---|
| 0 | completed / partial / input_error，`result.json` 已写 |
| 2 | 完全无法启动或 result.json 无法写入（唯一例外） |

## 质量闸门

- [ ] 模式与 task.md 一致；装置/路线不混型
- [ ] 每个组件/阶段/连接/工况/判据有来源且路径真实存在
- [ ] 介质流向、端口、工况单位正确；无断头箭头
- [ ] 判定分支有判据来源或明确 `not_provided`
- [ ] 无材料外部件、外部步骤、外部参数
- [ ] SVG 可解析、文字为 `<text>`、无外链/脚本/位图替代
- [ ] PDF/PNG 文件头有效；PNG 尺寸符合图幅与 dpi
- [ ] `status=completed` 只在 coverage=1.0 且全部检查通过时出现

## 异常处理

| 情况 | 处理 |
|---|---|
| 部件清单不全 | 只画确认部件，缺口写 `exceptions` |
| 连接关系模糊 | 虚线 + `direction: "undetermined"` + 说明 |
| 工况只给范围 | 原样写范围，不取中值 |
| 判定无判据 | `criterion_source: "not_provided"`，不编阈值 |
| 只有部分材料 | 先出已确认部分，`status=partial`，继续补 |
| 渲染器缺失 | `export.py` 用 matplotlib 重绘；仍失败则保留 SVG 并标 partial |

## 示例

`examples/case.json` + `examples/paper.txt` 是**合成接口示例**（synthetic，
不是正式答案），覆盖 `(a)` 装置 + `(b)` 路线、并行出口、工况、端口与判据。
可直接试跑：

```bash
mkdir -p /tmp/demo-in && cp "$SKILL_DIR/examples/case.json" \
  "$SKILL_DIR/examples/paper.txt" /tmp/demo-in/
python3 "$SKILL_DIR/scripts/run_case.py" \
  --input /tmp/demo-in --output /tmp/demo-out --work /tmp/demo-work
```

期望：`status=completed`、`coverage=1.0`、SVG 中存在 `A1…A8`、`K1…K7`、
`P1`、`P2`、`S1…S3`、`D1`、`T1` 命名组。

## 版本与限制

- 版本 0.14.0：单入口、五段流水线、官方 media 字段、双模式合成、真实导出。
- 仅使用标准库 + 可选 matplotlib（无网络、无 GUI）。
- 不支持位图重绘为矢量；已有位图只能作为材料参考，最终交付仍为新建 SVG。
