# Graphical Abstract 输出契约

`scripts/run_case.py` 是唯一入口。它由 `scripts/result_graphics.py` 统一生成
`/app/output/result.json`；Agent 不得手写或补造官方字段。

## 1. 调用与退出码

```bash
python "$SKILL_DIR/scripts/run_case.py" \
  --input /input --out /app/output --work /app/work --json
```

| 退出码 | 含义 | result.json |
|---|---|---|
| 0 | 已完成、partial 或 input_error | 一定写出 |
| 2 | 无法启动（脚本缺失、输出目录不可写） | 可能不存在 |

`input_error` 只用于 `/input` 或 `/input/case.json` 缺失/不可解析；材料不足、
导出失败、审计失败一律写 `partial`，不得伪装成 `answered`。

## 2. 输出文件

```
/app/output/result.json              # 官方结果，唯一必需 JSON
/app/output/figures/ga.svg           # 可编辑源文件
/app/output/figures/ga.pdf           # 真实导出件（导出成功时）
/app/output/preview/ga.png           # 真实预览（导出成功时）
/app/work/ga-spec.json               # 归一化后的绘图 spec
/app/work/coverage.json              # audit.py 原始报告
/app/work/inventory.json             # 真实输入盘点（路径/字节/sha256）
/app/work/run-report.json            # 本次运行摘要
```

## 3. status 词表

| status | task_status | 触发条件 |
|---|---|---|
| `answered` | `completed` | claim 非空、3–5 元素、导出 PDF+PNG 有效、审计无 error |
| `partial` | `partial` | 缺来源、元素不足、渲染/导出失败、审计 error |
| `input_error` | `input_error` | `/input` 或 `case.json` 不可用 |

## 4. 必需 media 字段

`result.json` 恒含以下六个字段：

```json
{
  "status": "answered|partial|input_error",
  "editable_source": {"path": "figures/ga.svg", "format": "svg"},
  "export_artifact": {"path": "figures/ga.pdf", "format": "pdf", "dpi": 300},
  "content_map": [
    {"element_id": "G1", "element_type": "visual_element",
     "kind": "reported",
     "maps_to": {"path": "paper.txt", "locator": "sentence 2"}},
    {"element_id": "E1", "element_type": "relation",
     "kind": "narrative",
     "maps_to": {"path": "paper.txt", "locator": "sentence 2"}}
  ],
  "compliance_checks": {
    "xml_valid": true,
    "text_as_text": true,
    "no_external_refs": true,
    "export_signature": "pdf",
    "export_signatures": {"pdf": true, "png": true},
    "dpi": 300,
    "single_claim": true,
    "element_count": 3,
    "relation_count": 2,
    "coverage": 1.0,
    "min_font_ok": true,
    "status_complete": true
  },
  "exceptions": []
}
```

- `export_artifact` 在没有任何真实导出时为 `null`，此时 `status` 必须是
  `partial`，并在 `exceptions` 写 `export_unavailable`。
- `content_map` 必须覆盖 SVG 中每一个 `G*` 元素与 `E*` 关系；`audit.py` 用
  `coverage` 校验，缺一即 error。
- `compliance_checks` 恒为 object；布尔值来自 `audit.py` 的真实检查。

## 5. 其余字段

| 字段 | 来源 | 要求 |
|---|---|---|
| `case_id` | `/input/case.json` | 与输入一致 |
| `task_family` / `workorder` | 固定 | `graphical-abstract` / `graphical_abstract` |
| `graphical_abstract.claim` | spec | 单一主结论，可为空仅当 partial |
| `graphical_abstract.canvas` | spec | width/height/dpi |
| `elements[]` | spec | id/role/concept/source/kind |
| `relations[]` | spec | id/from/to/label |
| `claim_support[]` | claim.support + numbers | 每项有 path/locator |
| `visual_claim_ledger[]` | 由 claim 与 elements 生成 | 每个断言可回溯 |
| `not_claimed[]` | spec | 明确边界 |
| `consistency` | 真实推导 | `visual_reviewed` 只有实际打开预览后才为 true |
| `checks` | audit 摘要 | XML/文字/外部引用/导出签名 |
| `artifacts[]` | 真实存在的文件 | editable/export/preview |
| `uncertainty[]` | run_case | 尺寸默认、spec 由输入推导等 |
| `change_log[]` | run_case | 读取、渲染、导出、归一化记录 |

## 6. 视觉复核

`--visual-reviewed` 只有在真的打开 `preview/ga.png` 或 `figures/ga.pdf` 后才能加。
未复核时 `checks.visual_reviewed=false`、`consistency.visual_reviewed=false`，
不得写成 true。
