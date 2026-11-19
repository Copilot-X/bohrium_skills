---
name: biological-pathway-illustrator
description: 生物机制图与通路图：从真实材料建立区室/分子/交互台账，区分激活、抑制、结合、转位、修饰与推断，用单入口生成可编辑 SVG、真实 PDF/PNG，并逐边审计与像素复核。Draw a source-bound mechanism or pathway figure with compartments, typed edges, real exports and a single-entry audit. Use for 机制图、信号通路、调控网络、生物示意图、pathway figure。
---

# 生物机制图与通路图（Mechanism & Pathway）

## 用途与触发

当 task.md 要求“画机制图 / 信号通路图 / 调控网络 / 生物示意图”，或 workorder 为
`biological_mechanism_pathway` 时使用。交付物是：

**一张真实可编辑 SVG + 真实 PDF/PNG + 逐元素来源 + 逐边语义审计。**

评分重点是：区室/分子/基因不混用；激活、抑制、结合、转位、修饰、推断可区分；
方向与材料一致；图例自洽；源文件可复核；结果字段由脚本真实生成。

## 交付契约

`/app/output/result.json` 必须包含官方 media 字段：

- `status`：`completed` / `partial` / `insufficient_evidence` / `input_error`；
- `editable_source`：真实 SVG 的路径与格式；
- `export_artifact`：真实 PDF 或 PNG；没有时写 `null`；
- `content_map`：每个区室、分子、边的 `maps_to` 来源；
- `compliance_checks`：`scripts/audit.py` 的检查结果；
- `exceptions`：真实异常列表。

这些字段由 `scripts/result_graphics.py` 生成，不要手写。

## 支持文件（按需）

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output/figures /app/output/preview
```

| 文件 | 用途 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：盘点→台账→spec→SVG→PDF/PNG→audit→result |
| `scripts/pathway_svg.py` | 台账校验、rank 布局、可编辑 SVG 渲染（语义线型/端点） |
| `scripts/export.py` | 真实 PDF/PNG 导出；cairosvg/rsvg/inkscape 优先，matplotlib 兜底 |
| `scripts/audit.py` | SVG XML/文字/外链/区室/分子/边覆盖/导出文件头/像素复核 |
| `scripts/result_graphics.py` | 官方 media 字段与 `result.json` 构建 |
| `templates/pathway-spec.schema.json` | 台账 JSON Schema（模型写台账时对照） |
| `references/output-contract.md` | 输入发现、字段、状态与退出码 |
| `references/pathway-rules.md` | 实体/区室/边语义/布局/错误码 |
| `examples/case.json` | 合成接口示例（synthetic，非正式答案） |
| `examples/paper.txt` | 与示例搭配的合成材料（synthetic） |

## 执行步骤

### 第 1 步：读材料，写台账（模型完成）

先读 `/input` 中的 task、case、figure-workorder 与材料。用真实文件路径和
locator 建 `/app/work/pathway.json`，结构见
`templates/pathway-spec.schema.json`，最小示例见 `references/pathway-rules.md`。

硬规则：

- 一个实体一个节点；同义名放 `aliases[]`；修饰状态放 `sublabel`；
- 每条 `reported` 边有 `source{path,locator,quote?}`；
- 材料只说“相关/伴随”时用 `association`（无箭头）或 `inferred`（需极性）；
- 不凭领域常识补“经典通路”中间节点；
- 基因斜体、蛋白正体；区室按 Extracellular → Membrane → Cytoplasm → Nucleus
  排 `order`。

输入没有台账时，`run_case.py` 会从 `paper.txt`/`task.md` 生成临时草稿
`/app/work/pathway-draft.json` 并强制 `status=partial`。草稿只能作为起点：
复核/修正后复制为 `/app/work/pathway.json`，再重跑入口；不要直接把草稿当最终答案。

### 第 2 步：运行唯一入口

```bash
python3 "$SKILL_DIR/scripts/run_case.py" \
  --input /input --out /app/output --work /app/work --json
```

常用可选参数：

```bash
--case /input/case.json          # 显式 case/台账
--ledger /app/work/pathway.json  # 显式台账
--formats pdf,png --dpi 300      # 导出格式与分辨率
--basename fig2                  # 输出文件基名
```

入口内部调用 `scripts/pathway_svg.py`、`scripts/export.py`、`scripts/audit.py`、
`scripts/result_graphics.py`；不要绕过入口另拼脚本顺序。

### 第 3 步：看图并复核

打开 `/app/output/preview/<base>.png` 与 `result.json`，逐项检查：

1. 每个区室/分子/边都在图中，方向与材料一致；
2. 激活=实线箭头，抑制=实线 T 形，结合=细线无箭头，转位=虚线箭头，
   修饰=箭头 + `P`，推断=灰色虚线且保留极性；
3. 推断边有 `schematic_disclaimers`；图例覆盖实际线型；
4. 文字不溢出、不压线；基因斜体；区室标签不重叠；
5. `compliance_checks` 全为真、`coverage.element_coverage=1.0`。

发现问题是改台账后重跑入口，不要手改 `result.json` 或伪造检查结果。

### 第 4 步：异常处理

| 情况 | 处理 |
|---|---|
| 缺材料/读不了 | 入口写 `status=input_error`；补材料后重跑 |
| 台账为空/畸形 | 入口写 `input_error` 并列出校验错误 |
| 缺来源 | 补 `source`，或改 `inferred` + 虚线 + 免责；状态为 `partial` |
| 原文只说“相关” | 用 `association` 或 inferred，不画因果 |
| 导出失败 | 保留 SVG，`export_artifact=null`、`partial`、`export_unavailable` |
| 文字溢出/压线 | 缩短 label、增大画布或改 `route` |
| 自动草稿 | 必须人工/模型复核；结果保持 `partial` |

## 边语义（强约束）

| effect | 线型 | 端点 | 原文触发词 |
|---|---|---|---|
| `activate` | 实线（蓝） | 箭头 | activate/promote/induce/upregulate/recruit |
| `inhibit` | 实线（橙） | T 形横杠 | inhibit/suppress/block/reduce/repress |
| `bind` | 细实线（灰） | 无箭头 | bind/associate/form complex/co-IP |
| `translocate` | 虚线（深灰） | 箭头 | translocate/import/export/shuttle |
| `modify` | 实线（紫） | 箭头 + `P` | phosphorylate/ubiquitinate/cleave/acetylate |
| `association` | 细点线（灰） | 无箭头 | associated with/correlate/co-vary |
| `unknown_link` | 虚线（灰） | 箭头 + `?` | 上下游出现但机制缺失 |
| `inferred` | 灰色虚线 | 保留 `base_effect` 端点 | 间接/关联证据；必须写免责 |

跨区室判定看“分子本身是否移动”：移动用 `translocate`；只是信号传递则保留
`activate`/`inhibit` 并在 `mechanism` 说明传递方式。

## 输出文件

```
/app/output/figures/<base>.svg
/app/output/figures/<base>.pdf
/app/output/preview/<base>.png
/app/output/result.json
/app/work/inventory.json
/app/work/figure-spec.json
/app/work/coverage.json
/app/work/run-report.json
```

`result.json` 的完整字段、状态判定和退出码见
`references/output-contract.md`；审计错误码与修复见
`references/pathway-rules.md`。

## 自检清单

- [ ] 台账中每个区室/分子/边都有 id、label 和 source（或 inferred 标记）
- [ ] 所有 `source.path` 来自真实输入；`content_map` 覆盖全部元素
- [ ] 激活/抑制/结合/转位/修饰/推断的线型与端点正确
- [ ] 推断边灰色虚线 + `schematic_disclaimers`
- [ ] 图例覆盖实际边类型；基因斜体；文字不溢出/压线
- [ ] `editable_source` 指向真实 SVG；`export_artifact` 指向真实 PDF/PNG
- [ ] `compliance_checks` 来自 `audit.py`，不是手写
- [ ] 真的打开 PNG 做过视觉复核
- [ ] `run_case.py` 退出码为 0 且 `result.json` 状态诚实

## 版本与限制

- 版本：0.14.0（单入口 + 台账渲染 + 真实导出 + 逐边/像素审计）。
- 仅用标准库渲染；导出优先外部渲染器，matplotlib 只作可选兜底。
- 不调用网络；不依赖 python-pptx；复杂反馈环可手工指定 `rank/order/route`。
- 平台可能把技能装到题目专属目录，必须先解析 `$SKILL_DIR`。
- 示例 `examples/case.json` + `examples/paper.txt` 是 synthetic 接口样例，
  不是本题答案；不要复制其中的实体或来源。
