# 论文转学术 PPT 参考

配合 `templates/deck-plan.json` 和 `scripts/check_paper_deck.py` 使用。
**task.md 的受众、时长、页数、语言优先**；只使用论文材料，不联网补内容。

## 1. 先定预算

- 页数 ≈ 可用分钟 ÷ 1.5；15 分钟约 12–15 页。
- 分配参考：背景 20% / 方法 25% / 结果 40% / 结论与局限 15%。
- 页数不够时先砍方法细节，保结果与局限。
- `must_cover` 写清必须覆盖的内容；缺口进 `content_gaps[]`，不编造。

## 2. Claim 台账

每条上台结论：

```json
{"claim_id":"F001","text":"A 在 350 °C 保持 92% 选择性",
 "value":"92","unit":"%","condition":"350 °C, 1 bar",
 "source":{"path":"materials/paper.pdf","locator":"Fig.2a; §4.1"},
 "kind":"reported"}
```

- 只抽结论级事实；数字原样保留。
- 每页只承载台账内的 claim；没有 claim 的 title/section 页写 `claims: []`。
- 台账外内容不上页；缺证据的内容进 `content_gaps`。

## 3. 故事线与标题

- 标题是**结论句**，不是话题词：`A 在 350 °C 保持 92% 选择性` 优于 `结果`。
- 连续读标题应能复述全文逻辑。
- 结构选一条：问题解决 / 时间轴 / 总—分—总 / 对比 / 案例驱动；不混搭。
- 每页一个主张；结果页一图 + 1–2 句解读，不搬论文段落。

## 4. 页面类型与版式

| kind | 用途 |
|---|---|
| title | 封面 |
| section | 章节过渡 |
| content | 文字/要点 |
| chart / figure | 图或图表 |
| table | 表格 |
| ending | take-home 3 条 |

- 内容页 3–5 个要点，每点 ≤12 词；细节进 notes。
- 同一版式不连续超过 2 页；一页一图，图片不拉伸。
- 标题 28 pt、正文 18 pt、页脚 10 pt；深蓝标题/深灰正文。
- 局限单独一页；结尾给 3 条 take-home，不用“谢谢”空页。

## 5. 讲者备注

每页 3–5 句口语要点：这页为什么重要、关键数字、过渡句。
30–60 秒/页，不逐字念页面。

## 6. 页级证据映射

每页：

```json
{"no":6,"role":"result","title":"…",
 "claims":[{"claim_id":"F001","source":{"path":"…","locator":"Fig.2a"}}],
 "visual":{"path":"materials/fig2a.png","locator":"Fig.2a"},
 "notes_seconds":60,"layout":"content"}
```

页脚引用：`Author et al., Journal Year, Fig.X`；图来自论文时注明 Fig 号，不二次加工。

## 7. 生成与校验

```bash
python3 {SKILL_DIR}/scripts/make_pptx.py --spec /app/work/deck-spec.json \
  --out /app/output/deck.pptx --json
python3 {SKILL_DIR}/scripts/pptx_edit.py verify /app/output/deck.pptx --json
python3 {SKILL_DIR}/scripts/check_paper_deck.py --result /app/output/result.json \
  --deck /app/output/deck.pptx --spec /app/work/deck-spec.json --root /app/output --json
```

`check_paper_deck.py` 检查页数、标题、notes、claims 映射、spec 一致与 PPTX 结构；
预览必须真实渲染，工具缺失时 `status=partial`，不伪造。

## 8. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| 论文搬家 | 整段文字上页 | 图 + 1–2 句 |
| 标题无信息 | “方法/结果” | 改结论式 |
| 页数超时 | 页数×1.5 > 时长 | 删背景/方法 |
| 图不对文 | 图与标题无关 | 换图/改结论 |
| 无备注 | 只有页面文字 | 补口语要点 |
| 预览缺失 | 未渲染 | 渲染并检查 |
| 编造内容 | 材料外数据 | 删除或进 content_gaps |

## 9. 交付前检查

- [ ] 页数、时长、受众符合 task.md。
- [ ] 每页标题是结论句，能指回 claim。
- [ ] 数值与原文一致。
- [ ] 每页 notes 非空。
- [ ] PPTX 通过 verify，页数与 spec 一致。
- [ ] 预览真实；局限页存在。
- [ ] `check_paper_deck.py` 返回 `ok: true`。
