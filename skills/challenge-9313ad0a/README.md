# 思维导图 Skill（v0.6.0）

## 用途

把论文、提案或笔记转成层级清晰的思维导图：节点带类型与来源定位，支持交叉链接，输出可编辑 SVG 与 JSON 大纲。

## 输入 / 输出

- 输入：`/input/task.md`、`/input/case.json`、论文/笔记/大纲/术语表
- 输出：`/app/output/mindmap.json`、`mindmap.svg`、`result.json`；可选 `preview/mindmap.png`
- `result.json` 核心字段：`mindmap`、`nodes[]`、`cross_links[]`、`coverage`、`deliverable`、`preserved_facts`、`evidence_map`、`change_log`

## 快速开始

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
# 1) 写 mindmap.json（单根、3-7 一级分支、每节点带 source）
python3 "$SKILL_DIR/scripts/mindmap_to_schematic.py" --outline /app/work/mindmap.json --out-spec /app/work/mindmap-spec.json --out-svg /app/output/mindmap.svg --json
python3 "$SKILL_DIR/scripts/svg_lint.py" /app/output/mindmap.svg --json
```

## 最小示例（示例，不是正式答案）

输入：1 篇 IMRaD 论文，要求 5 个一级分支、≤30 节点。  
输出：`mindmap.json` 28 节点、5 分支、4 条 cross-link；`mindmap.svg` 含 28 个 `<text>`；`result.json` 的 `deliverable` 指向 SVG，`preserved_facts/evidence_map` 覆盖核心节点。

## 设计要点

- 结构优先：单根、无环、一级分支 3–7 个，同级粒度一致。
- 节点一意：标签 ≤20 字，细节下沉子节点。
- 来源可追溯：claim/evidence 节点带 `source{path, locator}`；没有来源的只能来自 task.md。
- 交叉链接只连真实关系，带关系词；没有来源的删除或标 `inferred`。
- SVG 脚本是可选加速器；失败可手绘，不阻塞交付。

## 已知限制

- 默认 ≤40 节点；task.md 有明确数量时以 task.md 为准。
- 无 PNG 渲染工具时只交 SVG，`status=partial`，不伪造 PNG。
- 平台可能把技能装到题目专属目录，命令必须先解析 `$SKILL_DIR`。
