# 学术 PPT 生成与编辑 Playbook

适用：从论文/材料生成可编辑学术 deck；在给定起始稿上做最小干预编辑；
生成或修改后做结构、版式、占位符、备注与视觉检查，并给出真实预览。

一句话原则：**一页一个结论，每个结论有出处；生成可编辑，编辑不越界，预览不伪造。**

---

## 1. 先判工作流

| 输入信号 | 工作流 | 主命令 | 典型失败 |
|---|---|---|---|
| task.md 要求“做一份 PPT / 把材料变成 slides” | `generate` | `make_pptx.py --spec` | 标题是话题词、备注为空、图被拉伸 |
| task.md 给出起始稿并要求“修改/补充/调整” | `edit` | `pptx_edit.py` | 重建代替编辑、越界改页、丢母版 |
| 给出模板/母版，要求在模板上生成 | `adapt` | `pptx_edit.py apply-template` | 占位符索引假设错误、主题被覆盖 |
| 只要求提取文字/盘点 | `inspect` | `pptx_edit.py inspect/list` | 把盘点结果当成交付物 |

判定顺序：先读 `/input/task.md` 与 `/input/case.json`，看是否有 `starting_deck` / `workorder`。
两者同时存在时，默认是 `edit`；只有明确允许重建才走 `generate`。
`result.json` 的 `workflow_mode` 必须与真实行为一致。

---

## 2. 输入盘点（生成前也要做）

1. `/input/task.md`：受众、时长、页数上限/固定页数、语言、必须覆盖内容、讲稿要求、比例（16:9 / 4:3）。
2. `/input/case.json`：`case_id`、`starting_deck`、`template_id`、`workorder`、`source_materials[]`。
3. 源材料：论文全文/图表、数据表、已有 deck。**只读实际文件，不从文件名猜内容。**
4. 模板：母版/版式清单、占位符类型、主题字体与配色、页脚/页码规则。
5. 预算：15 分钟约 12–15 页；固定页数时先分配结果页，再压缩方法细节。

起始稿盘点命令：

```bash
python3 {SKILL_DIR}/scripts/pptx_edit.py inspect /app/work/baseline/deck.pptx --json
python3 {SKILL_DIR}/scripts/pptx_edit.py list    /app/work/baseline/deck.pptx --json
```

记录：页数、每页 `texts[]`（index + 文本）、notes、images、layout、主题字体/配色。
**文件名顺序不等于页序，页序以 `sldIdLst` 为准。**

---

## 3. 叙事结构：一页一结论

### 3.1 页面角色

| role / kind | 作用 | 页面内容上限 | 讲稿 |
|---|---|---|---|
| `title` | 题目、汇报人、场合 | 标题 ≤ 2 行 | 20–30 秒开场 |
| `section` | 章节转折 | 一行章节名 | 15–30 秒过渡 |
| `motivation` | 问题与缺口 | 3–4 条要点 或 1 张图 | 40–60 秒 |
| `method` | 方法如何支撑结论 | 3–5 条，不放结果 | 40–60 秒 |
| `result` | 关键结果（结论式标题） | 1 张主图 + 2–3 条读数 | 45–75 秒 |
| `discussion` | 机制/解释 | 1 张图 或 3 条推理 | 45–60 秒 |
| `limitation` | 局限与替代解释 | 2–3 条 | 30–45 秒 |
| `conclusion` | 结论与下一步 | 3 条以内 | 30–45 秒 |

### 3.2 标题写法

- 标题写**结论句**：“A 在 350 °C 保持 92% 选择性”，不写“结果”“性能测试”。
- 一页只放一个主张；两个并列主张拆两页，不要挤成两栏。
- 数字、单位、条件与源材料逐字一致；为排版改数值属不可接受错误。
- 标题与讲稿分工：标题给结论，讲稿给“为什么重要 + 怎么得到 + 过渡”。

### 3.3 页数与时间预算

- 15 分钟 ≈ 12–15 页，其中结果页 ≥ 1/3，方法页 ≤ 1/4。
- 固定页数时先保 `result` / `limitation` / `conclusion`，压缩 `method` 背景细节。
- 页数不足时拆页不删内容；页数超限时合并同角色页并记录合并映射。

---

## 4. deck-spec：生成用的结构化输入

`templates/deck-spec.json` 给出可复制骨架。字段：

```json
{
  "title": "deck 标题",
  "size": "16:9",
  "language": "zh",
  "audience": "领域同行（15 分钟组会）",
  "duration_min": 15,
  "target_slides": 6,
  "throughline": "一句话主线",
  "slides": [
    {"kind": "title", "title": "…", "subtitle": "…", "notes": "…"},
    {"kind": "section", "title": "…", "notes": "…"},
    {"kind": "content", "title": "结论句",
     "bullets": ["读数 1", "读数 2"],
     "image": "materials/fig2a.png",
     "caption": "Fig. 2a：…（源：materials/paper.pdf, Fig. 2a）",
     "footer": "Author et al., Journal 2025, Fig. 2a",
     "notes": "讲稿 45 秒",
     "evidence": [
       {"claim": "…", "source": {"path": "materials/paper.pdf", "locator": "Fig. 2a"}}
     ]}
  ]
}
```

规则：

- `kind` 用 `title / section / content / title-only`；`content` 有 `image` 时左文右图，无图时通栏。
- `bullets` 每条一行以内；建议 3–5 条，单条 ≤ 40 个汉字/≈12 个英文词。
- `image` 必须是真实存在的 PNG/JPEG；文件缺失时生成器会静默跳过图片，必须用 `check_deck.py --spec` 抓出来。
- `image` 路径按**运行进程的当前目录**解析，不是相对 spec 文件。生成前先 `cd /app/work`，
  或在 spec 里写绝对路径；否则图片会被静默跳过。
- `notes` 每页都写；不要复述页面文字，写“为什么这页重要 + 怎么过渡”。
- `evidence` 只有内容页需要；`source.path` + `source.locator` 必须能回到源材料。
- 额外字段 `audience / duration_min / throughline` 不参与排版，用于自检与 `result.json` 映射。

---

## 5. 生成流程

```bash
# 1) 写 deck-spec（从模板复制后替换全部示例值）
cp {SKILL_DIR}/templates/deck-spec.json /app/work/deck-spec.json

# 2) 生成可编辑 pptx（母版 + 版式 + 备注页）
python3 {SKILL_DIR}/scripts/make_pptx.py \
  --spec /app/work/deck-spec.json --out /app/output/deck.pptx

# 3) 回读校验：结构 + 内容 + 备注 + 图片 + spec 一致性 + 证据
python3 {SKILL_DIR}/scripts/check_deck.py /app/output/deck.pptx \
  --spec /app/work/deck-spec.json \
  --expect-slides 6 --expect-aspect 16:9 \
  --require-notes --require-evidence \
  --json --out /app/work/check-deck.json

# 4) 需要预览时的浏览器几何预览（office-renderer 不可用时）
python3 {SKILL_DIR}/scripts/pptx_preview.py /app/output/deck.pptx \
  --out /app/work/preview/deck.html --width 1200 --json
```

生成后必须回读，不能只看“wrote ... bytes”。`make_pptx.py --check` 只做最底层包检查，
`check_deck.py` 才检查标题、备注、图片引用、越界、溢出与 spec 一致性。

---

## 6. 编辑流程：最小干预

1. **备份基线**：复制到 `/app/work/baseline/`，全程只读；所有写操作输出新文件。
2. **盘点**：`inspect` + `list`，拿到每页 run 级文本。
3. **解析工单**：每条 `edit_id / slide / target / action / from / to / source / reason`。
   工单有编号就沿用；没有则自建并写依据。
4. **逐条执行**：

```bash
# 精确替换第 6 页第 3 个 run
python3 {SKILL_DIR}/scripts/pptx_edit.py set-text deck.pptx \
  --slide 6 --index 3 --text "A 在 350 °C 保持 92% 选择性" --out deck_v2.pptx --json

# 单页字符串替换（命中多处时必须限定 slide）
python3 {SKILL_DIR}/scripts/pptx_edit.py replace deck.pptx \
  --from "结果" --to "A 在 350 °C 保持 92% 选择性" --slide 6 --out deck_v2.pptx --json

# 改写讲稿
python3 {SKILL_DIR}/scripts/pptx_edit.py set-notes deck_v2.pptx \
  --slide 6 --text "…" --out deck_v2.pptx --json

# 替换图片（保持原关系、位置、尺寸）
python3 {SKILL_DIR}/scripts/pptx_edit.py set-image deck_v2.pptx \
  --part ppt/media/image3.png --src materials/fig2a.png --out deck_v2.pptx --json
```

5. **校验**：

```bash
python3 {SKILL_DIR}/scripts/pptx_edit.py verify /app/output/deck.pptx --json
python3 {SKILL_DIR}/scripts/check_deck.py /app/output/deck.pptx \
  --expect-slides 12 --require-notes --json --out /app/work/check-deck.json
```

编辑纪律：

- 只改工单点名项；未点名的页、母版、版式、主题、图片、备注原样保留。
- 只改目标 `<a:t>` 的文本，不改 `rPr` 的字号/颜色/加粗。
- 替换图片必须同格式；保持原位置与裁剪，不重编码未涉及的媒体。
- 文本变长先压缩文字，不缩字号、不挪框来“塞进去”。
- 任何“顺手优化”（换配色、调字号、重排）都算越界。

---

## 7. 版式、占位符与母版

- 先盘点再定位：占位符 `idx` 不可跨模板移植，优先按 shape id/name 与文本锚点定位。
- 版式真相在 `ppt/slideLayouts/`；母版真相在 `ppt/slideMasters/`。单页看起来“改不动”时先查母版/版式覆盖。
- 不要为单页特殊处理去改母版/版式。
- 幻灯片上的文本可以是文本框（生成器默认）或占位符（模板继承）。两种都可编辑，但不要混用同一套定位假设。
- 比例（16:9 / 4:3）必须与 task.md 一致；不一致时所有坐标假设都会偏。
- 检查命令会报告 layout/master 数量、占位符使用数与“占位符在版式中找不到”的警告。

---

## 8. 图表与图片

- 一页一图；图必须对准该页结论，不为装饰放图。
- 用原始图，不二次渲染走样；必须裁切时保留轴标签、图例、单位。
- 图片按原宽高比等比缩放，不拉伸变形；生成器会按比例缩放到右侧区域。
- 图注写“图号 + 读数 + 源 locator”，例如 `Fig. 2a：350 °C 处 92%（源：paper.pdf, Fig. 2a）`。
- 图片引用必须能落到 `ppt/media/*`；`check_deck.py` 会检查每条 `a:blip` 关系目标。
- 缺图时宁可改成文字要点并在 `uncertainty` 说明，不要放占位图冒充。

---

## 9. 讲稿（speaker notes）

- 每页都有 notes；长度约 30–75 秒（中文 80–200 字）。
- 结构：一句结论回扣 + 一个机制/证据 + 一句过渡。
- 不把 notes 塞进正文；正文是给观众读的，notes 是给讲者说的。
- 编辑工单只点名 notes 时，正文一个字都不动。
- `check_deck.py --require-notes` 会逐页检查 notes 非空。

---

## 10. 页级证据映射

`result.json` 的 `page_evidence` 至少覆盖每一页：

```json
{"slide": 3, "kind": "result", "title": "A 在 350 °C 保持 92% 选择性",
 "claims": [
   {"claim": "A 在 350 °C 的选择性为 92%",
    "source": {"path": "materials/paper.pdf", "locator": "Fig. 2a"}}
 ]}
```

规则：

- `title / section / closing` 等装饰页可以 `claims: []`，但要保留条目与 `kind`。
- 内容页每个 claim 必须有 `source.path` + `source.locator`；能在源材料中找到。
- 推断与原文分开：无法逐字定位的写 `"status": "inferred"` 并说明依据。
- 没有证据的页不能靠“版面好看”过关；`check_deck.py --require-evidence` 会检查。

---

## 11. 质量闸门：内容 QA 与视觉 QA 分开

### 11.1 内容 QA

- [ ] 每页标题是结论句，能指回 claim
- [ ] 数值/单位/条件与源材料一致，未为排版改动
- [ ] 页数、时长、受众、语言符合 task.md
- [ ] 模板残留扫描：`Click to edit`、`Lorem ipsum`、`TODO/TBD`、`[insert`、`待补充`
- [ ] 未点名页面逐页 diff 为 `unchanged`

### 11.2 视觉 QA

- [ ] 形状不出画布；文字不溢出、不压住下一条
- [ ] 图片不变形、不裁掉轴标签/图例
- [ ] 标题与正文层级明显；正文字号 ≥ 16 pt
- [ ] 页脚/页码不碰内容；比例正确
- [ ] 至少做一轮“修复 → 重渲染 → 复查”，改间距后复查相邻页

`check_deck.py` 给出的是**风险清单**（估算溢出、重叠、越界、占位残留），
不是像素级判决；最终以真实渲染为准。`--strict` 会让 warning 也导致非零退出。

---

## 12. 渲染与真实预览

优先顺序：

1. **office-renderer**（评测镜像提供）：

```bash
office-renderer --input /app/output/deck.pptx --out /app/output/preview/deck.pdf
pdf-render-tool --input /app/output/preview/deck.pdf --out /app/output/preview/ --dpi 150
```

2. **office-renderer 不可用**：用 `pptx_preview.py` 从真实 PPTX 的 OOXML 生成 HTML 几何预览，
   再用本机 Chrome 截图：

```bash
python3 {SKILL_DIR}/scripts/pptx_preview.py /app/output/deck.pptx \
  --out /app/work/preview/deck.html --width 1200 --json
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu \
  --hide-scrollbars --window-size=1200,900 --virtual-time-budget=3000 \
  --screenshot=/app/output/preview/slide-01.png \
  "file:///app/work/preview/deck.html"
```

   HTML 预览是**几何/文本近似**，字体形状与 PowerPoint 不完全一致；必须在
   `render.engine` 写 `html-from-pptx`，不得声称是 office-renderer 输出。

3. 两者都不可用：交付 pptx + 结构校验报告，`status: "partial"`，`preview.unavailable: true`。
   **绝不伪造预览图。**

---

## 13. 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 标题是话题词 | “结果”“方法”当标题 | 改成结论句，带数值/方向 |
| 正文当讲稿 | 页面塞满整段文字 | 正文压到 3–5 条，细节移入 notes |
| 备注为空 | `check_deck.py --require-notes` 报错 | 每页补 80–200 字讲稿 |
| 图片被静默跳过 | spec 有 image，deck 无图 | 检查文件路径/格式，补真实图 |
| 图片拉伸 | 宽高比与原图不符 | 等比缩放，保留留白 |
| 文字溢出 | 估算 ratio > 1 或渲染压字 | 压缩文字或拆页，先不改字号 |
| 占位符残留 | `Click to edit title`/`TODO` | 替换为真实内容 |
| 编辑变成重建 | 主题/母版被重置 | 从基线恢复，走 `pptx_edit.py` |
| 越界编辑 | 未点名页变化 | 回滚该页，只改工单项 |
| 文件打不开 | zip/关系损坏 | `pptx_edit.py verify` + 修复关系 |
| 伪造预览 | 声称已渲染但无文件 | 写 `preview.unavailable=true` |

---

## 14. 交付清单

- [ ] `/app/output/deck.pptx` 可解包、可打开、页数与要求一致
- [ ] 每页有标题与 notes；内容页有证据映射
- [ ] 图片引用全部落地，未变形
- [ ] `check_deck.py` 0 error；warning 已逐条处理或说明
- [ ] `pptx_edit.py verify` 通过（编辑模式）
- [ ] `validate_artifacts.py --result result.json` 通过
- [ ] `result.json` 含 `case_id`、`status`、`workflow_mode`、`artifacts`、`page_evidence`
- [ ] 真实预览存在；无渲染时如实标注
- [ ] `unresolved` / `uncertainty` 为空或已说明

---

## 15. 最小示例

输入：6 页 spec + `materials/fig-selectivity.png` + `materials/paper.pdf`。
执行：`make_pptx.py --spec` → `check_deck.py --spec --require-notes --require-evidence`
→ `pptx_preview.py` → Chrome 截图。
输出：`deck.pptx` 6 页、6 页 notes、2 张图、`check_deck` 0 error；
`result.json` 的 `page_evidence` 6 条；`preview/slide-01.png`、`slide-03.png` 为真实截图。
