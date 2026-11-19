# 科研图表解析 Skill（v0.6.0）

## 用途

把固定科研 case 中的原图片（必要时含原表）读成可核验的结构化结果。核心是**先判图件
模式、先答问题**：

- `quantitative_chart`：折线/柱状/散点/箱线/热图/光谱等有数值坐标轴的图，做轴检查与
  `plot_tables`（可选证据结构），读数带 `estimated/read_error`。
- `visual_figure`：显微图/凝胶/照片/示意图，只做 panel、特征、比例尺与视觉描述，
  不反推数值。
- `multi_panel`：逐 panel 判模式，`panel_modes` 覆盖每个 panel，禁止跨 panel 合并条件。

## 输入 / 输出

- 输入：`/input/case.json`（`case_id`、`figures[]`、`questions[]`）、图像/PDF、
  `/input/task.md`；可能含字面表格。
- 输出：`/app/output/result.json`。核心字段：`figures[]`、`answers[]`、
  `question_figure_map`、`panel_coverage`、`unreadable[]`；定量图可选
  `plot_tables`/`axis_check`/`panel_map`；官方别名字段
  `status`、`result`、`claims`、`uncertainties` 必须存在。

## 快速开始

```bash
# 1) 解析技能目录（平台通常装在 /skills/weekly-skill）
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
if [ ! -f "$SKILL_DIR/SKILL.md" ]; then
  SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
fi
echo "SKILL_DIR=$SKILL_DIR"

# 2) 读 references/figure-reading-playbook.md，盘点 figures/questions，判 figure_mode
# 3) 先写 /app/output/result.json 的 answers[] 与 question_figure_map
# 4) 再补定量图证据、视觉描述、panel_coverage、官方字段
# 5) 可选审计（失败不阻塞交付）
python3 "$SKILL_DIR/scripts/check_figure_analysis.py" \
  --result /app/output/result.json --input /input/case.json --json
```

## 最小示例（示例，不是正式答案）

**定量图**：折线图 x=温度(°C)、y=转化率(%)、三条催化剂曲线。`answers[0]` 直接回答
“催化剂 A 在 350 °C 达峰 92%（估读 ±2%）”，挂 `Fig.2/a`；`plot_tables` 只放峰值、
端点和比较点，估读标 `estimated: true`。

**视觉图**：显微图带 `100 µm` 比例尺。`observations` 只写“可见针状晶体、沿边缘聚集、
比例尺 100 µm”，`kind: "visual"`、`quantitative: false`；没有题目明确要求时不换算尺寸。

**负向**：图像无法解码时进 `unreadable[]`（`error_class: "decode_failed"`），相关题
`status: "insufficient_evidence"`，不猜图型和数值。

## 设计要点

1. **答案优先**：先答完所有 `questions[]` 并落盘，再补证据表；plot table 不是前置闸门。
2. **双模式隔离**：视觉图不生成 `plot_tables`、不写数值 `series.points`；定量图才做轴检查。
3. **问题到证据**：`question_figure_map` 每问至少一条 `figure_id + panel`；原表用
   `Table.N/table` 约定并在 `locator_kind` 标明。
4. **覆盖可审计**：`panel_coverage` 逐 panel 记录是否覆盖，`unreadable[]` 记录缺口与错误类型。
5. **不编造**：未给像素的图只列缺口；误差棒/显著性/n 只按原文写；缺失写 `not_provided`。
6. **脚本可选**：checker 与像素标定是加速器，失败时手工核对并在 `uncertainty` 记
   `audit_unavailable`。

## 支持文件

| 文件 | 作用 |
|---|---|
| `references/figure-reading-playbook.md` | 模式决策、读数顺序、视觉描述规则、失败模式 |
| `templates/figure-analysis.json` | result.json 骨架（含双模式、question_figure_map、panel_coverage） |
| `templates/figure-legend.md` | 需要补图注时的可独立成立模板 |
| `scripts/check_figure_analysis.py` | 可选审计：覆盖、双模式、轴、误差、官方字段 |
| `scripts/calibrate_figure_axes.py` | 可选：仅定量图的像素→数值线性/log 标定 |
| `scripts/emit_official.py` | 若随包提供：补齐官方 reading 别名字段 |

## 已知限制

- 视觉图只做定性描述；不保证显微结构解释正确，也不做无比例尺的尺寸测量。
- 定量图读数受分辨率和标注限制，估读误差按半刻度或满量程 1–2% 给。
- 不逐点穷举；只覆盖问题相关点、峰值、端点、交点与图注明标值。
- PDF 只读目标页；未给像素的其他图注只列为缺口，不构造。
- 平台可能把技能装到题目专属目录，命令必须先解析 `$SKILL_DIR`。

## 版本

- 0.6.0：新增 `figure_mode` 双模式、答案优先流程、`question_figure_map`、
  `panel_coverage`、`unreadable[]`；plot table 降为定量图可选证据。
- 0.5.0：plot-to-table 优先，checker/标定改为可选。
