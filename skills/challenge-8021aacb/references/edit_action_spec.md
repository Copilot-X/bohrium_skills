# 可执行编辑动作规范（edit action spec）

`edit` 是 finding 与作者/脚本之间的执行契约。它必须能让另一个 Agent 或作者**不经二次解释**就知道改哪里、改成什么。本规范同时约束 `scripts/actions.py` 的自动应用行为。

## 1. 最小结构

```json
{
  "type": "move|reorder|merge|split|delete|insert|add_transition|rewrite|narrow|relabel",
  "before": "原文逐字片段（定位锚点）",
  "after": "替换/新增后的文字（按 type 必填）",
  "target": "目标段落 id / 位置标签（按 type 必填）",
  "occurrence": "first|last（可选，重复句消歧）"
}
```

规则：

- `before` 必须是原文的**逐字子串**；不得用摘要、省略号或“整体段落”代替。
- `after` 不得引入原文没有的数字、单位、条件或引用键。
- `target` 对结构化动作使用稳定定位：段落 id（如 `p3.2.1`）、章节标题或“结果章节末尾”这类可解析标签。
- 一条 finding 只表达一个动作；需要多个动作时拆成多条 finding 或使用 `reorder` 的章节级 target。

## 2. 类型语义

| type | before | after | target | 自动应用 | 说明 |
|---|---|---|---|---|---|
| `move` | 要移动的逐字句子/段 | 可选 | 目标段落 id | 是 | 从来源段落删除，追加到 target 段落末尾；数字随文本一起移动 |
| `reorder` | 受影响章节标题 | 可选 | 新位置标签 | 否 | 整章重排涉及跨节引用，默认人工执行 |
| `merge` | 被合并段落的首句 | 可选 | 保留段落 id | 是 | 把来源段落文本并入 target，来源段落清空 |
| `split` | 段落首句 | 可选 | “段落中部”等 | 是 | 在句数中点附近拆成两段，不改文字 |
| `delete` | 待删除逐字文本 | 不需要 | 可选 | 条件式 | 只有 before 不含数字/引用时才自动删除 |
| `insert` | 插入点前的逐字句子 | 新增文字 | 可选 | 是 | 在 before 之后插入 after；新增文字不得含数字/引用 |
| `add_transition` | 需要承接的段首/句首 | 过渡句 | 可选 | 是 | 在 before 之前插入 after；新增文字不得含数字/引用 |
| `rewrite` | 原文逐字片段 | 改写后文字 | 可选 | 是 | 数字、单位、引用键集合必须与 before 完全一致 |
| `narrow` | 原文逐字片段 | 收窄后文字 | 可选 | 是 | 同 rewrite；用于因果→关联、全称→样本内、首创→本文 |
| `relabel` | 旧标签/标题 | 新标签 | 可选 | 是 | 数字/引用保护同 rewrite；不改标签指向的事实 |

## 3. 定位与消歧

1. 优先用 `location.paragraph_id` 对应段落中的 `before` 定位。
2. 同一句在稿件中出现多次（D5 跨章节重复）时，必须写 `occurrence: "last"`（或 `"first"`）或给出唯一上下文；`actions.py` 在无法唯一确定时跳过并记录原因。
3. 缺失章节/图表类 finding 的 `location.paragraph_id` 可写 `document`/`chapter`/`section`，并用 `evidence.kind = "structure" | "omission"` 说明缺什么。此时 `edit.before` 用相邻的真实标题作为插入锚点。
4. 不得用行号作为唯一定位；行号在 docx/PDF 转换后不稳定。

## 4. 自动应用保护（actions.py）

对 `apply: true` 的 finding，依次检查：

1. `type` 在支持集合内；`before` 非空；内容类动作有 `after`。
2. **数字/引用保护**：`guard_tokens(before) == guard_tokens(after)`（guard 包含百分比、统计量、样本量、单位、年份、`[n]`、`(Author, 2020)`、`\cite{}`、`[@key]`）。
3. **条件保护**：rewrite/narrow/relabel 不得丢掉 before 中的条件词（随机、对照、横断面、剂量、温度、样本、纳入/排除标准等）。
4. **删除保护**：`delete` 的 before 含数字/引用时拒绝自动删除，改为人工处理。
5. **新增保护**：insert/add_transition 的新增文字不得含数字、单位或引用。
6. 全部应用后做**全局回滚检查**：原稿与 `revised.md` 的 guard token 集合必须完全相等。不等则整批回滚，`change_log` 记为未应用。

被拒绝的编辑不会消失：finding 保留 `apply: false` + `apply_reason`，`change_log` 写明“未自动应用：原因”。

## 5. change_log 格式

每条 finding 一条记录（review-only 用一条 `finding_id: "none"`）：

```json
{
  "finding_id": "F-03",
  "action": "已应用 delete 编辑：删除讨论中重复的结论句",
  "diff": "- 睡眠质量与学业成绩之间的关联在不同模型中保持稳定。\n+ [deleted]",
  "locator": "4/4.1/p4.1.1",
  "applied": true
}
```

- `diff` 至少给一行 before/after 摘要；结构化动作可写 `[moved to p3.2.1]`。
- 未应用：`"diff": "无"`，`"applied": false`，action 以“未自动应用：”开头。
- 不得把未执行的编辑写成已执行；不得为了凑数生成假 diff。

## 6. 质量检查

- 每条 finding 的 `edit.type` 在词表内；`move/merge/split/reorder` 有 `target`；`rewrite/narrow/relabel/insert/add_transition` 有 `after`；`delete` 只需 `before`。
- `revised.md` 存在时，所有 `apply: true` 的 `after` 应能在正文中找到；`delete` 的 before 不应再出现。
- 数字/引用失败时优先降级为 review-only 并说明，不得强行输出改稿。
- 自动引擎只处理确定性文本操作；语义判断（如“这段是否真的该合并”）由作者按 suggestion 复核。
