# 生物通路图绘制手册（Pathway Figure Playbook）

本手册服务于 `biological_mechanism_pathway` 工单：从科研材料出发，先建
“区室 → 实体（分子/细胞/复合物）→ 关系（激活/抑制/转运/结合/修饰）→ 来源”
四层表，再生成可编辑 SVG、真实导出件和逐元素来源映射。

三条硬规则：

1. **无来源不入图**：每个节点、每条边、每个数值都要能回到材料或权威命名；
   推断关系必须标 `inferred`、画虚线并写入 `schematic_disclaimers`。
2. **语义决定线型**：激活、抑制、转运、结合、修饰各有固定线型与端点，
   同一张图内不得混用；颜色只能辅助，不能作为唯一区分手段。
3. **图是表的渲染**：先写 `pathway.json`，再由 `scripts/pathway_to_spec.py`
   生成 spec/SVG；不要手写坐标绕过实体表与校验。

---

## 1. 通路层级与构图

### 1.1 常见信息流

```
刺激/配体 → 受体/通道 → 近膜转导子 → 激酶级联 → 转录因子 → 靶基因/效应
   ↑                                                              │
   └──────────────── 反馈/旁分泌/负调控 ──────────────────────────┘
```

| 层级 | 典型实体 | 建议区室 | 构图提示 |
|---|---|---|---|
| 刺激 | 配体、药物、离子、机械力、病原 | Extracellular / Lumen | 放最外侧，rank 0 |
| 受体 | RTK、GPCR、离子通道、整合素 | Plasma membrane | 跨膜画在膜区室，不要放胞质 |
| 近膜转导 | RAS、G 蛋白、接头蛋白、脂质 | Cytoplasm / membrane inner leaflet | 与受体相邻 rank |
| 激酶级联 | RAF、MEK、ERK、AKT、JAK/STAT | Cytoplasm | 同层级横向并列，顺序箭头 |
| 转录因子 | NF-κB、STAT、FOS、FOXO | Nucleus | 入核边用 translocate |
| 靶基因/效应 | 基因、分泌因子、代谢物、表型 | Nucleus / Extracellular | 末端节点，避免继续发散 |
| 调控子 | 磷酸酶、泛素连接酶、抑制蛋白 | 与靶点同区室为佳 | 放在靶点同 rank 的另一 order，用纵向 T 形边 |

### 1.2 节点预算

- 一张图 **5–15 个主节点**；超过 15 个先合并同类（家族、复合物、同通路支路）。
- 一个节点只承担一个实体；不要把“配体+受体”“基因+蛋白”塞进同一个节点。
- 并列关系同 rank；递进关系按信息流增加 rank；调节条件放旁侧，不插入主链。
- 支路在分叉点分开、在共同下游汇合；汇合边不要重复画同一语义。
- 反馈环必须有明确起点和终点，不能只画一条无标签回线。

### 1.3 布局类型

| 结构 | 布局 | 说明 |
|---|---|---|
| 单链 | `LR` 横向 | 刺激在左、效应在右，最易读 |
| 受体—核内 | `LR` 或 `TB` | `TB` 时受体在上、核在下 |
| 核心+分支 | `LR` + 同 rank 多 order | 分叉点后多条平行支路 |
| 闭环/反馈 | `LR` + 旁侧调节节点 | 反馈边用 `route: "curve"` 或 `orthogonal`，避开节点 |
| 多区室 | `LR` + `groups` | 区室由外到内排列，组框只做背景 |

---

## 2. 实体、命名与形状

### 2.1 实体类型

| `type` | 例子 | 形状 | 说明 |
|---|---|---|---|
| `ligand` | EGF、TNF、Ca2+ | circle | 小分子/离子也用此型 |
| `receptor` | EGFR、TNFR1、GPCR | rounded | 放 Plasma membrane |
| `kinase` | RAF、MEK、ERK、AKT | ellipse | 激酶/磷酸化酶 |
| `phosphatase` | PTEN、DUSP6 | ellipse | 负调控酶 |
| `gtpase` | RAS、RHO | ellipse | 小 G 蛋白 |
| `tf` | NF-κB、STAT3、FOS | hexagon | 转录因子，通常在核内 |
| `complex` | NLRP3 inflammasome、AP-1 | rect | 复合物整体一个节点 |
| `metabolite` | ROS、ATP、乳酸 | diamond | 代谢物/离子/第二信使 |
| `gene` | IL1B、CCND1 | rect | 基因/启动子/增强子 |
| `rna` | mRNA、miRNA | rect | 转录本 |
| `channel` | TRPV1、AQP4 | rounded | 通道/转运体 |
| `cell` | 巨噬细胞、T 细胞 | rounded | 细胞类型 |
| `protein` | 接头蛋白、支架蛋白 | rounded | 未归入上述类的蛋白 |
| `other` | 其他 | rect | 需在 `note` 说明 |

### 2.2 命名规范

- **基因斜体、蛋白正体**：`IL1B`（基因）与 `IL-1β`（蛋白）不能混用；
  `display.italic` 记录排版意图，`entity_kind` 记录 `gene|protein|complex|cell|metabolite`。
- **一个实体一个节点**：同义名/旧名放 `aliases[]`，不要为 `KRAS` 与 `RAS` 各画一个节点。
- **物种前缀**：仅跨物种比较时使用 `human HIF1A` / `mouse Hif1a`；同一物种内不重复。
- **家族合并**：材料不区分亚型时用 `AKT1/2/3` 一个节点；材料分别给数据时才拆开。
- **复合物**：`NLRP3/ASC/pro-caspase-1` 作为一个 `complex` 节点；只有在亚基有独立边时才拆。
- **修饰状态**：用 `sublabel` 写 `p-S473`、`Ub-K48`、`GTP-bound`，不另造节点。
- **细胞类型**：`cell` 节点写标准名称；不要用“细胞”这种无法区分的泛称。
- **别名冲突**：同名不同义（如 `CA` 既指碳酸酐酶又指钙）必须在 `note` 消歧；
  无法消歧时不要入图，写入 `uncertainty`。

### 2.3 区室

| `compartment` | 用途 | 默认位置 |
|---|---|---|
| Extracellular | 配体、分泌因子、胞外基质 | 最外 |
| Plasma membrane | 受体、通道、膜脂 | 第二层 |
| Cytoplasm | 激酶级联、接头蛋白、代谢 | 中间 |
| Mitochondrion / ER / Golgi / Lysosome | 细胞器事件 | 按需插入 |
| Nucleus | 转录因子、基因、染色质 | 最内 |
| Lumen / Tissue | 管道、组织、器官 | 按材料定义 |

- 区室用浅色虚线圆角框，标签在左上角、斜体；组框在节点与边之下。
- 跨膜受体画在膜区室，不要画进胞质；配体在胞外，不要画进核内。
- 一个分子只有一个 `compartment`（作用区室）；发生转运时用 `translocate` 边表达。
- 跨区室的**信号**（受体→胞质激酶、胞质→核内 TF 的激活）保留 `activate/inhibit`，
  但必须在 `mechanism` 写明传递方式；跨区室的**分子移动**才用 `translocate`。
  判断依据是“分子本身是否换区室”，不是“边两端是否同区室”。

---

## 3. 关系语义（核心）

| `effect` | 含义 | 线型 | 端点 | 原文依据 | 渲染 `kind` |
|---|---|---|---|---|---|
| `activate` | 激活、促进、上调、募集、诱导 | 实线 | 实心箭头 | activate / promote / induce / upregulate / recruit | `activate` |
| `inhibit` | 抑制、阻断、负调控、降低 | 实线 | T 形横杠 | inhibit / suppress / block / reduce / negative regulation | `inhibit` |
| `translocate` | 分子跨区室移动（入核、出核、分泌、内吞） | 虚线 | 实心箭头 | translocate / import / export / shuttle / traffic to | `translocate` |
| `bind` | 结合、组装、共定位复合物 | 细实线 | 无箭头 | bind / associate / form complex / co-IP | `bind` |
| `modify` | 磷酸化、泛素化、切割等共价修饰 | 实线 | 实心箭头 | phosphorylate / ubiquitinate / cleave / acetylate | `default` + 标签 `P`/`Ub` |
| `association` | 仅相关，无方向性因果证据 | 细实线 | 无箭头 | correlate / associated with / co-varies | `bind` + 标签 `assoc` |
| `unknown_link` | 材料暗示连接但缺中间环节 | 虚线 | 实心箭头 | 上下游均出现、中间机制缺失 | `inferred` + 标签 `?` |
| 任意 `inferred` | 由相关/间接证据推断的上述关系 | 灰色虚线 | 实心箭头 | 明确标为推断 | `inferred` + 极性标签 |

### 3.1 判定顺序

1. 先问“原文用的是哪个动词”，不要用领域常识替换原文动词。
2. 再问“方向是否明确”；只有“相关/伴随”时用 `association`，不画箭头。
3. 再问“分子是否换区室”；是则 `translocate`，否则按激活/抑制/结合/修饰归类。
4. 最后问“证据是直接实验、间接证据还是推断”；推断一律 `kind: "inferred"`，
   统一渲染为灰色虚线箭头，`effect` 的极性写在边上（`+`/`-`）并写入免责说明。
   这样图例只需一条 “inferred / uncertain” 线型，不会出现图例与图面不一致。

### 3.2 不允许的写法

- 用 `+`/`-` 文本或颜色代替箭头/T 形端。
- 把“相关”画成激活箭头；把“抑制”画成激活箭头。
- 用颜色同时表示激活和抑制（色盲不可读，且图例易冲突）。
- 为“经典通路”补材料没有的中间节点；缺环节用 `unknown_link` 或写入 `omitted`。
- 同一个分子因修饰状态画成多个节点；用 `sublabel`。
- 反馈边穿过节点或文字；先调 rank/order，再换 `route`。

### 3.3 反馈与分支

- 负反馈：把抑制子放在靶点同 rank 的另一 `order`，用纵向 T 形边指向靶点，
  避免长距离回线压过主链。
- 正反馈：用带标签的 `activate` 回线，标 `feedback`，不要与主链箭头混淆。
- 前馈：分叉点同时连到下游两个节点时，两条边都要有独立来源；
  只有一条有来源时，另一条必须是 `inferred`。
- 汇合：多条边进入同一节点时，标签分别写在边中点；不要合并成一条无来源的粗箭头。

---

## 4. 证据等级与来源映射

### 4.1 关系证据

| `kind` | 含义 | 图面 | 结果字段 |
|---|---|---|---|
| `reported` | 材料明确陈述或有直接实验 | 实线/规定线型 | `kind: "reported"` |
| `inferred` | 由相关、表达模式、数据库或常识推断 | 灰色虚线箭头 + 极性标签 + 免责说明 | `kind: "inferred"` |

可选 `evidence_strength`：

| 等级 | 判据 | 用法 |
|---|---|---|
| `strong` | 直接扰动 +  rescue / 多物种多方法一致 | 主链实线 |
| `moderate` | 直接结合/激活实验，或两个独立证据 | 主链实线 |
| `weak` | 单一相关、单一组学、单一模型 | 考虑虚线或标注 |
| `speculative` | 仅推断、无直接证据 | 必须 `inferred` + 虚线 |

### 4.2 来源对象

```json
{"path": "materials/results.md", "locator": "p.3, Fig.2A",
 "quote": "EGF bound EGFR and promoted RAS activation."}
```

- `path` 必须是材料中的真实文件；`locator` 精确到页/段/图注/表。
- `quote` 尽量逐字；改写引文会破坏可核验性。
- 每条 `reported` 交互必须有来源；`inferred` 也应有来源（说明推断起点）。
- 若原始材料是 PDF/图片，校验器不会自动核对引文（只给 warning）；
  请把解析文本另存为 `.md`/`.txt` 并让 `path` 指向它，或人工逐条核对后说明。
- 综述图只能作结构参考，不能当作本研究证据；数据库条目要写明数据库与条目 ID。
- 来源冲突时以本研究材料为准，冲突写入 `conflicts[]`，不要静默选一个。

### 4.3 数值与图例编码

- 只有材料给出数值时才编码表达量、显著性、置信度、时间点；
  缺值写 `n.d.`，不得当 0。
- 每个编码通道都要在图例中说明：通道（fill/line width/size）、映射对象、尺度、单位、来源。
- 颜色映射必须色盲友好；同时用形状/线型/标签冗余编码。
- 图例项必须与图面实际使用的线型、颜色、形状一一对应；不得出现图例有、图面无的项。

---

## 5. 布局与几何

- 画布：常规 `180 × 120 mm`；紧凑 `180 × 90 mm`；节点多时 `180 × 140 mm`。
- 字号 ≥ 7 pt；节点文字 ≤ 2 行；标签不出框、不压线。
- 同一 rank 的节点按 `order` 从上到下排列；分支多时增大画布高度。
- 箭头在节点边缘停住，不画到圆心；箭头尖端附近不放文字。
- 边标签带白色衬底；反馈边与主链交叉时用 `route: "curve"` 或调整 order。
- 区室组框留 ≥ 8 px 内边距；组框标签不与箭头重叠。
- 渲染顺序：背景 → 区室 → 边 → 节点 → 标题 → 图例 → 脚注。
- 主色不超过 5 种；同类型同形状；灰度打印仍能靠线型/形状区分。

---

## 6. 标准工作流（v0.6 交付物优先）

```bash
# 0) 解析安装目录（不要假设固定路径）
SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
echo "$SKILL_DIR"

# 1) 复制实体表模板（可选），按材料填写 compartments/molecules/interactions
cp "$SKILL_DIR/templates/pathway-entities.json" /app/work/pathway.json

# 2) 实体表 -> figure spec -> editable SVG + result 草稿
#    converter 不渲染、也不要求 PDF/PNG；先把真实 SVG 落盘
python3 "$SKILL_DIR/scripts/pathway_to_spec.py" \
  --pathway /app/work/pathway.json \
  --out-spec /app/work/figure-spec.json \
  --out-svg /app/output/figures/fig3.svg \
  --result-out /app/output/result.json \
  --root /app/output --reviewed --json

# 3) 语义核对后，可选真实导出；无渲染器时自动生成 HTML 预览
python3 "$SKILL_DIR/scripts/export_pathway.py" \
  --svg /app/output/figures/fig3.svg --outdir /app/output \
  --dpi 300 --update-result /app/output/result.json --json

# 4) 可选审计：0=通过，1=有问题，2=输入不可读；非 0 不阻塞交付
python3 "$SKILL_DIR/scripts/check_pathway_result.py" \
  --pathway /app/work/pathway.json \
  --spec /app/work/figure-spec.json \
  --svg /app/output/figures/fig3.svg \
  --result /app/output/result.json \
  --materials-root /input \
  --root /app/output --json
```

`--reviewed` 只能在逐条核对完激活/抑制方向与来源后使用；它表示“语义已人工确认”，
不是自动通过开关。未确认时不要加，结果应为 `status: "partial"`。
审计脚本失败时，把原因写进 `exceptions`（`audit_failed` / `audit_unavailable`）
后继续交付，不要为了过 checker 删减内容。

---

## 7. 常见错误与修复

| 错误 | 识别信号 | 修复 |
|---|---|---|
| 凭空补经典通路 | 出现材料没提的节点/边 | 删除或改 `unknown_link` + 免责 |
| 相关画成因果 | `associated with` 被画成实线箭头 | 改 `association`，无箭头 |
| 抑制画成激活 | T 形端缺失或语义反了 | 按原文动词改 `inhibit` |
| 跨区室移动用 activate | 分子入核/分泌却画实线箭头 | 改 `translocate`，虚线 |
| 受体画在胞质 | 受体节点不在膜区室 | 移到 Plasma membrane |
| 配体画在核内 | 配体节点在 Nucleus | 移到 Extracellular |
| 同一分子重复节点 | 同名节点出现两次 | 合并，用 `sublabel` 记修饰 |
| 推断关系实线 | `inferred` 边没有虚线 | spec 设 `dashed: true` |
| 来源缺 locator | 只有文件名 | 补页/段/图注 |
| 引文与材料不符 | quote 在材料中找不到 | 改逐字引文或标 inferred |
| 反馈边穿节点 | 回线压过主链节点 | 调 order/route，或旁置调控子 |
| 图例与图面不符 | 图例项多于/少于实际线型 | 由实际 effect 生成图例 |
| 颜色唯一编码 | 去掉颜色后语义丢失 | 加形状/线型/标签冗余 |
| 数值无来源 | 节点写着表达量但无出处 | 补 source 或删除数值 |

---

## 8. 交付前自检

- [ ] `pathway.json` 的每个区室/分子/交互都有唯一 id 和来源
- [ ] 基因/蛋白命名区分，别名不另造节点，复合物/家族处理一致
- [ ] 每条边的 `effect` 与原文动词一致；相关关系未画成因果
- [ ] 抑制边均为 T 形端，转运边均为虚线，结合边无箭头
- [ ] 分子移动跨区室用 `translocate`；纯信号跨膜保留 activate/inhibit 并写 mechanism
- [ ] 推断边均为虚线，且逐条出现在 `schematic_disclaimers`
- [ ] 区室位置符合材料与生物学常识（配体胞外、受体膜、TF 核内）
- [ ] 图例完整且与图面一致；颜色不是唯一区分手段
- [ ] SVG 可解析、文本可编辑、viewBox 完整、无外部引用
- [ ] 每个区室/节点/边都有 `<g id>`，id 与表一致
- [ ] PNG/PDF 若存在，由 SVG 真实渲染且文件头有效；没有则 `partial` +
      `export_unavailable`，不伪造
- [ ] `check_pathway_result.py` 是可选审计；失败已记入 `exceptions`，未阻塞交付
- [ ] `omitted`、`conflicts`、`uncertainty` 写清裁剪与不确定性

## 9. 可编辑 SVG 与导出降级（方法要点）

- **可编辑性优先于导出件**：先把每个实体、关系、图例写成独立的 `<g id>`；
  文字保持 `<text>` 与 `font-family`，方便他人替换标签后重新导出。
- **自包含**：不引用外部字体/位图/脚本；跨机器打开不丢字、不丢图。
  位图只能做装饰并注明来源，不能承载机制语义。
- **分层顺序固定**：背景 → 区室 → 边 → 节点 → 标题 → 图例 → 脚注；
  这样编辑者可以单独选中箭头层修正语义，不用重画整图。
- **导出探测**：`cairosvg` → `rsvg-convert` → `inkscape`；每个输出必须通过
  文件头校验（PNG `\x89PNG`、PDF `%PDF`），失败文件立即删除，再试下一个。
- **降级**：全部渲染器不可用时，输出内联 SVG 的自包含 HTML 预览；
  `status=partial`、`export_artifact=null`、`exceptions` 写 `export_unavailable`。
  HTML 是“可查看的预览”，不是导出件，不能写进 `export_artifact`。
- **诚实记录**：`checks`/`compliance_checks` 只写实际运行的检查；
  没有测量过的 dpi、尺寸、字体不要编造。
