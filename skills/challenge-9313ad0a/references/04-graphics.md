# 科研绘图赛道通用流程（Graphics Playbook）

适用于：科研示意图、数据图表、生物机制/通路图、实验装置与技术路线图、Graphical Abstract。

## 0. 一句话原则

**图是数据的另一种写法，不是装饰。** 每个图形元素要么映射到材料中的实体/关系/数值，
要么标为示意（`schematic`）。评分明确写着：技术性文件检查不能替代科学正确性判断。

## 1. 输入盘点

1. `/input/task.md`：图种、用途（正文图/封面图/摘要图）、尺寸、格式、分辨率、配色要求、
   必须出现的元素。
2. `/input/case.json`：`case_id`、`workorder`（如 `source_value_chart`、
   `biological_mechanism_pathway`、`apparatus_and_technical_route`、
   `editable_relationship_schematic`、`graphical_abstract`）、源材料路径。
3. 材料：原始数据表、论文图、正文段落、参考图、模板/母版、字体清单。
4. 生成 `/app/work/figure-spec.json`（见 §3），再动手画。

## 2. 图种对应的取证重点

| 图种 | 必须从材料里拿到的 | 不允许凭空出现的 |
|---|---|---|
| 数据图表 | 每个系列的数值、误差、n、坐标轴标签与单位 | 数据点、拟合线、显著性星号 |
| 示意图/关系图 | 实体清单、关系方向、层级 | 未在材料中出现的通路或组分 |
| 机制/通路图 | 分子名、箭头方向、抑制/激活语义、区室 | 额外调控因子、未验证的互作 |
| 装置图 | 部件名、连接顺序、介质流向、条件标注 | 未提及的部件或接口 |
| 技术路线图 | 阶段、输入输出、判定分支 | 未定义的方法步骤 |
| Graphical Abstract | 一句主张 + 3–5 个关键元素 + 结果指向 | 与正文结论不符的视觉暗示 |

## 3. 先写规格，再画图

`/app/work/figure-spec.json`：

```json
{
  "figure_id": "Fig.3",
  "kind": "source_value_chart",
  "canvas": {"width_mm": 180, "height_mm": 120, "dpi": 300},
  "style": {"font_family": "Liberation Sans", "font_size_pt": 8,
            "palette": "colorblind-safe", "line_width_pt": 1.0},
  "panels": [
    {"id": "a", "type": "bar", "axes": {"x": {"label": "Catalyst", "unit": null},
      "y": {"label": "Conversion", "unit": "%", "range": [0, 100]}},
     "series": [{"name": "A", "values": [72.1, 84.3], "error": {"type": "SD", "values": [2.1, 1.8]},
                 "n": 3, "source": "materials/results.csv#row=2-3"}]}
  ],
  "elements": [
    {"id": "e1", "label": "Catalyst bed", "shape": "rect",
     "source": {"path": "materials/method.md", "locator": "§2.1"}}
  ],
  "schematic_marks": ["e7 箭头为示意，材料未给方向"],
  "render": {"svg": "figures/fig3.svg", "png": "figures/fig3.png", "pdf": "figures/fig3.pdf"}
}
```

规格里每个 `series` / `element` 都必须有 `source`；没有来源的进 `schematic_marks`。

## 4. 绘制规范

- **可编辑优先**：主产物是 SVG（文本保持为 `<text>`，不要转曲；分层用 `<g id="...">`）。
  PNG/PDF 是导出件，不能只有位图。
- **画布与字号**：按目标版面定物理尺寸（单栏 85 mm / 双栏 180 mm），字号 ≥ 7 pt，
  导出 300 dpi 起。
- **坐标轴**：标签 + 单位齐全；对数轴显式标注；误差棒在图注说明类型与 n。
- **配色**：色盲友好（Okabe–Ito 或 viridis 系）；不用红绿对立传达唯一信息；灰度打印仍可区分
  （线型/标记差异）。
- **字体**：只用 `font-discovery` 能确认存在的字体（如 Liberation 系列）；缺字体时降级并记录。
- **标注**：面板编号 `a/b/c` 统一位置；比例尺、箭头、图例不得遮挡数据。
- **不得**：贴图冒充矢量、拉伸位图、用渐变/阴影掩盖数据密度、加 3D 效果。

## 5. 生成与导出

0. 先用生成器把规格变成 SVG，再手工润色（不要从空文件手写坐标）：
   - 数据图：`scripts/make_chart.py --spec figure-spec.json --out fig.svg`
     （bar / line / scatter，含误差棒、图例、刻度、显著性括号）；
   - 示意图/通路/装置/路线：`scripts/make_schematic.py --spec schematic.json --out fig.svg`
     （节点分区、激活箭头、抑制 T 形端、转运虚线、区室框）。
   两个脚本都是纯标准库，输出文本仍为 `<text>`、元素按 `<g id>` 分层。
1. 用 `scripts/svg_lint.py` 校验：XML 合法、`viewBox` 存在、无 `<script>`/外链、
   文本未被转曲、`font-family` 在白名单内。
2. 导出：
   - PDF：优先 `latex-compiler` 或 `office-renderer` 提供的转换；也可用 SVG→PDF 工具。
   - PNG：`pdf-render-tool` 或 `browser-automation` 截图，指定 dpi/尺寸。
3. 导出后回读：PNG 检查尺寸与非空白像素比例；PDF 检查页数与可打开性。
4. **预览必须有**：`preview/fig3.png`，且确实由源文件渲染而来（不得用占位图）。

## 6. 输出契约

```json
{
  "case_id": "…",
  "figure_id": "Fig.3",
  "kind": "source_value_chart",
  "artifacts": [
    {"role": "editable", "path": "figures/fig3.svg", "format": "svg"},
    {"role": "export", "path": "figures/fig3.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/fig3.png", "format": "png",
     "width_px": 2126, "height_px": 1417}
  ],
  "element_source_map": [
    {"element_id": "series:A", "maps_to": {"path": "materials/results.csv", "locator": "row 2-3"},
     "kind": "reported"}
  ],
  "checks": {
    "xml_valid": true, "fonts_embedded_or_listed": true,
    "text_as_text": true, "external_refs": 0,
    "dpi": 300, "colorblind_safe": true, "grayscale_distinguishable": true
  },
  "schematic_disclaimers": ["e7 的箭头方向为示意"],
  "uncertainty": [{"item": "误差棒类型", "reason": "图注未说明", "impact": "按 SD 处理并标注"}],
  "change_log": [{"step": 1, "action": "建立元素-来源映射", "evidence": "figure-spec.json"}]
}
```

## 7. 异常处理

| 情况 | 处理 |
|---|---|
| 原始数据缺失，只有图 | 从图中读数，标 `estimated: true` + 读数误差；不伪造原始表 |
| 图注与正文冲突 | 保留两处值，`conflicts[]` 记录，图面采用正文值并注明 |
| 指定字体不存在 | 用可用的等宽度替代，`font_substitution` 记录原名/替代名 |
| 导出工具缺失 | 交付 SVG + 说明缺失的导出件与原因，`status=partial` |
| 数据点过多无法逐一标注 | 用趋势 + 代表点，说明抽样规则 |
| 材料含受版权图片 | 不直接嵌入，重绘并注明"重绘自 Fig.X" |

## 8. 自检

- [ ] 每个 series/element 有 source 或 schematic 标记
- [ ] 轴标签、单位、误差类型、n 与材料一致
- [ ] SVG 文本仍是文本，分层可编辑
- [ ] PNG/PDF 由源文件真实渲染，尺寸/dpi 达标
- [ ] 色盲与灰度可读
- [ ] 没有材料外的实体或数值
