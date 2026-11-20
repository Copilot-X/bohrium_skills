---
name: apparatus-and-route-drawer
description: 实验装置图与技术路线图：装置模式画组件、连接、流向与工况；路线模式画阶段、输入输出与决策分支；均输出可编辑 SVG 与逐元素来源映射。Draw an experimental apparatus schematic or a technical-route flowchart as editable SVG with a per-element source map. Use for 实验装置、技术路线、流程图、方法路线图。
---

# 实验装置与技术路线图（Apparatus & Technical Route）

## 用途与触发

task.md 要求"画实验装置图""技术路线图""工艺流程""研究方法框架图"时使用。
组织方口径（硬约束）：workorder 为 `apparatus_and_technical_route`；
**必须交付真实可编辑的 SVG/PNG/PDF**。

同一题可能要求其中一种，也可能要求两张子图（`(a)` 装置 / `(b)` 路线）。
先判定类型，再选模板。

## 输入

- `/input/task.md`：子图类型、必须出现的部件/阶段、条件标注、图幅、语言
- `/input/case.json`：`case_id`、`workorder`、`source_materials[]`
- 材料：方法章节、装置清单、工艺参数、流程图文字、已有图（若要编辑）

## 执行步骤

1. **判定子类型**：
   - `apparatus`：有物理部件、管路、介质流向、工况参数；
   - `route`：有阶段、输入输出、判定分支、方法选择；
   - 两者都有 → 两个 panel，分别建 spec。
2. **建要素表** `/app/work/route.json`

   装置型：
   ```json
   {"mode": "apparatus",
    "components": [
      {"id": "A1", "label": "Mass flow controller", "role": "feed",
       "source": {"path": "materials/method.md", "locator": "§2.1"}}],
    "connections": [
      {"id": "K1", "from": "A1", "to": "A2", "medium": "N2", "direction": "forward",
       "source": "§2.1"}],
    "conditions": [
      {"id": "P1", "location": "A2", "params": {"T": "350 °C", "P": "1 bar",
       "flow": "50 mL/min"}, "source": "§2.2"}],
    "measurement_points": [{"id": "S1", "at": "A3", "measures": "conversion (GC)", "source": "§2.3"}]}
   ```

   路线型：
   ```json
   {"mode": "route",
    "stages": [
      {"id": "S1", "label": "原料预处理", "inputs": ["…"], "outputs": ["…"],
       "method": "…", "source": "§2.1"}],
    "decisions": [
      {"id": "D1", "after": "S2", "question": "转化率 ≥ 90%?",
       "yes": "S3", "no": "S2b", "criterion_source": "§2.4"}],
    "terminal": [{"id": "T1", "label": "性能评价", "deliverable": "…"}]}
   ```
3. **写规格** `/app/work/figure-spec.json`：
   - 装置：`layout: "free"`（mm 坐标）或 `layered` 按介质流向；
     管路用 `flow` 边（粗箭头），电气/信号用细虚线，标注介质名；
   - 路线：`direction: "TB"`，阶段用圆角矩形，判定用菱形，终点用椭圆；
     `yes/no` 分支在边上写标签。
4. **生成**：
   ```bash
   python3 /skills/weekly-skill/scripts/make_schematic.py \
     --spec /app/work/figure-spec.json --out /app/output/figures/fig4.svg --json
   ```
5. **装置图专项检查**：
   - 每个部件有名称，连接关系与材料一致（不得漏掉阀门/取样点）；
   - 介质流向箭头闭合、方向正确；工况标注挂在对应部件旁；
   - 未提及的部件不得出现（尤其"常见"的泵、换热器）。
6. **路线图专项检查**：
   - 阶段顺序与材料一致；判定分支有明确判据；
   - 每个阶段有输入输出；终止节点是真实交付物；
   - 无"凭空增加的方法步骤"。
7. **校验与导出**：`svg_lint.py` → PDF/PNG（≥300 dpi）→ 回读尺寸。
8. **落盘**：`figures/fig4.svg`、`figures/fig4.pdf`、`preview/fig4.png`、
   `route.json`、`result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "apparatus-route",
  "workorder": "apparatus_and_technical_route",
  "status": "completed|partial",
  "figure": {"figure_id": "Fig.4", "mode": "apparatus|route|both",
             "components": 0, "stages": 0, "canvas_mm": [180, 110]},
  "artifacts": [
    {"role": "editable", "path": "figures/fig4.svg", "format": "svg"},
    {"role": "export", "path": "figures/fig4.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/fig4.png", "format": "png",
     "width_px": 0, "height_px": 0, "dpi": 300}
  ],
  "elements": [
    {"element_id": "A1", "label": "Mass flow controller", "type": "component",
     "source": {"path": "materials/method.md", "locator": "§2.1"}},
    {"element_id": "S1", "label": "原料预处理", "type": "stage",
     "inputs": ["…"], "outputs": ["…"], "source": "§2.1"}
  ],
  "connections": [
    {"id": "K1", "from": "A1", "to": "A2", "medium": "N2", "direction": "forward",
     "source": "§2.1"}
  ],
  "conditions": [
    {"location": "A2", "params": {"T": "350 °C", "P": "1 bar"}, "source": "§2.2"}
  ],
  "decisions": [
    {"id": "D1", "after": "S2", "question": "转化率 ≥ 90%?", "yes": "S3",
     "no": "S2b", "criterion_source": "§2.4"}
  ],
  "checks": {"xml_valid": true, "text_as_text": true, "external_refs": 0,
             "all_components_named": true, "flow_direction_closed": true,
             "export_from_source": true},
  "schematic_disclaimers": [],
  "uncertainty": [],
  "change_log": []
}
```

## 绘制规范

- **装置图**：介质从左/上进入、右/下流出；管路用正交折线，转弯圆角；
  同类部件同形状，阀门用小三角对、泵用圆内三角、反应器用矩形；
  工况参数用引出线 + 小号字，不压在管路上。
- **路线图**：纵向自上而下；阶段宽度一致；判定菱形宽高比 ≈ 1.6:1；
  `yes` 走主线（向下/向右），`no` 走侧支（向左）；
  输入输出用短标签挂在阶段两侧，不另设大框。
- 字号 ≥ 7 pt；对比度足够；灰度打印后线型仍可区分。
- 不画阴影/立体/渐变；不用图标库位图。

## 异常处理

| 情况 | 处理 |
|---|---|
| 部件清单不全 | 只画确认的部件，缺口写 `missing_components` |
| 连接关系表述模糊 | 画虚线连接 + `direction: "undetermined"` + 说明 |
| 工况只给范围 | 原样写范围与"典型值"，不取中值冒充实测 |
| 判定分支无判据 | 标 `criterion_source: "not_provided"`，不编阈值 |
| 需与装置图并列 | 两 panel 共用图例与字号 |
| 导出失败 | 只交付 SVG，`status=partial` |

## 自检

- [ ] 每个部件/阶段有名称与来源
- [ ] 连接完整、介质名与流向正确
- [ ] 工况标注挂在正确位置
- [ ] 判定分支有判据来源或明确标注缺失
- [ ] 无材料外部件或步骤
- [ ] SVG 通过 lint，导出件真实渲染

## 快速路径（预算内）

1. **0–2 分钟**：判定模式（装置 / 路线），读 task.md 与材料，列组件或阶段清单。
2. **2–7 分钟**：先出主链骨架（装置：源→处理→检测；路线：阶段 1→2→3），落盘 SVG。
3. **7–12 分钟**：补连接/分支、工况或输入输出标注、来源映射。
4. **最后 2 分钟**：核对流向、编号与导出文件。

## 方法增强（融合参考实践）

- **模式判定**：有实物组件与管路选装置图；有阶段、判据与交付物选路线图；两者都有则分两栏。
- **组件表**：`id / name / type / connections[] / condition / source`；装置图标统一抽象符号，不追求写实。
- **流向明确**：管路/信号线带方向箭头，入口出口标注物料或信号名称；双向流用双箭头并说明。
- **工况标注**：温度、压力、流量、浓度标在对应组件或连线旁，单位齐全。
- **路线阶段**：每阶段记 `inputs / activities / outputs / decision`；决策分支写清判据（是/否走向）。
- **来源映射**：每个组件/阶段有 `source{path, locator}`；材料未给的参数标 `not_specified`，不猜。
- **导出校验**：SVG 可解析；PNG/PDF 文件头有效、体积 > 0。

### 定向方法增强

- **来自 社科机制图**：先选图型再画——分析框架（外层情境边界+内部多维并列+中心问题）、机制链（自变量→中介→因变量）、技术路线（方法→数据→分析→发现四阶卡片）、对比矩阵（维度×类型）、理论整合（多理论→整合框架）；本题装置模式对应流程链，路线模式对应四阶路线卡片，不混用。
- **来自 社科机制图**：布局是排版意图，不能默认——左右推进（线性流程）、上下递进（层级步骤）、环形闭环（反馈/循环）三选一，写入 `layout_type`；节点 ≥3 才考虑环形。
- **来自 社科机制图**：顶刊黑白印刷友好——浅灰分区、实线/虚线区分层级，关键信息不靠颜色传达；成图按 A4 版面比例，插入 docx/PDF 后仍清晰。
- **来自 ProcessOn 思维导图**：阶段/模块做 MECE 检查——同级不重叠、不遗漏、粒度一致（“中试放大”不能与“取样”并列）；保留材料原有标题骨架，末级压缩成一句话（≤20 字），一个节点只放一个信息点。
- **来自 ProcessOn 思维导图**：结构按内容选——线性流程、纵向层级、时间线、矩阵表、鱼骨（多因一果）；层级不超过 6 级；不用 HTML 表格伪装成图，矩阵模式用真实表格线 + 灰表头 + 斑马纹。
- **两篇合取后的改造**：`result.json` 增加 `diagram_type ∈ {framework, chain, roadmap, matrix}`、`layout_type ∈ {horizontal, vertical, circular}`、`mece_check{overlap, gap, granularity}`、`max_depth` 与 `render_backend: "local_svg"`；参考中的云端渲染/付费调用在本环境不可用，只采用其图型与布局规则。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 模式 | 装置/路线判定与 task.md 一致 | 重判并调整结构 |
| G2 | 完整 | 组件或阶段无缺失 | 补全并连好流向 |
| G3 | 流向 | 每条连线有方向与含义 | 补箭头/标签 |
| G4 | 条件 | 关键工况/判据齐全、单位正确 | 补标注或标 not_specified |
| G5 | 来源 | 每元素有来源定位 | 补来源 |
| G6 | 导出 | SVG/PNG/PDF 均有效 | 重新渲染 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 装置图像插画 | 追求写实导致歧义 | 改抽象符号+标签 |
| 流向不清 | 连线无箭头 | 补方向与物料名 |
| 工况缺失 | 只有设备名 | 补温度/压力/流量 |
| 路线无判据 | 决策分支没有条件 | 补判据与走向 |
| 阶段缺输出 | 只有活动 | 补 inputs/outputs |
| 导出占位 | 文件过小/头错误 | 真实渲染重导出 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`mode`、`elements[]`、`connections[]`、`artifacts`
- [ ] `diagram.svg` 可解析且可编辑
- [ ] PNG/PDF 真实有效
- [ ] 组件/阶段完整、流向明确
- [ ] 工况或判据齐全
- [ ] 每元素有来源定位
- [ ] 未虚构材料外参数

## 最小示例（示例）

输入：方法 §2.1–2.3（6 个部件、3 条管路、2 个工况点）+ 技术路线 4 阶段 1 判定。
输出：`fig4.svg` 两 panel；`components` 6、`stages` 4、`decisions` 1；
`preview/fig4.png` 2126×1299 @300 dpi；`status: "completed"`。
