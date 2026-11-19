# 一次完整调用示例（synthetic，非正式答案）

本示例只说明接口与产物形态，不冒充官方案例结果，也不包含任何正式答案。
示例输入是合成稿件与合成模板，数值和文字均为演示用。

## 1. 输入目录

```text
/input/
  template.docx          # 合成模板：A4，上下 2.2/2.0 cm，左右 2.5/2.3 cm，双栏
  paper.docx             # 合成稿件：标题、摘要、两节正文、一个表、参考文献
  case JSON              # case_id=demo-format-001，含 output_contract
  guide text             # 可选：合成指南，要求 Times New Roman、1.5 倍行距、APA
```

模板 `template.docx` 的 `word/styles.xml` 中实际存在 Normal、Title、
Heading 1–3、Caption、Table Caption、Bibliography；`word/document.xml`
的 `sectPr` 给出页面与页边距；`word/theme/theme1.xml` 给出主题字体。
这些值会被 `adapter.py` 逐项读入，而不是从文件名猜测。

## 2. 调用

```bash
python3 scripts/main.py \
  --input /input --output /app/output --work /app/work --json
```

若模型先改写了正文，可把改写稿放到 `/app/work/adapted.md`：

```bash
python3 scripts/main.py \
  --input /input --output /app/output --work /app/work \
  --body /app/work/adapted.md --json
```

不传 `--body` 时，`paper.docx` 的正文在副本上逐字保留，只重写样式、页面、
题注位置与参考文献段落格式。脚本不会用抽取式句子拼一份“保底正文”。

## 3. 示意输出（本地合成数据，不是官方成绩）

```json
{
  "status": "completed",
  "case_id": "demo-format-001",
  "deliverable": {
    "path": "manuscript.docx",
    "format": "docx",
    "role": "editable",
    "bytes": 4464,
    "readback": {
      "paragraphs": 16,
      "page": "A4",
      "margins_cm": {"top": 2.2, "right": 2.3, "bottom": 2.0,
                     "left": 2.5, "header": 1.25, "footer": 1.25}
    }
  },
  "summary": {"total": 23, "pass": 18, "fixed": 0, "missing": 0,
              "not_applicable": 5, "manual_check": 0},
  "fidelity": {"similarity": 1.0, "ordered_similarity": 0.98},
  "body_mode": "source-preserved",
  "unmet": []
}
```

`/app/output/` 下会同时出现：

- `manuscript.docx`：可编辑主稿；页面、标题、题注、参考文献样式已套用；
- `format-spec.json`：逐字段规格与 `meta.evidence`；
- `docx-check.json`：回读实测（页面、页边距、栏数、字体、字号、题注、文献）；
- `source-inventory.json`：输入角色与内容单元计数；
- `adaptation-report.md`：人读摘要；
- `result.json`：官方四字段与要求台账。

## 4. 示例中的逐项结果（节选）

| 要求 | 期望 | 实测 | 状态 |
|---|---|---|---|
| 纸张 | A4，11906×16838 twips | A4，11906×16838 twips | pass |
| 页边距 | top 2.2 / left 2.5 cm | top 2.2 / left 2.499 cm | pass |
| 栏数 | 2 栏，栏间距 0.7 cm | 2 栏，0.7 cm | pass |
| 正文 | Liberation Serif，11 pt，1.5 倍行距 | 同上 | pass |
| 标题 | Heading 1–3 绑定 outlineLvl | Heading1 使用 6 次，outline 1 | pass |
| 表题 | 表上方，Table Caption | 表题在表上方，样式 Table Caption | pass |
| 参考文献 | 悬挂缩进 0.8 cm | Bibliography，hanging 0.8 cm | pass |
| 正文保真 | 词元多重集不减少 | similarity=1.0 | pass |
| 页眉页脚 | 模板引用保留 | 需 Word/LibreOffice 视觉确认 | manual_check |

## 5. 失败示例（无模板）

```json
{
  "status": "partial",
  "deliverable": {"path": "manuscript.docx", "format": "docx"},
  "unmet": [{
    "id": "U-TEMPLATE",
    "requirement": "template.docx",
    "reason": "no template DOCX candidate was found",
    "impact": "built-in A4 defaults and the guide were used"
  }]
}
```

仍然会产出可打开的 `manuscript.docx` 和完整 `result.json`，但状态必须是
`partial`，并在 `uncertainty` 中说明模板特定样式未读取。

## 6. 示例边界

- 示例模板与稿件均为合成，不含真实期刊版权内容；
- 示例中的分值、字体可用性和页数是本地环境的测量值，不是官方评分；
- 正式案例请以 `/input` 下真实文件为唯一事实依据；标签与文件名不能替代
  实际内容核对。
