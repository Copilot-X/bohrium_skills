# 可编辑示意图与改稿参考（可选）

本文件是方法参考，**不是必须执行的闸门**。`task.md` 与材料优先；时间不足时
按 `SKILL.md` 的交付物优先流程直接出图。配合 `templates/entities.json`、
`scripts/build_schematic.py`、`scripts/compare_svg.py`、
`scripts/check_schematic_result.py` 使用。

## 1. 先建实体-关系表（图只是表的渲染）

```json
{"entities":[{"id":"E1","label":"Catalyst bed","type":"component",
 "source":{"path":"materials/method.md","locator":"§2.1"},"kind":"reported"}],
 "relations":[{"id":"R1","from":"E1","to":"E2","semantic":"flow",
 "direction":"forward","label":"product","source":"§2.1","kind":"reported"}]}
```

- 每个实体/关系有 id、标签、来源；推断项标 `kind: "inferred"`。
- 语义：`flow|activate|inhibit|convert|contain|bind|translocate`；
  不同语义用不同线型/箭头，同一含义不要混用两种线型。
- 没有拓扑时按语义排层，不臆造回路；材料外的实体、浓度、时间点不画。
- 方向不明：无箭头连线 + `"direction": "undetermined"` + 免责说明。

## 2. 画可编辑 SVG

- 主产物 SVG：文字保留 `<text>`，节点/边各自 `<g id="E1">`/`<g id="R1">`；
  自包含（无 `<script>`、无 http/file 引用）；根元素带 `viewBox`。
- 激活实线箭头、抑制 T 形端、转运虚线箭头、包含用分区框；
  分区框只包 rect 与标签，不要嵌套节点组（否则改一个节点会被误判为改分区）。
- 字号 ≥7 pt；节点文字 ≤2 行；标签不出框、不压线；节点不重叠。
- 色盲友好（Okabe–Ito），灰度打印靠线型/形状可辨。
- 可用 `build_schematic.py` 由表生成；它报结构错误时修表或手写 SVG，
  脚本失败不阻塞交付。

## 3. 编辑模式（最小干预）

1. 复制起始 SVG 到输出路径，解析其 `<g id>` 与文本，建 `element-inventory.json`。
2. 只改工单点名的元素；未点名元素的位置/样式/文字保持不变。
3. 新增元素必须有来源；删除元素必须在 `change_log` 写 `removed` + 理由。
4. 产出 `edit-diff.json`：`{element_id, action, before, after}`；坐标级移动也要记。
5. 不得整图重画后声称“编辑”。
6. 先把 task.md 的修改点转成 `/app/work/workorder.json`：

```json
{"targets": ["E2"], "add": ["E5"], "remove": ["E3"], "source": "task.md §edit"}
```

```bash
python3 "$SKILL_DIR/scripts/compare_svg.py" --before /app/work/start.svg \
  --after /app/output/figures/fig1.svg --workorder /app/work/workorder.json --json
```

`compare_svg.py` 只比较 element id 与组内文本；它报 `1` 表示越界改动，
报 `2` 表示 SVG/workorder 不可读。审计失败不阻塞交付，但要记录并人工核对。

## 4. 几何与文本检查

- 文本在框内且居中；放不下先换行，再以 0.5 pt 步长缩字号（最低 7 pt）。
- 箭头尖端 10 px 内不放文字；箭头在节点边框前 ≥12 px 停住。
- 同层间距 ≥16 px，文字与边框 ≥8 px；层间距 ≥40 px。
- `viewBox` 覆盖全部内容 + 图例 + 16 px 余量。
- 至少人工核对：无重叠、无穿字、无标签溢出、无缺失 marker 定义。

## 5. 导出（真实渲染，不伪造）

```bash
python3 "$SKILL_DIR/scripts/export_schematic.py" \
  --svg /app/output/figures/fig1.svg --outdir /app/output --dpi 300 --json
```

- 探测顺序 `cairosvg -> rsvg-convert -> inkscape`；脚本校验 PNG `\x89PNG`、
  PDF `%PDF`，无效文件删除后换下一个工具。
- 全部不可用：交付 SVG + 自包含 HTML 预览，`status=partial`，
  `exceptions` 写 `export_unavailable`，`export_artifact: null`。
- 不要调用 `latex-compiler`/`office-renderer`/`pdf-render-tool`/`browser-automation`。

## 6. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| 图与表不一致 | SVG 多/少实体 | 以表为准重渲染 |
| 无标签箭头 | 关系含义不明 | 补 semantic 与 label |
| 编辑丢元素 | 未点名元素消失 | 从基线恢复 |
| 文字溢出 | 标签超出节点框 | 换行/扩框/缩字号 |
| 导出占位 | PNG 只有几百字节/头错 | 删除，换真实渲染器 |
| 推断当事实 | 推断关系实线 | 改灰色虚线并标 inferred |
| 导出成功但科学错误 | 实体/方向与材料不符 | 以材料为准改图，不以导出通过为质量证明 |

## 7. 交付前检查（建议）

- [ ] 每个节点/边有 source 或 inferred。
- [ ] 编辑模式只改工单元素，`preserved` 可核对。
- [ ] SVG 可解析、文本未转曲、自包含。
- [ ] PNG/PDF 真实渲染，尺寸/dpi 记录真实；没有导出时状态为 partial。
- [ ] `check_schematic_result.py` 返回 `ok: true`；非 0 时记录原因再交付。
