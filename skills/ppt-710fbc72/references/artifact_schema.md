# 接口与产物 schema

本文件是 `scripts/main.py`、`scripts/spec.py`、`scripts/deckkit.py`、
`scripts/verify.py`、`scripts/reports.py` 共同使用的字段说明。示例只展示
接口形状，不是正式案例答案。

## 1. workorder（`/input/artifacts/slide-workorder.json`）

```json
{
  "mode": "adapt",
  "case_id": "CASE-001",
  "target_template": "materials/target-template.pptx",
  "starting_deck": "materials/starting-deck.pptx",
  "slide_count": 10,
  "audience": "同领域研究生",
  "duration_min": 15,
  "language": "zh-CN",
  "edits": [
    {"edit_id": "E01", "slide": 3, "action": "replace",
     "from": "旧表述", "to": "新表述"}
  ],
  "preserve": ["master", "layouts", "theme", "untouched_slides"],
  "required_points": ["研究问题", "方法", "结果", "限制"],
  "output_contract": ["case_id", "status", "editable_source",
                      "export_artifact", "content_map",
                      "compliance_checks", "exceptions"]
}
```

- `mode`：`adapt|retarget|edit` 走起始稿重定向；`build|generate|new` 走
  `template+content`；缺失时由 `scripts/spec.py` 按是否存在起始稿推断。
- `edits[].action` 支持 `replace|set_title|set_body|set_notes|delete`。
  `replace` 先按 `<a:t>` 直接替换；跨 run 时在段落级合并后替换，文字不丢。
  未命中的编辑写入 `exceptions`，不会删除原内容。
- 相对路径按 input 目录、input 父目录、当前目录依次解析；再不行按 basename
  在 input 下递归查找。

## 2. deck spec（`/app/work/deck-spec.json`）

由模型撰写；`scripts/spec.py::validate_deck_spec` 校验并由 `main.py` 装配。

```json
{
  "title": "整册标题",
  "source": "materials/paper.pdf",
  "slides": [
    {
      "role": "content",
      "title": "页级主张（一句话）",
      "subtitle": "可选副标题",
      "bullets": ["要点 1", "要点 2"],
      "bullets_right": ["右栏要点"],
      "image": "materials/fig2a.png",
      "caption": "Fig. 2a …",
      "notes": "讲稿（写入 notes slide）",
      "source": "Fig. 2a; Table 1",
      "claims": ["可复核的数字/结论"],
      "layout": "可选：必须来自 template-audit 的真实 layout 名",
      "footer": "可选页脚",
      "title_size": 32,
      "body_size": 18
    }
  ]
}
```

| 字段 | 必填 | 规则 |
|---|---|---|
| `role` | 是 | `title|section|content|two-column|figure|quote|closing` |
| `title` | 是 | 页级主张；不得为空；标题字号必须大于正文 |
| `bullets` | content 建议 | 字符串或 `{text, level, bold}`；一行一个要点 |
| `image` | figure 需要 | 真实图片路径，相对 spec/input/work 目录解析；等比缩放 |
| `caption` | figure 建议 | 图注 ≥12 pt，含图号 |
| `notes` | 建议 | 讲稿/讲解顺序；模板无 notes master 时记录 limitation |
| `source` | 建议 | 论文图/表/章节/数据定位；缺失时回退到源材料路径 |
| `claims` | 可选 | 逐页可复核主张，写入 `claim_source.json` |
| `layout` | 可选 | 只有审计中真实存在的版式名才会被采用 |

`role=content` 且只有图片、没有 bullets 时，`scripts/spec.py` 会按 figure 处理。

## 3. template audit（`/app/work/template-audit.json`）

由 `scripts/deckkit.py::audit_template` 从真实模板读取：

```json
{
  "ok": true,
  "slide_size": {"cx": 12192000, "cy": 6858000, "type": "screen16x9"},
  "aspect_ratio": "16:9",
  "masters": ["ppt/slideMasters/slideMaster1.xml"],
  "masters_count": 1,
  "layouts": [
    {"part": "ppt/slideLayouts/slideLayout1.xml", "index": 1,
     "name": "Title 1", "type": "title",
     "placeholders": [{"idx": "0", "type": "ctrTitle",
                       "box": {"x": 0, "y": 0, "cx": 1, "cy": 1}}]}
  ],
  "theme_part": "ppt/theme/theme1.xml",
  "theme_fonts": ["Neue Haas Grotesk Text Pro"],
  "theme_colors": ["131313", "203430"],
  "notes_master": "ppt/notesMasters/notesMaster1.xml",
  "sample_slides": ["ppt/slides/slide1.xml"],
  "sample_slide_count": 14
}
```

版式选择只使用真实 `name` 与 `placeholders`；content 角色会排除
section/agenda/photo/quote/conclusion 等版式。`scripts/spec.py` 负责生成
`layout-map.json` 与 format spec。

## 4. format spec（`/app/work/ppt-format-spec.json`）

```json
{
  "mode": "build",
  "slide_size": {"cx": 12192000, "cy": 6858000},
  "aspect_ratio": "16:9",
  "language": "zh-CN",
  "roles": {
    "title": {"layout": "Title 1", "title_size": 32, "body_size": 18},
    "content": {"layout": "Title, Content 1 (Max)", "title_size": 32,
                "body_size": 18},
    "section": {"layout": "Section Header"},
    "figure": {"layout": "Picture with Caption"}
  },
  "typography": {"title_size": 32, "body_size": 18,
                 "caption_size": 12, "footer_size": 10},
  "fonts": {
    "theme_fonts": ["…"],
    "theme_colors": ["…"],
    "substitutions": [],
    "policy": "never_substitute_silently"
  },
  "constraints": {"slide_count": 10, "body_size_min": 18,
                  "title_size_min": 24},
  "layouts_available": ["Title 1", "Content 1"]
}
```

- `roles.*.layout` 必须来自审计；缺失时使用最接近的真实版式并记
  `layout_fallback`。
- `fonts.substitutions` 默认为空；字体缺失只写 `font_availability` /
  `FONT_NOT_INSTALLED`，不改 PPTX 内的字体声明。

## 5. result.json 官方字段

入口始终生成以下字段（`scripts/reports.py::build_result`）：

```json
{
  "case_id": "CASE-001",
  "status": "answered|partial|input_error|blocked",
  "task_status": "completed|partial|input_error",
  "editable_source": {
    "path": "presentation.pptx", "format": "pptx",
    "slide_count": 10, "editable": true
  },
  "export_artifact": {
    "path": "preview/presentation.pdf", "format": "pdf",
    "native": true, "png_count": 10, "bytes": 123456
  },
  "content_map": [
    {"slide": 1, "title": "…", "claim": "…",
     "source": "Fig. 2a", "locator": "Fig. 2a",
     "evidence": ["…"], "notes_present": true,
     "template_layout": "Title 1", "preserved": false}
  ],
  "compliance_checks": {
    "pptx_valid": true,
    "template_fidelity": "master+layouts+theme",
    "placeholder_used": true,
    "export_signature": "pdf",
    "slide_size_match": true,
    "master_and_layouts_inherited": true,
    "theme_inherited": true,
    "layouts_from_template": true,
    "fonts_within_template": true,
    "content_preserved": true,
    "numbers_preserved": true,
    "title_hierarchy_ok": true,
    "notes_present": true,
    "page_count_match": true,
    "visual_preview": true,
    "official_fields_present": true
  },
  "exceptions": [
    {"code": "NATIVE_RENDER_UNAVAILABLE", "severity": "limitation",
     "detail": "本机未找到 LibreOffice/soffice，未生成原生 PDF/PNG 预览"}
  ]
}
```

无原生渲染时：

```json
"export_artifact": {
  "path": null, "format": null, "native": false,
  "status": "unavailable",
  "limitation": "本机未找到 LibreOffice/soffice …"
},
"render": {"engine": "none", "native": false, "unavailable": true}
```

`status` 规则：

- `answered`：deck 可打开、模板四项全 true、原生 PDF 存在；
- `partial`：无原生渲染、模板降级、缺 deck spec、或有结构性校验错误；
- `input_error`：找不到/读不了目标模板；仍写 result 并退出 0。

`case.json` 中未与上述字段冲突的键（如 `output_contract`、
`source_materials`、`title`）会原样保留。

## 6. 记录文件（`/app/work/`，并镜像到 `<output>/work/`）

| 文件 | 内容 |
|---|---|
| `plan.json` | `{case_id, mode, slide_count, slides[{slide, role, layout, title, source, notes_present}], spec_path, missing_spec}` |
| `claim_source.json` | `{case_id, claims: content_map, sources[]}` |
| `change_log.json` | `{case_id, changes[{action, detail}]}`；模板替换、版式重定向、编辑应用 |
| `render_report.json` | `{engine, native, pdf, png[], unavailable, limitation, duration_seconds}` |
| `time.json` | `{started_at, finished_at, total_seconds, stages}` |
| `format-check.json` | `scripts/verify.py` 的完整校验结果 |
| `adapt-report.json` | `scripts/deckkit.py` 的 build/retarget 报告（fidelity、layout_map、edits） |
| `workorder.json` | 归一化后的工单（保留 `original`） |
| `template-audit.json` | 真实模板审计 |
| `layout-map.json` | `{slides[{slide, role, layout, layout_part, fallback}]}` |
| `ppt-format-spec.json` | 角色→版式、字号、字体策略 |

## 7. exceptions 常见 code

| code | 含义 | 处置 |
|---|---|---|
| `NO_TARGET_TEMPLATE` | 没有目标模板 | input_error，写 result 退出 0 |
| `TEMPLATE_UNUSABLE` | 模板无 master/theme 或 XML 损坏 | 拒绝套用 |
| `DECK_SPEC_MISSING` | build 模式没有模型 spec | 输出 partial 骨架 |
| `WORKORDER_MISSING` | 无正式 workorder，按文件推断 | info |
| `LAYOUT_FALLBACK`（写入 uncertainty） | 指定/理想版式不存在 | 用真实版式并记录 |
| `PLACEHOLDER_NOT_IN_LAYOUT` | slide 占位符在 layout 中无对应槽 | 重写 ph 或保留为自由形状 |
| `EDIT_NOT_APPLIED` | workorder 编辑原文未命中 | 不删内容，列出 edit_id/slide |
| `FONT_NOT_INSTALLED` | 主题字体本机缺失 | 保留声明，不替换 |
| `FONT_OUTSIDE_TEMPLATE` | slide 级显式字体不在主题 | 记录，不静默改 |
| `FONT_TOO_SMALL` | 显式字号低于下限 | 提示模型改 spec，不硬压字号 |
| `CONTENT_LOSS` / `NUMBER_MISSING` | 保留率 <0.95 / 数字丢失 | 阻断 completed |
| `IMAGE_STRETCHED` | 图片宽高比偏差 >3% | 等比缩放或换图 |
| `SHAPE_OUT_OF_BOUNDS` | 形状越出页面 | 调整 spec/版式 |
| `OVERFLOW_RISK` | 估算文字溢出 | 压缩文案或拆页 |
| `NATIVE_RENDER_UNAVAILABLE` | 无 office renderer | partial + limitation |

## 8. 调用关系

```text
scripts/main.py
  -> scripts/spec.py      发现输入 / workorder / layout map / format spec
  -> scripts/deckkit.py   audit_template / build_from_spec / retarget_deck / render_preview
  -> scripts/verify.py    run(...)
  -> scripts/reports.py   content_map / write_records / build_result
  -> scripts/selfcheck.py 合成模板与起始稿的本地冒烟测试
```

示例调用：

```bash
python3 scripts/main.py --input /input --work /app/work --output /app/output --stage plan
# 模型写 /app/work/deck-spec.json
python3 scripts/main.py --input /input --work /app/work --output /app/output
```

以上示例只描述接口，不是任何固定案例的答案或评分证据。
