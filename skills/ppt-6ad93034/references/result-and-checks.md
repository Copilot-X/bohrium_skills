# result.json 契约与记录文件

入口 `scripts/main.py` 负责生成 `result.json`。规则：
**保留输入 case.json 的原有根字段，再由入口写入下列字段；不得删除任务字段。**

## 1. 根字段

```json
{
  "case_id": "与输入一致；缺失时用 unknown",
  "status": "answered | partial | input_error",
  "task_status": "completed | partial | input_error",
  "status_reason": "一句话说明为什么是这个状态",
  "workflow_mode": "generate | edit | adapt",
  "task_family": "deck-generate | deck-edit | deck-adapt",
  "input_summary": {"task_md": "...", "case_json": "...", "materials": 3,
                    "sources": 2, "figures": 4, "tables": 1, "evidence_items": 120},
  "deck": {
    "path": "presentation.pptx", "format": "pptx", "slide_count": 12,
    "aspect_ratio": "16:9", "starting_deck": null,
    "slides_added": 0, "slides_removed": 0, "slides_reordered": false,
    "template_fidelity": "master+layouts+theme",
    "layout_map": [{"slide": 1, "layout": "Title Slide"}],
    "speaker_notes_slides": 12
  },
  "artifacts": [
    {"role": "editable", "path": "presentation.pptx", "format": "pptx"},
    {"role": "render", "path": "preview/presentation.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/slide-01.png", "format": "png"}
  ],
  "workorder": {"path": "...", "mode": "edit", "slide_count": 12,
                "starting_deck": "...", "target_template": "...", "edit_items": 2},
  "plan": {"path": "plan.json", "slides": 12, "content_origin": "model-authored"},
  "claim_source": {"path": "claim_source.json", "slides": 12},
  "timing": {"path": "time.json", "total_seconds": 38.2, "stages": {}},
  "change_records": {"path": "change_log.json", "changes": 5},
  "render_report": {"path": "render_report.json", "native": false,
                    "engine": "matplotlib-spec"},
  "page_evidence": [
    {"slide": 6, "kind": "result", "title": "...",
     "claims": [{"claim": "...", "source": {"path": "materials/paper.pdf",
                                            "locator": "p.5"}}],
     "source": {"path": "materials/paper.pdf", "locator": "p.5"},
     "figure": {"path": "materials/paper.pdf", "locator": "p.5"},
     "notes": true}
  ],
  "edits": [
    {"edit_id": "E01", "slide": 6, "action": "replace", "target": "text#3",
     "before": "...", "after": "...", "source": {"path": "...", "locator": "..."},
     "verified": true}
  ],
  "unresolved_edits": [],
  "preserved": {"master": true, "layouts": 11, "theme_fonts": ["Calibri"],
                "images": 4, "untouched_slides": [1, 2, 3],
                "content_preserved_from_base": false},
  "render": {"engine": "libreoffice-soffice | matplotlib-spec | none",
             "native": true, "pdf": "preview/presentation.pdf",
             "png": ["preview/slide-01.png"], "unavailable": false,
             "pdf_pages": 12, "limitations": []},
  "checks": {"zip_valid": true, "relationships_resolved": true,
             "slide_count_match": true, "notes_complete": true,
             "images_resolved": true, "text_overflow_risk": [],
             "placeholder_junk": [], "spec_conformance": true,
             "numbers_verified": true, "evidence_coverage": 1.0,
             "visual_reviewed": true, "render_available": true,
             "template_fidelity": "none", "edit_scope_ok": true},
  "editable_source": {"path": "presentation.pptx", "format": "pptx",
                      "sha256": "...", "bytes": 48213, "slide_count": 12,
                      "editable": true},
  "export_artifact": {"path": "preview/presentation.pdf", "format": "pdf",
                      "pages": 12, "engine": "libreoffice-soffice",
                      "native": true},
  "content_map": [
    {"slide": 6, "role": "result", "claim": "...",
     "maps_to": {"path": "materials/paper.pdf", "locator": "p.5"}}
  ],
  "compliance_checks": {"pptx_valid": true, "slide_count_match": true,
                        "notes_complete": true, "export_signature": "pdf",
                        "template_fidelity": "none", "numbers_verified": true,
                        "evidence_coverage": 1.0, "render_available": true},
  "exceptions": [{"code": "render_limitation", "severity": "limitation",
                  "detail": "..."}],
  "uncertainty": [],
  "limitations": [],
  "sources": [{"path": "materials/paper.pdf", "kind": "pdf",
               "chars": 18231, "sha256": "..."}],
  "template": {"path": "...", "fidelity": "master+layouts+theme",
               "layouts_used": [], "fallback_reason": null},
  "spec": {"path": "deck-spec.json", "origin": "model-authored",
           "issues": [], "warnings": []},
  "notes": {"budget_seconds": 900, "native_renderer": "matplotlib-spec",
            "preview_is_native": false, "evidence_pack": "../work/evidence-pack.json"}
}
```

## 2. 字段语义

- `status=answered` 只表示交付物与检查通过，不代表科学内容被评审认可。
- `editable_source` 必须指向真实 PPTX；文字是普通文本 run，可在 PowerPoint 中编辑。
- `export_artifact` 必须是真实 PDF；`native=false` 表示由同一 spec 绘制的预览，
  不是 PPTX 原生转换，原因写进 `limitations`。
- `content_map` 与 `page_evidence` 必须一致；每张图要有 `figure_source` 定位。
- `preserved` 在 edit/adapt 下必须写未点名页、母版/版式/主题字体、图片数。
- `edits` 只记录真正命中的项；未命中进 `unresolved_edits`，不得伪造成 `verified`。
- 输入 case.json 的其他字段原样保留；`output_contract` 也保留。

## 3. 记录文件

| 文件 | 内容 |
|---|---|
| `plan.json` | 有效 spec、页面大纲、约束、spec issues/warnings |
| `claim_source.json` | 每页 claim 与 path/locator、figure_source |
| `time.json` | 各阶段耗时、总耗时、预算 |
| `change_log.json` | discover/plan/build/edit/export/checks 的动作与时间 |
| `render_report.json` | 渲染引擎、native、PDF/PNG 路径与页数、限制 |
| `checks.json` | 完整检查结果与 inspected 元数据 |
| `gold.json` | 页级金标准合同 |
| `page-diff.json` | 逐页 diff 与 pass_rate |
| `deck-spec.json` | 最终生效的 spec（运行时产物，不是打包资源） |
| `../work/evidence-pack.json` | 证据索引，供模型写 spec 使用 |

## 4. 异常处理与退出码

- `input_error`：无 case/task/材料/编辑指令且无自带 spec；写完整 result，exit 0。
- `partial`：PPTX 或预览缺失、结构错误、数字未回查、编辑未解决；写完整 result，exit 0。
- 未捕获异常：`main.py` 兜底写 `internal_error` 的 partial result，exit 0。
- 只有输出目录无法创建/写入时 exit 2。
- 不得为“找不到某个可选文件”直接 exit 2。

## 5. 最小示例（接口示例，不是正式答案）

```json
{
  "case_id": "SYN-SLIDES",
  "status": "answered",
  "editable_source": {"path": "presentation.pptx", "format": "pptx"},
  "export_artifact": {"path": "preview/presentation.pdf", "format": "pdf"},
  "content_map": [{"slide": 2, "claim": "示例 claim",
                   "maps_to": {"path": "materials/example.txt", "locator": "line 4"}}],
  "compliance_checks": {"pptx_valid": true, "slide_count_match": true,
                        "notes_complete": true, "export_signature": "pdf",
                        "template_fidelity": "none", "render_available": true},
  "exceptions": []
}
```
