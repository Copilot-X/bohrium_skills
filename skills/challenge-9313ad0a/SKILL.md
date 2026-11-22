---
name: mindmap-builder
description: 思维导图构建：把论文、提案或笔记转成层级清晰的思维导图，节点带类型与来源定位，支持交叉链接，输出可编辑 SVG 与 JSON 大纲。Convert source material into a hierarchical mind map with typed nodes, cross-links and per-node sources, producing editable SVG and JSON outline. Use for 思维导图、概念图、知识结构图、大纲可视化。
---

# 思维导图（Mind Map Builder）

## 用途与触发

task.md 要求"画思维导图""知识结构图""概念图""梳理逻辑框架"时使用。

关键点：思维导图的价值在**结构与来源**，不在好看。每个节点都要能指回原文，
否则就是"看起来很有条理的自由发挥"。

## 输入

- `/input/task.md`：中心主题、层级深度、节点数量、导出格式、语言
- `/input/case.json`：`case_id`、`source_documents[]`、`output_contract`
- 材料：论文/笔记/大纲/术语表
- 可选：既有导图（要求在其基础上改）

## 第 0 步：生成结构初稿（推荐）

```bash
python3 "$SKILL_DIR/scripts/build_mindmap.py"   --input /input --work /app/work --output /app/output
```

脚本从材料标题和证据句生成 root/branch/leaf 三层节点、边和可编辑 SVG；
节点都带来源标题或逐字句。模型必须复核层级、合并/拆分节点并补全讲稿式
标签；预览 PNG 仅在 cairosvg 可用时生成，否则如实写 preview 不可用。

## 执行步骤

1. **定中心与主干**：从 task.md 与材料标题确定 1 个根节点 + 3–7 个一级分支。
   一级分支应是**并列的逻辑维度**（如 背景/问题/方法/结果/局限），不是章节标题照搬。
2. **建节点**：每个节点
   ```json
   {"node_id": "n12", "parent": "n3", "label": "表面氧空位提高选择性",
    "type": "claim|concept|method|result|evidence|question",
    "detail": "…", "source": {"path": "materials/paper.pdf", "locator": "p.4 §3.2"},
    "kind": "reported|inferred", "importance": "core|supporting|detail"}
   ```
   规则：
   - 一个节点一个意思，标签 ≤ 20 字；
   - `result` 节点必须带数值或方向；
   - `inferred` 节点用虚线样式并在标签中体现推测语气；
   - 没有来源的节点只能来自 task.md 的组织性要求，标 `source: "task"`。
3. **交叉链接**：记 `cross_links[]`（`from`、`to`、`relation`、`source`），
   用于表达跨分支的因果/对比关系。
4. **控制规模**：一级 3–7，总量按 task.md；默认 ≤ 40 节点。
   超限时合并细节节点，不删核心 claim。
5. **生成 SVG**：径向或左右树状布局，要求
   - 文本用 `<text>`，不转曲；
   - 每个节点一个 `<g id="nXX">`，带 `data-type`；
   - 层级用颜色/线宽区分，`inferred` 用虚线；
   - 连线用 `<path>`，不穿过节点；
   - 画布 `viewBox` 明确，字号 ≥ 10。
6. **校验**：`python3 /skills/weekly-skill/scripts/svg_lint.py /app/output/mindmap.svg --json`
   必须通过；节点数与 `mindmap.json` 一致。
7. **落盘**：`mindmap.json`、`mindmap.svg`、`preview/mindmap.png`（若可渲染）、`result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "mindmap",
  "status": "completed|partial",
  "mindmap": {
    "root": {"node_id": "n0", "label": "…"},
    "nodes": 0,
    "depth": 3,
    "branches": ["背景", "问题", "方法", "结果", "局限"]
  },
  "artifacts": [
    {"role": "editable", "path": "mindmap.svg", "format": "svg"},
    {"role": "structure", "path": "mindmap.json", "format": "json"},
    {"role": "preview", "path": "preview/mindmap.png", "format": "png"}
  ],
  "nodes": [
    {"node_id": "n12", "parent": "n3", "label": "…", "type": "claim",
     "detail": "…", "source": {"path": "materials/paper.pdf", "locator": "p.4"},
     "kind": "reported", "importance": "core"}
  ],
  "cross_links": [
    {"from": "n12", "to": "n21", "relation": "explains", "source": "p.5"}
  ],
  "coverage": {"sections_covered": ["Intro", "Results"], "not_covered": ["Methods details"]},
  "uncertainty": [{"item": "…", "reason": "…", "impact": "…"}],
  "change_log": []
}
```

## 布局与可读性规范

- 径向布局：根在中心，一级分支均匀分布，避免交叉；
- 左右树状：一级分支左右分列，深度方向留白；
- 同级节点垂直间距 ≥ 1.6 倍字号，兄弟节点不重叠；
- 颜色按分支区分，不超过 6 种；保证灰度可辨；
- 核心节点加粗/加边框，细节节点弱化；
- 长标签换行，不压线、不出画布。

## 规则

1. 导图是**结构的压缩**，不是原文摘抄；每个节点是提炼后的短语。
2. 不制造原文没有的分支；组织性节点（如"研究问题"）可来自 task.md，标 `source: "task"`。
3. 同一层级的节点粒度要一致（不要一个节点是一句话、另一个是整章）。
4. 跨分支关系必须用 cross-link 表达，不靠空间位置暗示。
5. `inferred` 节点不超过总量的 20%，且必须带依据。
6. 导出 SVG 必须可编辑、文本可选。

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料是扫描件 | OCR 后建图，标注识别风险 |
| 节点数超限 | 合并 detail 级节点，保留 core |
| 无法渲染 PNG | 交付 SVG + 说明，`status=partial` |
| 要求沿用旧导图 | 以旧图为骨架，标注新增/删除节点 |
| 材料观点冲突 | 建"争议"分支，双方并列并给来源 |

## 自检

- [ ] 每个节点有来源或 `source: "task"`
- [ ] 一级分支 3–7 个，粒度一致
- [ ] `inferred` 节点 ≤ 20% 且带依据
- [ ] SVG 通过 `svg_lint.py`
- [ ] 节点数与 JSON 一致
- [ ] 预览真实渲染

## 快速路径（预算内）

1. **0–2 分钟**：读材料，确定中心主题与 3–7 个一级分支。
2. **2–7 分钟**：先出 JSON 大纲（节点+层级），落盘骨架。
3. **7–12 分钟**：生成 SVG，补交叉链接与来源映射。
4. **最后 2 分钟**：跑 SVG 结构检查（XML/文本溢出/节点重叠）。

## 方法增强（融合参考实践）

- **层级规则**：中心节点唯一；每个节点只表达一个观点；建议深度 ≤ 4 层；叶子节点必须可回溯来源。
- **节点类型**：`topic`（主题）/ `claim`（论断）/ `evidence`（证据）/ `method`（方法）/ `question`（待解问题），类型决定配色与图标。
- **横向优先**：一级分支用名词短语并列，避免把时间顺序混进结构层级。
- **交叉链接**：只连真实存在的跨分支关系，用虚线并标注关系词（支持/对比/依赖）；禁止为好看滥连。
- **节点来源映射**：每个 `claim/evidence` 节点记 `source{path, locator}`；无来源节点标 `author_note`。
- **布局**：放射或左右分布，同级节点等距；文字宽度按字符数估算，超宽换行而非缩字号。
- **SVG 结构检查**：XML 可解析、每个 `<text>` 有坐标、节点框包含文字、无明显重叠、无悬空连线。

### 定向方法增强

- **来自 ProcessOn 思维导图**：先按 MECE 拆解（相互独立、完全穷尽）；原文有目录/标题时优先忠实保留骨架，只对最末级正文做“一句话观点”压缩，不把标题层改写成自己的话。
- **来自 ProcessOn 思维导图**：先选结构再画图——中心放射 `mind_free`、向右逻辑 `mind_right`、组织架构 `mind_org`、鱼骨归因 `mind_ishikawa`、时间轴 `mind_timeline`、树形 WBS `mind_tree`、表格式对比 `mind_tree_table`；结构不确定时默认中心放射。
- **来自 ProcessOn 思维导图**：标题最多到六级，超过六级改用无序列表；禁止 HTML；一个节点只放一个信息点；节点用短句/短语/名词，不粘贴整段。
- **来自 Mindmap Generator Plus**：布局按“中心 → 终端/分支 → 模块 → 功能项”四层数据组织；连接线用 L 形折线，节点用圆角框并留等距，避免连线穿过文字。
- **两篇合取后的改造**：`outline.json` 增加 `structure_type` 与 `leaf_compression` 字段；渲染前先检查层级跳级、节点超长、孤立节点与连线穿字。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 结构 | 单中心、层级 ≤ 4、每节点一观点 | 拆节点或提升层级 |
| G2 | 覆盖 | 材料核心论点均出现在图中 | 补节点或标 `omitted` |
| G3 | 来源 | claim/evidence 节点有来源定位 | 补定位或改标 author_note |
| G4 | 交叉链接 | 每条都有关系词且两端存在 | 删除或补关系说明 |
| G5 | 渲染 | SVG 可解析、文字不溢出、无重叠 | 调整布局/换行 |
| G6 | 一致 | JSON 大纲与 SVG 节点一一对应 | 以 JSON 为准重新渲染 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 节点写成整句 | 单节点超过 20 字 | 提炼关键词，细节下沉子节点 |
| 层级过深 | 出现 5 层以上 | 合并同级或拆成两张图 |
| 孤立节点 | 无父无链接 | 挂接正确父节点或删除 |
| 交叉链接泛滥 | 虚线多于实线 | 只保留真实跨分支关系 |
| 文字溢出节点框 | SVG 中文字超出矩形 | 换行或加宽节点 |
| 图与大纲不一致 | SVG 多/少节点 | 以 JSON 重新生成 SVG |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`nodes[]`、`edges[]`、`artifacts`
- [ ] `mindmap.svg` 可解析且能编辑
- [ ] `outline.json` 与 SVG 节点一致
- [ ] 中心唯一、层级 ≤ 4、每节点一观点
- [ ] claim/evidence 节点有来源定位
- [ ] 交叉链接均带关系词
- [ ] 无文字溢出与明显重叠

## 最小示例（示例）

输入：1 篇论文（IMRaD）+ 要求"5 个一级分支、≤30 节点"。
输出：`mindmap.json` 28 节点、5 分支、4 条 cross-link；
`mindmap.svg` 通过 lint（`text_elements: 28`）；`preview/mindmap.png` 由 SVG 渲染。
## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/inspect_input.py` | 盘点材料、节点与关系 |
| `scripts/validate_result.py` | 校验思维导图结构、证据与 result.json |
| `references/01-evidence.md` | 证据定位与逐字引用规则 |
| `references/08-quality-gates.md` | 交付质量闸门 |
| `templates/result_envelope.json` | result.json 信封示例 |

## 本题交付对照

- 目标产物：可编辑的稿件、改稿、引用/格式结果或结构化写作产物；完成标准：满足题目指定体裁、受众和格式。
- 事实与来源映射：数字、术语、引用、图表和关键论断的来源/修改记录；完成标准：可逐条回溯，不新增材料外事实。
- 版本与边界：修改差异、人工确认项、材料缺口和异常；完成标准：结果可继续编辑，未确认部分明确标注。
