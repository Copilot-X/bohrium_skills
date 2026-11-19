# 学位论文与长文档修订 Skill（v0.5.0）

## 用途

按 `organizer-edit-workorder.json` 的 `target_ids`，在分节可编辑章节上做源文保留的修订：逐条 propose/accept/reject，支持版本比较、rollback/reapply、受控非法操作与真实 HTML 预览；未点名段落、引用、公式、表格、标题保持原样。

## 输入 / 输出

- 输入：`/input/task.md`、`/input/case.json`、`/input/organizer-edit-workorder.json`、`chapters/*`
- 输出：`/app/output/revised.md`、`preview.html`、`revision-record.json`、`original-records.json`、`result.json`
- `result.json`：`deliverable`、`preserved_facts`、`evidence_map`、`change_log`、`operation_receipts`

## 快速开始

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
python3 "$SKILL_DIR/scripts/revision_flow.py" init --input /input --work /app/work --json
python3 "$SKILL_DIR/scripts/revision_flow.py" plan --work /app/work --json
# 填 /app/work/annotations.json，然后：
python3 "$SKILL_DIR/scripts/revision_flow.py" propose --work /app/work --spec /app/work/annotations.json --json
python3 "$SKILL_DIR/scripts/revision_flow.py" accept E01 --work /app/work --json
python3 "$SKILL_DIR/scripts/revision_flow.py" export --work /app/work --out /app/output --json
```

## 最小示例（示例，不是正式答案）

输入：2 章、3 个未点名段落，workorder target_ids `["C01-P002","C02-P001"]`。  
输出：2 条批注；accept E01、reject E02；`compare v0→v1` 有 diff；`rollback v1→v0` 后 `reapply v0→v1`；`invalidate accept-unknown` 被捕获且 state hash 不变；`revised.md` 只多出批注块，`verify` 全部通过。

## 设计要点

- 批注是插入块，不修改原句；未点名段落逐字保留。
- 每条修改独立 accept/reject；拒绝也留 reason。
- 版本快照 + diff + rollback/reapply，状态可恢复。
- 受控非法操作是工单要求，不是平台故障；记录 state hash 不变。
- 预览是真实生成的 HTML，不伪造 PDF/PNG。

## 已知限制

- 只做工单点名的编辑，不整章重写、不补写未提供章节。
- `basis_quote` 必须逐字来自源文；找不到时重摘，不用 `--allow-unverified-quote` 冒充。
- 平台可能把技能装到题目专属目录，命令必须先解析 `$SKILL_DIR`。
