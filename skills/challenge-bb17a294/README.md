# academic-translator 0.5.0

学术翻译与双语写作 skill：**译文优先**。先把原文完整译出并落盘，再补术语表、保真记录与 `result.json`；checker 与回译抽检是可选审计，失败不阻塞交付。

## 输入

- `/input/task.md`：方向、文体、读者、输出形态与硬约束（以此为准）。
- `/input/case.json`：`case_id`、语言方向、`output_contract`。
- 原文材料路径（md/txt/docx/tex/pdf；PDF/扫描件依赖运行时 PDF/OCR 工具）。
- 可选：作者术语表、期刊风格、已有译文（润色模式）。

## 输出

| 文件 | 必需 | 说明 |
|---|---|---|
| `translation.md` | 是 | 完整纯译文，按原文顺序 |
| `bilingual.md` | 是 | 段级对照，`## Sxx` + Source/Target |
| `result.json` | 是 | 任务字段 + 官方 writing 别名 |
| `glossary.json` | 建议 | 5–20 条锁定术语 + register |
| `translation-notes.json` | 可选 | 紧凑机读记录 |
| `translation-report.md` | 可选 | 10–30 行人读核对记录 |
| `translation-check.json` | 可选 | checker 报告（未跑不生成） |

`result.json` 顶层：`case_id`、`task_family`、`mode`、`status`、`direction`、`artifacts`、`register`、`segments`、`glossary`、`preservation`、`back_translation_check`、`untranslated`、`uncertainty`、`change_log`、`translation_check`，以及官方别名 `deliverable`、`preserved_facts`、`evidence_map`、`change_log`。

## 三条命令

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"

# 1) 可选：切段 + 候选术语
python3 "$SKILL_DIR/scripts/segment_source.py" --source /input/source.md \
  --out /app/work/segments.json --candidates /app/work/glossary-candidates.json

# 2) 可选：从 bilingual.md 生成 result.json 骨架（避免重复输出全文）
python3 "$SKILL_DIR/scripts/assemble_result.py" --case-id CASE-ID --direction 'zh->en' \
  --segments /app/work/segments.json --bilingual /app/output/bilingual.md \
  --glossary /app/output/glossary.json --artifact-root /app/output \
  --out /app/output/result.json --status completed

# 3) 可选：审计（exit 0/1/2；失败记 audit_unavailable，不阻塞）
python3 "$SKILL_DIR/scripts/check_translation.py" --source /input/source.md \
  --result /app/output/result.json --glossary /app/output/glossary.json \
  --bilingual /app/output/bilingual.md --artifact-root /app/output \
  --report /app/output/translation-check.json
```

审计后可按需补官方写作别名（失败时手工补）：

```bash
python3 "$SKILL_DIR/scripts/emit_official.py" --result /app/output/result.json \
  --out /app/output/result.json --profile writing --json
```

## 最小示例（示例，不是正式答案）

源文：`氧空位是 CeO2 表面催化活性的关键缺陷。我们在 120 °C 下测试了 3 个样品[3]。`

输出片段：

```markdown
## S01
- **Source (zh):** 氧空位是 CeO2 表面催化活性的关键缺陷。我们在 120 °C 下测试了 3 个样品[3]。
- **Target (en):** Oxygen vacancies are the key defects governing the catalytic activity of CeO2 surfaces. We tested 3 samples at 120 °C [3].
```

对应 `result.segments[0].source` 与上面 Source 行一致；`preservation` 在核对 `120 °C`、`3`、`[3]` 后写 `true`。该示例只演示接口，不是正式案例结果。

## 降级路径

- 无 `segment_source.py`：按空行手工切段，标题/图注/表格/公式各自成段。
- 无 `check_translation.py` 或运行失败：`uncertainty` 记 `audit_unavailable`，手工核对数字、单位、引用、否定、情态与术语后交付。
- 无术语表：可在 `result.glossary` 内嵌最小术语，并在 `uncertainty` 说明。
- 源文缺失/为空：`status=input_error`，不产出伪译文。
- 时间不足：保存已译内容，未译段列入 `untranslated`，`status=partial`。

## 限制

- checker 是启发式筛检，不判断译文的学术质量，也不替代人工。
- PDF/OCR 依赖运行时提供的工具；扫描件数字与引用需人工复核。
- 超长文需分块；拼接后必须复核段数、顺序与术语一致性。
