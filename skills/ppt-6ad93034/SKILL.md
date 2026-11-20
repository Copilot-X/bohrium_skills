---
name: deck-editor
description: 学术 PPT 生成与编辑：从论文/材料生成可编辑 PPTX（结论式标题、每页备注、页级证据），或在给定起始稿上按工单做最小干预编辑并输出逐页 before/after diff；生成或修改后校验版式/占位符/备注/图片/溢出并给出真实预览。Generate an editable academic deck from source material, or edit an existing deck without touching unrequested content, then verify structure, notes and visuals. Use for PPT 生成、做 slides、PPT 改稿、幻灯片修订。
---

# 学术 PPT 生成与编辑（Deck Build & Edit）

## 用途与触发

两种工作流，先判定再动手：

| 输入信号 | `workflow_mode` | 说明 |
|---|---|---|
| task.md 要求“做一份 PPT / 把材料变成 slides / 按大纲生成” | `generate` | 从 `deck-spec` 生成可编辑 PPTX |
| task.md 给出**起始稿**并要求“修改/补充/调整” | `edit` | 基线只读，只改工单点名项 |
| 给模板/母版并要求在模板上出稿 | `adapt` | 用 `pptx_edit.py apply-template` 继承真实母版/版式 |
| 只要求盘点/提取文字 | `inspect` | 用 `inspect` + `list`，不要冒充交付 |

组织方口径（硬约束）：`actual starting deck where edit/adapt requires one`；
“保留来源的编辑”——未点名的页面、母版、版式、主题、图片、备注都必须原样保留。
技术性文件检查通过不等于演示的科学内容正确；页级证据映射必须单独做。

## 输入

- `/input/task.md`：受众、时长、固定页数/上限、语言、必须覆盖内容、比例、讲稿要求
- `/input/case.json`：`case_id`、`starting_deck`、`template_id`、`workorder`、`source_materials[]`
- 起始稿（编辑模式）：`.pptx`，可能含母版/版式/图表/图片/备注
- 源材料：论文全文/图表、数据表、已有 deck；只读实际文件
- 可选：`organizer-edit-workorder.json`（逐条编号的修改要求）

## 支持文件（必须使用）

所有命令在评测环境以 `{SKILL_DIR}` = `/skills/weekly-skill` 运行；本地为当前 skill 目录。

| 文件 | 触发条件 | 准确命令 / 用法 |
|---|---|---|
| `references/ppt-generation-playbook.md` | 任何生成/编辑任务开始前 | 先读：工作流判定、叙事、版式、QA、失败模式；按其表格执行 |
| `templates/deck-spec.json` | `generate` 模式 | `cp {SKILL_DIR}/templates/deck-spec.json /app/work/deck-spec.json` 后替换全部示例值 |
| `scripts/make_pptx.py` | 从 spec 生成可编辑 PPTX | `python3 {SKILL_DIR}/scripts/make_pptx.py --spec /app/work/deck-spec.json --out /app/output/deck.pptx` |
| `scripts/check_deck.py` | 生成后 / 编辑后 / 交付前**必跑** | `python3 {SKILL_DIR}/scripts/check_deck.py /app/output/deck.pptx --spec /app/work/deck-spec.json --expect-slides 6 --expect-aspect 16:9 --require-notes --require-evidence --result /app/output/result.json --json --out /app/work/check-deck.json` |
| `scripts/pptx_preview.py` | `office-renderer` 不可用，需要真实浏览器预览 | `python3 {SKILL_DIR}/scripts/pptx_preview.py /app/output/deck.pptx --out /app/work/preview/deck.html --width 1200 --json`，再用 Chrome 截图 |
| `scripts/pptx_edit.py` | `edit` / `adapt` 模式：盘点、改 run、改备注、换图、套模板、校验 | `inspect` / `list` / `set-text` / `replace` / `set-notes` / `set-image` / `apply-template` / `verify`，见第 5 节 |
| `scripts/validate_result.py` | 写 `result.json` 后 | `python3 {SKILL_DIR}/scripts/validate_result.py /app/output/result.json` |
| `scripts/validate_artifacts.py` | 交付前最后一道门 | `python3 {SKILL_DIR}/scripts/validate_artifacts.py --result /app/output/result.json` |

`check_deck.py` 是结构/内容/备注/图片/溢出/spec/result 的统一校验入口；
`--strict` 让 warning 也导致非零退出。**不要只运行 `make_pptx.py --check` 就交付。**

## 执行步骤 A：生成（generate）

1. **盘点输入**：按 playbook 第 2 节；确认受众、时长、页数、比例、语言、必须覆盖内容。
2. **先写叙事再写 spec**：一页一个结论；确定 `throughline` 与每页 `role/kind`。
   - 结果页 ≥ 1/3，方法页 ≤ 1/4；固定页数先保结果与局限。
   - 标题写结论句；数值/单位/条件与源材料逐字一致。
3. **写 `/app/work/deck-spec.json`**：从 `templates/deck-spec.json` 复制，替换所有示例值；
   每个内容页写 `notes` 与 `evidence`（`source.path` + `source.locator`）。
4. **生成**：在 `/app/work` 下运行（或 spec 中写图片绝对路径），否则相对图片路径会被静默跳过：
   `make_pptx.py --spec ... --out /app/output/deck.pptx`。
5. **回读校验**：`check_deck.py --spec ... --require-notes --require-evidence`。
   - 有 spec 不一致 → 改 spec 或改内容后重新生成；不要手工改 pptx 绕过。
   - 图片被静默跳过 → 检查 `image` 路径存在且为 PNG/JPEG。
6. **真实预览**：优先 `office-renderer` + `pdf-render-tool`；不可用时
   `pptx_preview.py` 生成 HTML，再用 Chrome headless 截图。
   `render.engine` 如实写 `office-renderer` 或 `html-from-pptx`；都没有写
   `preview.unavailable=true` 且 `status: "partial"`。
7. **写 `result.json`**：见输出契约；`page_evidence` 覆盖每一页。

## 执行步骤 B：编辑（edit）

1. **备份基线**：复制到 `/app/work/baseline/<原名>.pptx`，**全程只读**；所有写操作输出新文件。
2. **盘点**：`/app/work/deck-inventory.json`
   ```bash
   python3 {SKILL_DIR}/scripts/pptx_edit.py inspect /app/work/baseline/deck.pptx --json
   python3 {SKILL_DIR}/scripts/pptx_edit.py list    /app/work/baseline/deck.pptx --json
   ```
   记录：页数、每页文本 run（index + 文本）、备注、图片、版式、主题字体/配色。
3. **解析工单**：`/app/work/edit-plan.json`
   ```json
   {"edits": [
     {"edit_id": "E01", "slide": 6, "target": "text#3",
      "action": "replace", "from": "结果", "to": "A 在 350 °C 保持 92% 选择性",
      "source": {"path": "materials/paper.pdf", "locator": "Fig.2a"},
      "reason": "标题改为结论句"},
     {"edit_id": "E02", "slide": 9, "target": "notes",
      "action": "set_notes", "to": "…", "reason": "补讲稿"}]}
   ```
   工单有编号就沿用；没有则自建并写清依据。
4. **逐条执行**：
   ```bash
   # 精确替换第 N 页第 K 个文本 run
   python3 {SKILL_DIR}/scripts/pptx_edit.py set-text deck.pptx \
     --slide 6 --index 3 --text "A 在 350 °C 保持 92% 选择性" --out deck_v2.pptx --json
   # 单页字符串替换
   python3 {SKILL_DIR}/scripts/pptx_edit.py replace deck.pptx \
     --from "结果" --to "A 在 350 °C 保持 92% 选择性" --slide 6 --out deck_v2.pptx --json
   # 改写讲稿
   python3 {SKILL_DIR}/scripts/pptx_edit.py set-notes deck_v2.pptx \
     --slide 6 --text "…" --out deck_v2.pptx --json
   # 替换图片（保持原关系与位置）
   python3 {SKILL_DIR}/scripts/pptx_edit.py set-image deck_v2.pptx \
     --part ppt/media/image3.png --src materials/fig2a.png --out deck_v2.pptx --json
   ```
   规则：
   - 一次只改工单指定项；不确定 target 先 `list` 核实，不猜 index；
   - 文本长度变化大时先压缩文字，不缩字号；溢出检查见第 6 步；
   - 不得删除未点名的页面、不得重排顺序；
   - 工单允许新增页时用 `make_pptx.py` 生成补充页并说明合并方式，否则记 `unresolved`。
5. **校验**：
   ```bash
   python3 {SKILL_DIR}/scripts/pptx_edit.py verify /app/output/deck.pptx --json
   python3 {SKILL_DIR}/scripts/check_deck.py /app/output/deck.pptx \
     --expect-slides 12 --require-notes --json --out /app/work/check-deck.json
   ```
   要求 `verify.ok=true`、`slide_count` 与起始稿一致（除非工单允许增减）、无悬空关系。
6. **渲染核对**：用 `office-renderer` 出 PDF，再 `pdf-render-tool` 出改动页 PNG；
   逐页对比修改前后，确认未误伤未点名内容。无 office-renderer 时用
   `pptx_preview.py` + Chrome，并如实标注 `render.engine`。
7. **写变更记录**：`/app/work/edit-report.json`，每条含
   `edit_id / slide / before / after / action / verified`。
8. **落盘**：`deck.pptx`、`edit-report.json`、`deck-inventory.json`、
   `preview/`（改动页至少）、`result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "deck-generate|deck-edit|deck-adapt",
  "workflow_mode": "generate|edit",
  "status": "completed|partial",
  "deck": {"path": "deck.pptx", "slide_count": 12, "aspect_ratio": "16:9",
           "starting_deck": "materials/start.pptx",
           "slides_added": 0, "slides_removed": 0, "slides_reordered": false},
  "artifacts": [
    {"role": "editable", "path": "deck.pptx", "format": "pptx"},
    {"role": "report", "path": "edit-report.json", "format": "json"},
    {"role": "render", "path": "preview/deck.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/slide-06.png", "format": "png"}
  ],
  "page_evidence": [
    {"slide": 1, "kind": "title", "title": "…", "claims": []},
    {"slide": 6, "kind": "result", "title": "A 在 350 °C 保持 92% 选择性",
     "claims": [
       {"claim": "A 在 350 °C 的选择性为 92%",
        "source": {"path": "materials/paper.pdf", "locator": "Fig.2a"}}
     ]}
  ],
  "edits": [
    {"edit_id": "E01", "slide": 6, "action": "replace", "target": "text#3",
     "before": "结果", "after": "A 在 350 °C 保持 92% 选择性",
     "source": {"path": "materials/paper.pdf", "locator": "Fig.2a"},
     "verified": true}
  ],
  "preserved": {"master": true, "layouts": 4, "theme_fonts": ["Liberation Sans"],
                "images": 7, "untouched_slides": [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12]},
  "checks": {"zip_valid": true, "relationships_resolved": true,
             "slide_count_match": true, "notes_complete": true,
             "images_resolved": true, "text_overflow_risk": [],
             "placeholder_junk": [], "spec_conformance": true},
  "render": {"engine": "office-renderer|html-from-pptx|none", "pdf": "preview/deck.pdf",
             "png": ["preview/slide-06.png"], "unavailable": false},
  "unresolved": [],
  "uncertainty": [],
  "change_log": []
}
```

- 生成模式下 `edits` 可为空、`preserved` 省略；`page_evidence` 不可省。
- 编辑模式下 `starting_deck`、`untouched_slides`、逐条 `before/after` 必须有。
- `render.engine` 写实际使用的渲染器；HTML 预览写 `html-from-pptx`。
- 没有渲染时 `render.unavailable = true`、`status = "partial"`，不得伪造 PNG/PDF。

## 编辑纪律

1. 基线只读；每次写出新文件，最后再定名。
2. 只改文本 run 的内容，不改 run 的格式属性（字号、颜色、加粗）。
3. 替换图片必须同格式；换格式要新增关系并更新引用，属高风险操作，优先转成同格式。
4. 备注与正文分开改；不要把讲稿塞进正文。
5. 不在母版/版式里做单页特殊处理。
6. 任何“顺手优化”（换配色、调字号、重排）都禁止——超出工单即失败。

## 版式、占位符与备注规则

- 先盘点再定位：占位符 `idx` 不可跨模板移植，按 shape id/name 与文本锚点定位，不单靠 `idx`。
- 视觉问题若改幻灯片无效，去查母版/版式覆盖；单页特殊处理不进母版。
- 幻灯片文本可以是文本框（生成器默认）或占位符（模板继承）；两种都可编辑，但不混用定位假设。
- 16:9 / 4:3 必须与 task.md 一致；比例错会导致所有坐标假设偏移。
- 每页都要有 notes；生成模式用 `--require-notes` 强制检查。
- 模板残留扫描：`Click to edit`、`Lorem ipsum`、`TODO/TBD`、`[insert`、`待补充`。

## 异常处理

| 情况 | 处理 |
|---|---|
| 起始稿损坏 | 记录问题，按工单重建最小 deck，`status=partial` |
| 工单 target 不存在 | 记 `unresolved`，不新建元素冒充 |
| 文本替换命中多页 | 用 `--slide` 限定，并在 report 说明 |
| 备注页不存在 | `set-notes` 会报错；改为重建该页或记 `unresolved` |
| 图片格式不同 | 先转成同格式，保留原尺寸与位置 |
| spec 图片缺失 | 生成器会跳过图片；补真实图或用文字要点并写 `uncertainty` |
| 页数超限 | 合并 method 细节页，保留结果与局限，记录合并映射 |
| office-renderer 不可用 | 用 `pptx_preview.py` + Chrome，标 `html-from-pptx`；都没有则标 `preview.unavailable` |
| 渲染不可用 | 交付 pptx + `check_deck.py`/`verify` 报告，不伪造预览 |

## 自检

- [ ] 每条工单/每个 spec 页面都有对应记录
- [ ] `before/after` 真实来自文件，不是凭记忆
- [ ] 未点名页面逐页比对未变化
- [ ] 母版/版式/主题/图片未被破坏
- [ ] `pptx_edit.py verify` 通过，无悬空关系（编辑模式）
- [ ] `check_deck.py` 0 error；warning 已处理或说明
- [ ] 每页有标题与 notes；内容页有 `page_evidence` claims
- [ ] 改动页/关键页有真实渲染核对
- [ ] `render.engine` 与实际一致；`unresolved` 为空或已说明

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 盘点 | 每页与文本 run 有 id | 补盘点再改 |
| G2 | 范围 | 仅工单点名项发生变化 | 回滚越界修改 |
| G3 | 保留 | 母版/版式/主题/图片未变 | 从基线恢复 |
| G4 | diff | 每页有 before/after 或 unchanged | 补 diff |
| G5 | 包 | zip 可解、XML 可解析、页数正确 | 修复包结构 |
| G6 | 内容 | 标题是结论句、notes 非空、证据可定位 | 改写并重跑 check_deck |
| G7 | 视觉 | 关键页不溢出/不重叠、图片不变形 | 压缩文字或拆页后重渲染 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 重建代替编辑 | 主题/母版被重置 | 从基线包重做 |
| 整段重设字体 | run 级格式丢失 | 只改目标 run |
| 越界改版式 | 未点名页布局变化 | 回滚该页 |
| 页数变化 | 增删了未要求的页 | 恢复原页序 |
| diff 缺失 | 只有“已修改” | 补逐页 diff |
| 备注为空 | `--require-notes` 报错 | 每页补讲稿 |
| 图片被静默跳过 | spec 有图、deck 无图 | 检查路径/格式，补真实图 |
| 文字溢出 | `check_deck` overflow warning | 压缩文字或拆页，先不改字号 |
| 包损坏 | PowerPoint 打不开 | 校验 XML 与关系文件 |
| 伪造预览 | 声称已渲染但无文件 | 标 `preview.unavailable` |

## 快速路径（预算内）

1. **0–2 分钟**：读 task.md/case.json；生成模式写 spec 骨架；编辑模式解包 pptx 建 inventory。
2. **2–7 分钟**：生成模式出第一版 deck 并 `check_deck --spec`；编辑模式执行第一批工单。
3. **7–12 分钟**：补齐 notes/证据/图片；逐页 diff；修复溢出与占位残留。
4. **最后 2 分钟**：回读 pptx，跑 `check_deck` + `validate_artifacts`，确认预览真实。

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`status`、`workflow_mode`、`artifacts`、`page_evidence`
- [ ] `deck.pptx` 可打开且页数正确
- [ ] 生成模式：spec 与 deck 一致，每页 notes，内容页有证据
- [ ] 编辑模式：仅工单点名项被修改，未点名页 unchanged
- [ ] 母版/版式/主题/图片保留（编辑模式）
- [ ] `check_deck.py` 0 error；`validate_result.py` 与 `validate_artifacts.py` 通过
- [ ] 真实预览存在；无渲染时 `preview.unavailable=true` 且 `status=partial`

## 最小示例

输入：6 页 spec + `materials/fig-selectivity.png` + `materials/paper.pdf`。
执行：
```bash
python3 {SKILL_DIR}/scripts/make_pptx.py --spec /app/work/deck-spec.json --out /app/output/deck.pptx
python3 {SKILL_DIR}/scripts/check_deck.py /app/output/deck.pptx --spec /app/work/deck-spec.json \
  --expect-slides 6 --expect-aspect 16:9 --require-notes --require-evidence \
  --result /app/output/result.json --json
python3 {SKILL_DIR}/scripts/pptx_preview.py /app/output/deck.pptx --out /app/work/preview/deck.html --json
```
输出：`deck.pptx` 6 页、6 页 notes、2 张图；`check_deck` 0 error；
`result.json` 的 `page_evidence` 6 条；`preview/slide-01.png`、`slide-03.png` 为真实截图。
