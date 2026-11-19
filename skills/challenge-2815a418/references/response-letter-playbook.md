# 审稿回复参考（逐条、真实修改、诚实边界）

配合 `templates/point-by-point.json` 和 `scripts/check_response_letter.py` 使用。
**只回应输入中的意见**；不虚构审稿人原话、补充实验、接收状态或全文返修。

## 1. 意见原子化

一条意见含多个诉求时拆成 `R1-C2a/C2b`：

```json
{"comment_id":"R1-C2","reviewer":"R1","text":"…",
 "type":"method|statistics|clarity|novelty|format|data",
 "severity":"major|minor","actionable":true,"needs_new_data":false,
 "target":{"chapter":"2.3","paragraph_id":"p2.3-4"}}
```

逐条回应，不漏子问题。

## 2. 四类处置

| 类型 | action | 必须给 |
|---|---|---|
| 可编辑解决 | `revised` / `clarified` | 真实 before/after + diff 路径 |
| 已有数据可重算 | `computed` | 方法、结果、数据来源 |
| 需新实验 | `deferred` | “本次未开展” + 计划 + 对结论的限制 |
| 不认同 | `disagreed` | 文献/数据依据，礼貌且具体 |

“删掉争议句”不等于解决；要说明为什么删、对结论的影响。

## 3. 回复信结构

每条：

```
Comment R1-C2: 原文
Response: 第一句直接给结论（We have added… / We respectfully disagree because…）
Changes: 章节 + 段 + 原句片段 + 改后片段
```

开头致谢一句；不空泛道歉；不写“已接收/已核对期刊系统”。

## 4. 真实修改与对应

- 每条 `revised` 必须有 `manuscript_change`：chapter/paragraph_id/before/after/diff。
- 声称的改动必须能在 revised 稿中找到；revised 稿的改动也要能在回复信找到。
- 无法完成的事项写 `deferred` + 原因 + 替代方案 + 影响。
- 统计意见必须给检验方法与数值，不能只写“已重新分析”。

## 5. 诚实红线

`honesty` 三项默认 false：

- `new_experiments_claimed`：只有基线材料真有新数据才可为 true；
- `acceptance_claimed`：任何编辑部状态都不得声称；
- `full_revision_claimed`：未完成全部修改不得写完整返修。

## 6. 语气

感谢—回应—依据—修改位置，四段式；不卑不亢，不人身化。
多位审稿人冲突时分别回应，指出冲突，请编辑裁定，不假装一致。

## 7. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| 漏答子问题 | 一条只答一半 | 拆原子条目 |
| 声称已改无改动 | 回复有 location 稿中无 | 补真实修改/改口径 |
| 虚构补充实验 | “新增 n=30”无数据 | 删除，写限制 |
| 空泛感谢 | 无实质回应 | 加具体修改/依据 |
| 反驳无据 | 只有“不同意” | 补数据/文献 |
| 三处不一致 | 信/稿/矩阵不同 | 以修改稿为准 |

## 8. 交付前检查

- [ ] 每条评论（含子问题）有 action 与回应。
- [ ] 每条 revised 有真实 diff，after 能在稿中找到。
- [ ] deferred 有原因/计划/影响；disagreed 有依据。
- [ ] honesty 三项为 false（或有可证理由）。
- [ ] 回复信、修改稿、point-by-point 三者一致。
- [ ] `check_response_letter.py` 返回 `ok: true`。
