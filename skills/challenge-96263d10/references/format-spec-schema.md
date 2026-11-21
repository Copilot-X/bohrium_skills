# format-spec.json 与 result.json 字段约定

本文件是 `scripts/adapter.py` 与 `scripts/main.py` 的交接契约。所有值都必须
能追溯到模板 OOXML 部件或格式指南行；推断值必须标注来源等级。

## 1. format-spec.json

```json
{
  "meta": {
    "generated_by": "adapter.py",
    "created_at": "2026-01-01T00:00:00Z",
    "template": "template.docx",
    "template_sha256": "…",
    "guide": "style guide file name or null",
    "source": "template.docx + guide",
    "evidence": [
      {"field": "page.margins", "value": {"top": 2.2},
       "source": "template.docx", "locator": "word/document.xml sectPr/pgMar top=3168"}
    ],
    "guide_evidence": [
      {"field": "fonts.body_ascii", "value": "Times New Roman",
       "source": "guide", "locator": "line 8", "text": "Body font: …"}
    ],
    "conflicts": [
      {"field": "references.style", "template": "numeric-bracket",
       "guide": "APA", "resolution": "guide value applied"}
    ],
    "unrecognized": [{"line": 14, "text": "…"}],
    "font_substitutions": [
      {"requested": "Times New Roman", "used": "Liberation Serif",
       "scope": "styles.body.font", "reason": "font not installed"}
    ]
  },
  "page": {
    "size": "A4",
    "width_twips": 11906,
    "height_twips": 16838,
    "width_cm": 21.0,
    "height_cm": 29.7,
    "orientation": "portrait",
    "margins_cm": {"top": 2.2, "right": 2.3, "bottom": 2.0,
                   "left": 2.5, "header": 1.25, "footer": 1.25, "gutter": 0},
    "columns": {"count": 1, "space_cm": 0.75},
    "section_count": 1
  },
  "fonts": {
    "body_ascii": "Liberation Serif",
    "body_east_asia": "Liberation Serif",
    "heading_ascii": "Liberation Serif",
    "body_size_pt": 10.5,
    "body_line_spacing": 1.15,
    "caption_size_pt": 9.0,
    "theme_major_latin": "…",
    "theme_minor_latin": "…",
    "theme_major_ea": "…",
    "theme_minor_ea": "…"
  },
  "styles": {
    "body": {
      "style_id": "Normal", "style_name": "Normal",
      "font": "Liberation Serif", "font_east_asia": "Liberation Serif",
      "size_pt": 10.5, "bold": null, "italic": null,
      "align": "both", "space_before_pt": 0, "space_after_pt": 0,
      "line_spacing": 1.15, "line_spacing_pt": null, "line_rule": "multiple",
      "first_line_indent_cm": 0.74, "left_indent_cm": null,
      "right_indent_cm": null, "hanging_indent_cm": null,
      "keep_next": null, "outline_level": null,
      "numbering": {"num_id": null, "level": null}
    },
    "title": { "style_id": "Title", "size_pt": 18.0, "bold": true, "align": "center" },
    "heading_1": { "style_id": "Heading1", "size_pt": 16.0, "bold": true,
                   "outline_level": 1, "keep_next": true },
    "heading_2": { "style_id": "Heading2", "size_pt": 14.0, "outline_level": 2 },
    "heading_3": { "style_id": "Heading3", "size_pt": 12.0, "outline_level": 3 },
    "caption_figure": { "style_id": "Caption", "size_pt": 9.0, "align": "left" },
    "caption_table": { "style_id": "TableCaption", "size_pt": 9.0 },
    "reference": { "style_id": "Bibliography", "size_pt": 9.0,
                   "hanging_indent_cm": 0.75, "line_spacing": 1.0 },
    "abstract": { "style_id": "Abstract", "size_pt": 9.5 },
    "keywords": { "style_id": "Keywords", "size_pt": 9.5 }
  },
  "captions": {
    "figure_position": "below",
    "table_position": "above",
    "labels": {"figure": "Figure", "table": "Table"},
    "separator": ". "
  },
  "references": {"style": "numeric-bracket", "hanging_indent_cm": 0.75,
                 "size_pt": 9.0},
  "table": {"style": "three-line"},
  "numbering": {"mode": "decimal", "samples": ["1 Introduction"],
                "numbering_xml": true},
  "header_footer": {"headers": 1, "footers": 1, "title_page": false},
  "requirements_flags": {"abstract": true, "keywords": true}
}
```

字段说明：

- `style_id` 必须与模板 `word/styles.xml` 中实际存在的 `styleId` 一致；
  缺失角色由正文派生时，`meta.evidence` 中该字段标 `synthetic: true`。
- 字体字段区分西文 `font` 与东亚 `font_east_asia`；值来自 `rFonts` 的
  `ascii/hAnsi` 与 `eastAsia`，`asciiTheme`/`eastAsiaTheme` 先解析为主题字体。
- 尺寸统一使用 `cm`（页面）与 `pt`（字号）；OOXML 原始值（twips/半磅）
  保留在 `page.width_twips`、`page.height_twips` 与 `meta.evidence` 中。
- 行距两种表达：`line_spacing` 是倍数（`lineRule=auto`），
  `line_spacing_pt` 是固定值（`lineRule=exact/atLeast`）。
- `references.style` 取值：`numeric-bracket`、`numeric`、`author-year`、
  `gb-t-7714`、`APA`、`MLA`、`IEEE`、`Vancouver`、`Chicago`、`Harvard`、
  `AMA`、`template`、`unknown`。
- `captions.labels` 记录模板示例中的标签语言；中文模板可能是 `{"figure":"图",
  "table":"表"}`。
- 未解析的指南行放 `meta.unrecognized`，不得静默丢弃。

## 2. result.json（官方 writing 字段）

```json
{
  "case_id": "…",
  "task_family": "format-adaptation",
  "status": "completed | partial | input_error",
  "deliverable": {
    "path": "manuscript.docx", "format": "docx", "role": "editable",
    "bytes": 4464, "sha256": "…",
    "readback": {"paragraphs": 16, "page": "A4", "margins_cm": {}}
  },
  "preserved_facts": [
    {"fact_id": "PF001", "kind": "number", "value": "84.3",
     "source": {"path": "source manuscript", "locator": "paragraph 12"},
     "output": {"path": "manuscript.docx", "locator": "paragraph 15"},
     "status": "preserved | unverified"}
  ],
  "evidence_map": [
    {"requirement_id": "R01", "area": "page", "method": "auto",
     "artifact": "manuscript.docx", "locator": "word/document.xml + word/styles.xml",
     "source": "template.docx: word/document.xml sectPr/pgSz …",
     "check_code": "PAGE-SIZE", "evidence": "pgSz=…", "status": "pass"}
  ],
  "change_log": [
    {"section": "document", "type": "page",
     "before": "source/template package", "after": "page=A4 …",
     "reason": "target template sectPr", "artifact": "manuscript.docx"}
  ],
  "requirements": [
    {"id": "R01", "area": "page", "field": "page.size",
     "expected": "A4 (11906x16838 twips)", "check_code": "PAGE-SIZE",
     "method": "auto", "source": "template.docx: …", "note": null,
     "status": "pass", "actual": {"name": "A4"},
     "evidence": "word/document.xml pgSz=…", "artifact": "manuscript.docx"}
  ],
  "summary": {"total": 23, "pass": 18, "fixed": 0, "missing": 0,
              "not_applicable": 5, "manual_check": 0},
  "fidelity": {
    "similarity": 1.0, "ordered_similarity": 1.0,
    "source_units": 587, "output_units": 587,
    "units": {"sections": {"source": 6, "output": 6, "status": "preserved"}},
    "preserved": true
  },
  "unmet": [{"id": "U-TEMPLATE", "requirement": "template.docx",
             "reason": "…", "impact": "…"}],
  "uncertainty": [{"kind": "manual_checks", "detail": "…", "impact": "…"}],
  "source_inventory": {"files": [], "manuscript": "…", "template": "…",
                       "guide": "…", "counts": {}},
  "docx_check": {"ok": true, "paragraphs": 16, "page": {}, "margins_cm": {},
                 "columns": {}, "captions": [], "references": {}},
  "format_spec": {"path": "format-spec.json", "source": "…",
                  "template": "…", "conflicts": [], "unrecognized": []},
  "output_contract": {"…": "…"},
  "official_contract": {"profile": "writing",
                        "fields_present": ["deliverable", "preserved_facts",
                                           "evidence_map", "change_log"]}
}
```

约定：

- `status=completed` 需要：源稿存在、模板存在、DOCX 回读成功、
  `summary.missing=0`、正文词元保真 ≥ 0.98。任一不满足为 `partial`；
  没有可用源稿为 `input_error`。
- `preserved_facts.output.locator` 只在产物中真实找到该值时填写；找不到写
  `null` 且 `status=unverified`。
- `requirements` 的 `status` 取值：`pass`、`fixed`、`missing`、
  `not_applicable`、`manual_check`。
- `manual_check` 不阻塞 `completed`，但必须出现在 `uncertainty` 中。
- `output_contract` 声明但未产出的字段写 `null`，并追加
  `uncertainty.kind=contract_field_unavailable`。
- 所有 `path` 必须真实存在；不存在的路径不得写入 `deliverable`/`artifacts`。
