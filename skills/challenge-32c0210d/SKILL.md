---
name: source-preserving-reviser
description: 学位论文/长文档的源文保留修订：按 organizer-edit-workorder 的 target_ids 逐条提出编辑批注，支持 propose/accept/reject、版本比较、rollback/reapply、受控非法操作与真实 preview，未点名段落逐字保留。Run a local source-preserving revision workflow over sectioned long documents with per-change accept/reject, version compare, rollback/reapply and a real export preview. Use for 学位论文修订、长文档改稿、编辑工单、版本回退、修订审计。
---

# 学位论文与长文档修订（Source-Preserving Reviser）

## 用途与触发

task.md 要求“在给定章节上按工单修改”“保留原文/来源”“逐条接受或拒绝修改”
“版本对比/回退”“提供可编辑稿与修订记录”时使用。核心不是从头写整篇论文，
而是**在真实分节源文上做最小、可追溯、可回退的修订**。

## 输入

- `/input/task.md`：修订目标、篇幅、格式、语言；**以它为准**。
- `/input/case.json`：`case_id`、`chapters[]` / `manuscript`、`output_contract`。
- `/input/organizer-edit-workorder.json`（或 case.json 内 `organizer_edit_workorder`）：
  `target_ids`、`task`、`operation_requirements`、`boundaries`。
- 章节文件：`chapters/*.md` / `.txt` / `.tex` / `.docx`，或 case.json 内联内容。
- 输出：`/app/output/`；过程文件：`/app/work/`。

## 路径解析（先做）

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
echo "SKILL_DIR=$SKILL_DIR"
mkdir -p /app/work /app/output
```

后续命令用 `"$SKILL_DIR/scripts/revision_flow.py"`。

## 支持文件（按需使用，不阻塞）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `scripts/revision_flow.py` | 核心 CLI：init/plan/propose/accept/reject/status/compare/rollback/reapply/invalidate/export/verify | `python3 "$SKILL_DIR/scripts/revision_flow.py" --help` |
| `references/longform-playbook.md` | 需要章节比例、跨章一致性、检查点或长文档失败模式时 | 按小节查阅，不抄进交付物 |
| `templates/chapter-index.json` | 章节较多、需要手工记录章节地图时 | 可选；`revision_flow.py init` 已生成 baseline，不必重复建树 |

CLI 失败/超时/环境缺依赖时**不阻塞交付**：保留已生成的 baseline、diff、revised.md，
把未完成操作写入 `limitations`；不要为了跑通脚本删真实批注或伪造 receipt。

## 交付物优先流程（必须按顺序）

### 第 1 步（约 15% 预算）：读工单，确认 target

读 task.md、case.json、workorder，抄下 `target_ids` 与必须保留的内容。
运行：

```bash
python3 "$SKILL_DIR/scripts/revision_flow.py" init \
  --input /input --work /app/work --json
python3 "$SKILL_DIR/scripts/revision_flow.py" plan \
  --work /app/work --json
```

`init` 产出 `import-receipt.json`、`baseline/`、`edit-plan.json`、
`annotations.template.json`。检查 `targets[]`：`found=false` 的目标不要猜，
在 `annotations.json` 中跳过并在 `uncertainty` 说明。

### 第 2 步（约 40% 预算）：写批注并 propose

复制 `annotations.template.json` 为 `/app/work/annotations.json`，每个 found target
一条：

```json
{"case_id":"...","annotations":[
  {"change_id":"E01","target_id":"C01-P002","annotation_type":"clarification",
   "annotation":"一条实质性、有来源依据的编辑批注（不是空泛建议）",
   "claim":"批注要说明的科学条件/限制/澄清",
   "basis_quote":"从原文逐字复制的短引文","locator":"C01-P002",
   "uncertainty":"仍不确定的点"}]}
```

`basis_quote` 必须能在源文中逐字找到；找不到就重新摘录，不要改写。
然后：

```bash
python3 "$SKILL_DIR/scripts/revision_flow.py" propose \
  --work /app/work --spec /app/work/annotations.json --json
```

`propose` 只写 `diffs/` 与 `proposals/`，不改原文。逐条查看 diff。

### 第 3 步（约 20% 预算）：逐条 accept/reject

```bash
python3 "$SKILL_DIR/scripts/revision_flow.py" accept E01 --work /app/work --json
python3 "$SKILL_DIR/scripts/revision_flow.py" reject E02 --work /app/work \
  --reason "改写原句，超出工单" --json
python3 "$SKILL_DIR/scripts/revision_flow.py" status --work /app/work --json
```

每条修改独立决策；拒绝也要留 reason。不要一次接受全部来省时间。

### 第 4 步（约 15% 预算）：版本比较、回退、重放、受控异常

```bash
python3 "$SKILL_DIR/scripts/revision_flow.py" compare --work /app/work \
  --from v0 --to v1 --json
python3 "$SKILL_DIR/scripts/revision_flow.py" rollback --work /app/work \
  --to v0 --json
python3 "$SKILL_DIR/scripts/revision_flow.py" reapply --work /app/work \
  --to v1 --json
python3 "$SKILL_DIR/scripts/revision_flow.py" invalidate --work /app/work \
  --operation accept-unknown --change-id E99 --json
```

`invalidate` 是工单要求的**受控故障注入**：它应被捕获、状态哈希不变，
并写 `invalid-operation-receipt.json`。不要把它说成平台故障。

### 第 5 步（约 10% 预算）：导出、验证、写结果

```bash
python3 "$SKILL_DIR/scripts/revision_flow.py" export \
  --work /app/work --out /app/output --json
python3 "$SKILL_DIR/scripts/revision_flow.py" verify \
  --work /app/work --out /app/output --json
```

`export` 产出 `revised.md`、`preview.html`、`revision-record.json`、
`original-records.json`、`result.json`。`verify` 检查 baseline 哈希、未点名段落、
引用/公式/表格/标题是否保持不变、target 批注是否存在。

## 输出契约

`/app/output/result.json` 至少包含：

```json
{
  "case_id": "与输入一致",
  "status": "completed|partial|input_error",
  "deliverable": {"path": "revised.md", "format": "markdown"},
  "preserved_facts": [
    {"fact_id": "PF-01", "value": "92%", "unit": "%", "condition": "350 °C",
     "locator": "C01-P002", "note": "原文未改动"}
  ],
  "evidence_map": [
    {"change_id": "E01", "target_id": "C01-P002", "claim": "…",
     "source": {"path": "chapters/C01.md", "locator": "C01-P002"},
     "quote": "逐字原文"}
  ],
  "change_log": [
    {"change_id": "E01", "action": "accepted", "before": "…", "after": "…",
     "reason": "…"}
  ],
  "operation_receipts": [
    {"operation": "import", "ok": true}, {"operation": "propose", "ok": true},
    {"operation": "accept", "ok": true}, {"operation": "reject", "ok": true},
    {"operation": "compare", "ok": true}, {"operation": "rollback", "ok": true},
    {"operation": "reapply", "ok": true}, {"operation": "invalidate", "ok": true},
    {"operation": "export", "ok": true}, {"operation": "verify", "ok": true}
  ],
  "artifacts": [
    {"role": "editable", "path": "revised.md", "format": "markdown"},
    {"role": "preview", "path": "preview.html", "format": "html"},
    {"role": "record", "path": "revision-record.json", "format": "json"}
  ],
  "uncertainty": [],
  "limitations": []
}
```

`export` 会写大部分字段；若缺 `preserved_facts`，从原文数字/条件补最小清单。
官方 writing 别名 `deliverable/preserved_facts/evidence_map/change_log` 必须存在。

## 组织方要求映射

| 工单要求 | 对应产物 |
|---|---|
| actual import/task receipt | `import-receipt.json` + `receipts.jsonl` |
| propose and individually accept/reject | `proposals/`、`diffs/`、`state.json` 的 accepted/rejected |
| version comparison | `versions/compare-*.diff` + `compare` receipt |
| rollback/reapplication | `rollback`/`reapply` receipts + 版本快照 |
| controlled invalid-operation exception | `invalid-operation-receipt.json`，state hash 不变 |
| editable manuscript / original-revised records | `revised.md`、`original-records.json`、`revision-record.json` |
| evidence map | `result.json.evidence_map` |
| real export preview | `preview.html`（内嵌修订稿与 diff，可直接打开） |

## 异常处理

| 情况 | 处理 |
|---|---|
| target 不存在 | 跳过该 target，写入 `uncertainty` 与 `limitations`，不新建同名段落 |
| quote 找不到 | 重新逐字摘录；仍找不到则换一条有依据的批注，不用 `--allow-unverified-quote` 冒充 |
| 输入不可读 | `status: "input_error"`，写清缺什么，不编造章节 |
| 时间不足 | 先完成 init/propose/accept/export；rollback/reapply/invalidate 可在 `limitations` 说明未完成 |
| 预览无法生成 | 保留 `revised.md`，`status: "partial"`，`artifacts` 不写不存在的文件 |

## 自检

- [ ] `init` 的 targets 全部有 `found` 状态
- [ ] 每条批注有 target、逐字 basis_quote、claim 与 uncertainty
- [ ] 至少一条 accept 与一条 reject（工单要求逐条决策）
- [ ] `compare` 有真实 diff；`rollback`/`reapply` 改变并恢复版本
- [ ] `invalidate` 的 state hash 前后一致
- [ ] `verify` 的 `original_text_preserved`、`unrequested_blocks_unchanged`、
      `target_annotations_present` 均为 true
- [ ] `result.json` 的 `deliverable` 路径真实存在；官方别名齐全
- [ ] 未点名段落、引用、公式、表格、标题未改

## 最小示例（示例，不是正式答案）

输入：2 章、3 个未点名段落、workorder target_ids `["C01-P002","C02-P001"]`。  
输出：2 条批注；accept E01、reject E02；`compare v0→v1` 有 diff；
`rollback v1→v0` 后 `reapply v0→v1`；`invalidate accept-unknown` 被捕获且 state 不变；
`revised.md` 只多出 2 个批注块，`verify` 全部通过。

## 版本与限制

- 版本：0.5.0（交付物优先；批注是插入块，不改原句；CLI 全 stdlib）。
- 只做工单点名的编辑；不整章重写、不补写未提供章节。
- `preview.html` 是真实生成的离线预览，不是截图；不伪造 PDF/PNG。
- 平台可能把技能装到题目专属目录；必须先解析 `$SKILL_DIR`。
