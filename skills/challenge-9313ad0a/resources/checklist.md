# 思维导图交付检查单（Checklist）

本文件是 `scripts/check_mindmap.py` 的规则说明书。脚本是硬检查，人工复核用它补语境。
统一从 `scripts/main.py` 调用：

## 1. 运行命令

```bash
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
python3 "$SKILL_DIR/scripts/main.py" run \
  --input /input --work /app/work --output /app/output --layout tree --json
python3 "$SKILL_DIR/scripts/main.py" check \
  --material /app/work/material.json --outline /app/work/mindmap.json \
  --spec /app/work/mindmap-spec.json --svg /app/output/mindmap.svg \
  --result /app/output/result.json --root /app/output --json
```

`run` 对 partial/input_error 也写 `result.json` 并退出 0；`check` 退出码 0 通过、1 有问题。

## 2. 结构与层级（G1）

- [ ] 恰好 1 个根节点（`parent` 为空）。
- [ ] 每个 `node_id` 唯一，`parent` 均存在，无环。
- [ ] 一级分支 3–7 个；树深 ≤ 4（硬上限 6）。
- [ ] 同级节点粒度一致，标签 ≤ 20 字（硬上限 60）。
- [ ] 同级不出现重复标签；一个节点只表达一个意思。

## 3. 节点与来源（G2/G3）

- [ ] `type` ∈ `topic/concept/question/method/evidence/result/claim/risk/gap/task`。
- [ ] `kind` ∈ `reported/task/inferred`。
- [ ] `reported` 节点必须有 `source.path` + `source.locator`（正文可带 `quote`）。
- [ ] `task` 节点只能来自 task.md 的组织性要求，`source` 含 `task`。
- [ ] `inferred` 节点必须在 `detail` 写出推断依据，且 ≤ 节点总数 20%。
- [ ] 核心结论/数字与材料逐字一致；不改写数值、条件、术语。

## 4. 关系与边（G4/G5）

- [ ] 层级边覆盖 `nodes-1` 条；每条边有 source 或标 `inferred`。
- [ ] `cross_links` 两端存在、非自环、关系词在允许表内。
- [ ] 不添加材料没有依据的跨分支连接；同句共现也需带 quote。
- [ ] 交叉链接用虚线；关系词写在 `data-relation`，不靠位置暗示。

## 5. SVG 可编辑性（G6）

- [ ] XML 可解析，根为 `<svg>`，有数值 `viewBox` 与正尺寸。
- [ ] 至少每个节点一个 `<g data-node-id data-type data-kind data-source>`。
- [ ] 文本为真实 `<text>/<tspan>`，不转曲；字号 ≥ 10。
- [ ] 无 `<script>`、`<image>`、`http(s)://`、`url(`、`@import` 等外部引用。
- [ ] SVG 节点 id、边 id 与 JSON/spec 一致；无空文本、无明显文字溢出。
- [ ] 每个节点 `<title>` 同时写出标签与来源，便于编辑时追溯。

## 6. 官方 result.json 字段（G7）

- [ ] `case_id` 与输入完全一致。
- [ ] `deliverable.path` 真实存在，优先 `mindmap.svg`，否则 `mindmap.json`。
- [ ] `preserved_facts` 覆盖核心节点/数字/条件，逐条带 source。
- [ ] `evidence_map` 覆盖 claim/evidence/result 节点，带 path/locator/quote。
- [ ] `change_log` 记录真实步骤（读取、抽取、渲染、校验、落盘、人工确认项）。
- [ ] `status` 为 `completed|partial`；partial 必须写清 `uncertainty` 与限制。

## 7. 诚实性红线

- [ ] 示例/演示数据明确标 `synthetic`，不冒充官方案例结果。
- [ ] 不伪造 PNG/PDF 预览；没有渲染器就交 SVG 并说明。
- [ ] OCR 失败、材料缺失、数值冲突分别写入 `uncertainty`，不静默忽略。
- [ ] 节点/关系数量与材料规模匹配，不为了“看起来完整”编造分支。
