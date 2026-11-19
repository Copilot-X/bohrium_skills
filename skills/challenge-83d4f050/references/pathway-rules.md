# 通路图规则（Pathway rules）

## 1. 区室与实体

- 区室按信息流排序：Extracellular → Plasma membrane → Cytoplasm → Nucleus，
  `order` 递增；渲染器按 `order` 排成 LR/TB 条带。
- 一个实体一个节点；同义名/旧名放 `aliases[]`，不画成多个节点。
- 修饰状态放 `sublabel`（`p-ERK`、`GTP`、`nuclear`），不写成独立分子。
- 复合物/家族默认一个节点；只有亚基有独立边时才拆。
- `entity_kind` 用 `gene|protein|complex|cell|metabolite`；基因在
  `naming.gene_style=italic` 或 `italic=true` 时斜体。
- `type` 决定形状：ligand=circle，receptor/kinase/phosphatase/gtpase=rounded，
  tf=hexagon，gene/rna/complex=rect，metabolite=diamond，cell=rounded。

## 2. 边语义（强约束）

| `effect` | 线型 | 端点 | 原文触发词 | 说明 |
|---|---|---|---|---|
| `activate` | 实线（蓝） | 箭头 | activate/promote/induce/upregulate/recruit | 因果激活 |
| `inhibit` | 实线（橙） | T 形横杠 | inhibit/suppress/block/reduce/repress | 因果抑制 |
| `bind` | 细实线（灰） | 无箭头 | bind/associate/form complex/co-IP | 结合，不表示方向 |
| `translocate` | 虚线（深灰） | 箭头 | translocate/import/export/shuttle | 分子本身跨区室移动 |
| `modify` | 实线（紫） | 箭头 + `P` | phosphorylate/ubiquitinate/cleave/acetylate | 修饰；标签默认 `P` |
| `association` | 细点线（灰） | 无箭头 | associated with/correlate/co-vary | 只相关，不画因果 |
| `unknown_link` | 虚线（灰） | 箭头 + `?` | 上下游出现但机制缺失 | 不补经典通路 |
| `inferred` | 灰色虚线 | 保留 `base_effect` 的端点 | 间接/关联证据 | 必须写免责说明 |

跨区室判定看“分子本身是否移动”：

- 分子移动 → `translocate`，首尾节点分别放在两个区室；
- 只是信号跨膜/跨区室传递 → 保留 `activate`/`inhibit`，在 `mechanism` 写传递方式；
- 配体在胞外、受体在质膜、激酶级联在胞质、TF/基因在核内。

## 3. 来源与推断

- 每条 `reported` 边必须有 `source{path,locator,quote?}`；
- 材料只说“相关/伴随” → `association`（无箭头）或 `inferred`（若需极性）；
- 证据不足但必须表达的连接 → `kind:"inferred"` + `base_effect`（activate/inhibit）
  + 灰色虚线 + `schematic_disclaimers`；
- 不凭领域常识补中间节点；缺中间环节写 `unknown_link` 或 `omitted[]`；
- 综述图只作结构参考，不能当本研究证据；冲突写 `conflicts[]`。

## 4. 布局与可读性

- 主链从左到右（LR）或从上到下（TB）；并列节点同 rank、不同 order；
- 反馈边用 `route:"curve"` 或 `"orthogonal"`，避免穿过节点；
- 区室先画（背景），再画边，最后画节点；箭头/T 形端点停在节点外；
- 字号 ≥7 pt；节点名最多 2 行；边标签放在空白处，避免压节点；
- 图例覆盖实际出现的所有边类型；颜色只作辅助，线型和端点才是语义；
- 基因斜体、蛋白正体；不写“↑/↓”替代箭头。

## 5. 审计错误码与修复

| code | 含义 | 修复 |
|---|---|---|
| `SVG_INVALID` | XML 不可解析 | 重新渲染或修台账 |
| `SVG_EXTERNAL_REFS` | 外部 href/src | 删除外链，改内嵌 |
| `SVG_HAS_SCRIPT` | 含 `<script>` | 删除脚本 |
| `SVG_HAS_RASTER_IMAGE` | 含 `<image>` | 用矢量图元重画 |
| `COMPARTMENT_GROUPS` | 区室 `<g id>` 缺失/多余 | 检查 `compartments[].id` |
| `MOLECULE_GROUPS` | 分子组缺失 | 检查 `molecules[].id` |
| `EDGE_GROUPS` | 边组缺失 | 检查 `interactions[].id` |
| `UNMAPPED_IDS` | 覆盖不全 | 补组或补 `content_map` |
| `MISSING_SOURCE` | reported 边无来源 | 补 source 或改 inferred |
| `EDGE_SEMANTICS` | 线型/端点与 effect 不符 | 修 effect 或 `base_effect` |
| `LEGEND_INCOMPLETE` | 图例缺边类型 | 重新生成图例 |
| `TEXT_MISSING` | 标签不在 SVG 文本中 | 检查 label/别名 |
| `TEXT_OVERFLOW` | 文字溢出节点 | 缩短标签或增大画布 |
| `LABEL_OVERLAP` | 边标签压节点 | 改 route 或缩短标签 |
| `EXPORT_INVALID` | PDF/PNG 文件头无效 | 重导出；实在不可用则 `partial` |
| `PIXEL_REVIEW_FAILED` | 预览空白/裁切/过单调 | 检查布局和导出 |

## 6. 最小台账

```json
{
  "case_id": "case-id",
  "figure_id": "Fig.2",
  "canvas": {"width_mm": 180, "height_mm": 118, "direction": "LR"},
  "compartments": [
    {"id": "C1", "label": "Extracellular", "order": 0,
     "source": {"path": "paper.txt", "locator": "line 2"}}
  ],
  "molecules": [
    {"id": "M1", "label": "L1", "entity_kind": "protein", "type": "ligand",
     "compartment": "C1", "rank": 0, "order": 0,
     "source": {"path": "paper.txt", "locator": "line 2", "quote": "..."}},
    {"id": "M2", "label": "R1", "entity_kind": "protein", "type": "receptor",
     "compartment": "C1", "rank": 1, "order": 0,
     "source": {"path": "paper.txt", "locator": "line 2", "quote": "..."}}
  ],
  "interactions": [
    {"id": "I1", "from": "M1", "to": "M2", "effect": "activate",
     "kind": "reported", "label": "+",
     "source": {"path": "paper.txt", "locator": "line 3", "quote": "..."}}
  ]
}
```

完整字段见 `templates/pathway-spec.schema.json`。
