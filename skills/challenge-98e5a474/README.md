# editable-schematic-builder 0.6.0

科研示意图生成与编辑 skill 的简明说明。完整流程与输出契约以 `SKILL.md` 为准。

## 适用场景

- 从方法/结果材料生成科研示意图、机制图、关系图、流程图；
- 在给定 SVG 上做保留来源的最小改动（只改工单点名元素）；
- 需要可编辑源文件、真实 PDF/PNG 导出和逐元素来源映射的交付。

不适用于统计图表数值计算、海报版式设计、艺术插画。

## 输入与输出

输入：`/input/task.md`（以此为准）、`/input/case.json`、材料文件；编辑模式另有起始 SVG。

输出（`/app/output`）：

- `figures/fig1.svg`：文字保持 `<text>`、节点/边各有 `<g id>` 的自包含 SVG；
- `figures/fig1.pdf`、`preview/fig1.png`：真实渲染件（工具可用时）；
- `preview/fig1.html`：无渲染器时的自包含内联预览（降级）；
- `result.json`：任务字段 + media 官方别名（`status`、`editable_source`、
  `export_artifact`、`content_map`、`compliance_checks`、`exceptions`）。

## 快速开始

先解析安装目录（不要假设固定路径）：

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
echo "$SKILL_DIR"
```

```bash
# 1) 由实体表生成可编辑 SVG
python3 "$SKILL_DIR/scripts/build_schematic.py" --entities /app/work/entities.json \
  --out /app/output/figures/fig1.svg --json

# 2) 探测渲染器并导出 PDF/PNG（cairosvg -> rsvg -> inkscape -> matplotlib）
python3 "$SKILL_DIR/scripts/export_schematic.py" --svg /app/output/figures/fig1.svg \
  --outdir /app/output --dpi 300 --json > /app/work/export.json

# 3) 组装契约完整的 result.json（缺导出时自动 null + partial，不伪造）
python3 "$SKILL_DIR/scripts/assemble_result.py" --entities /app/work/entities.json \
  --svg /app/output/figures/fig1.svg --root /app/output \
  --export /app/work/export.json --out /app/output/result.json \
  --case-id case-001 --json

# 4) 交付前自查（0=通过，1=有问题，2=输入不可读；非 0 不阻塞交付）
python3 "$SKILL_DIR/scripts/check_schematic_result.py" --result /app/output/result.json \
  --entities /app/work/entities.json --svg /app/output/figures/fig1.svg \
  --root /app/output --json
```

编辑模式另用 `compare_svg.py --before ... --after ... --workorder ... --json` 核对改动范围。

## 最小示例（示例，不是正式答案）

实体表含 `E1 Catalyst A -> E3 Product`；运行命令 1 得到 2 节点 1 边的 SVG。
若本机有 matplotlib（`scientific-python` 通常预装），命令 2 会产出真实
PDF/PNG（`exported_by: "matplotlib"`），命令 3 写 `status: "answered"`。
若四种渲染器都不可用，命令 2 输出 `status: "partial"` + `export_unavailable`
和 HTML 预览；命令 3 同步写 `export_artifact: null`，绝不伪造 PNG/PDF。

## 已知限制

- 导出探测 `cairosvg`、`rsvg-convert`、`inkscape`、`matplotlib`；
  matplotlib 回退只覆盖本 skill 生成的 SVG 子集（矩形/圆/椭圆/多边形/路径/文本/箭头）；
- `compare_svg.py` 只比较 `<g id>` 与组内文本，纯坐标移动不会自动检出；
- 自动布局只保证基本避让，复杂拓扑需手工指定 `rank/order` 或坐标；
- 无渲染器时无法产出 PDF/PNG，只能交付 SVG + HTML 预览。

## 导出降级

探测顺序 `cairosvg -> rsvg-convert -> inkscape`；每个候选输出都校验文件头，
无效文件立即删除。全部失败时写自包含 HTML 预览，`status=partial`，
`exceptions` 记 `export_unavailable`，绝不伪造 PNG/PDF。

## 维护说明

- 版本 0.6.0；核心脚本 stdlib-only（Python 3.12+），matplotlib 仅作导出回退。
- 修改脚本、流程或契约时，同步更新 `SKILL.md`、本文件与
  `references/schematic-editing-playbook.md`。
- 不写入无法验证的分数、运行记录或截图；示例必须标注“示例”。
