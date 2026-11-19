# 交付前自检

本文件是 `scripts/main.py finalize` 之后的人工复核清单。脚本负责机械一致性，
人负责“quote 是否真的蕴含 claim”。任何一项失败都不要用空数组或删正文掩盖。

## 1. 三个主产物

| 产物 | 必须存在 | 检查 |
|---|---|---|
| `text.md` | 是 | 模型写的完整正文，覆盖 task.md；体裁/篇幅/受众相符；不是提纲、不是抽取式拼接 |
| `claim-evidence.json` | 是 | 每条 claim 有 evidence；每个 quote 可回查；不支持句有处置 |
| `result.json` | 是 | `case_id` 正确；四个官方字段齐全；所有路径真实 |

官方四字段：

- `deliverable`：指向 `text.md`，格式/字数/语言/章节真实；
- `preserved_facts`：数字、单位、条件、样本量、引用编号，逐条带 source path+locator；
- `evidence_map`：claim → source → quote/locator/supports，正文句可对应；
- `change_log`：实际做过的动作、原因、证据；没有发生的事不要写。

若 `text.md` 尚未落盘，入口写 `status=partial` 且 `deliverable=null`；补写正文后
重新 finalize，不要把 partial 结果当成交付完成。

## 2. 数字回查

逐项检查 `result.json.number_index`：

- [ ] `found=false` 的 token 为 0，或已删除/改写对应句子；
- [ ] 每个关键数字在 quote 中逐字出现；
- [ ] `%`、百分点、倍、mg/kg 等单位未混用；
- [ ] 分母和比较对象写清（92% 的选择性，不是“92% 更好”）；
- [ ] 温度、压力、剂量、时间窗、样本量随数字出现；
- [ ] 不擅自增加小数位（“约 90%”不写成“90.0%”）；
- [ ] derived 值有可复算算式，正文写“计算得”。

## 3. 引用回查

逐项检查 `citation-audit.json`：

- [ ] 正文每个 `[n]` 在 `sources[]` 有 `citation_number=n`；
- [ ] 每个 `[n]` 在文末有 reference entry；
- [ ] 没有未被引用的孤儿条目（warning 也要解释）；
- [ ] 首次出现顺序符合目标格式（若要求顺序编号）；
- [ ] 句子引用的来源确实绑定到该句的 claim；
- [ ] 被 claim 使用的来源也在句中引用（否则补 `[n]` 或改句）；
- [ ] 不存在的文献、撤稿文献、只有摘要的文献已标注；
- [ ] reference entry 不含编造的作者、期刊、DOI、年份。

## 4. quote 逐字回查

逐项检查 `quote-audit.json`：

- [ ] `stats.not_found=0`；
- [ ] 每条 quote 是源文本的逐字子串（允许空白折叠与省略号分段）；
- [ ] 英文原文引英文，不翻译后回填；
- [ ] quote 包含支撑该 claim 的关键数字、条件、比较对象；
- [ ] 省略号没有改变语义；
- [ ] 摘要材料没有写成“全文表明”。

## 5. 论断强度

逐句问：

- [ ] 观测写成观测，推断写成推断；
- [ ] 相关没有写成因果；
- [ ] 单篇小样本没有写成领域共识；
- [ ] 摘要/预印本/模型结果没有写成已验证事实；
- [ ] 冲突证据并列呈现、条件保留、不取平均；
- [ ] 材料不足处写“现有材料未报告”，不用常识补全。

详细口径见 `references/hedging-language.md`。

## 6. 不支持句

- [ ] `result.json.coverage.unsupported` 与台账一致；
- [ ] 每个 `support=none` 句在 `unsupported[]` 有记录；
- [ ] `deleted` 句确实不在 `text.md`；
- [ ] `marked_as_reasoning` 句 `claims=[]`、`reasoning_marker=true`；
- [ ] 保留的推理句不含无法回查的数字；若有，改为 sourced 或删除。

## 7. result.json 与台账一致

- [ ] `sentences[]` 的 id/text/claims/support 与 `claim-evidence.json` 一致；
- [ ] `coverage` 四个计数与 sentences 一致；
- [ ] `evidence_map` 的 claim_id 与 claims[] 一致；
- [ ] `preserved_facts` 的 source path/locator 真实；
- [ ] `artifacts[]` 每个 path 都存在于 `/app/output`；
- [ ] `status=completed` 时没有 unsupported、citation error、quote error；
- [ ] `status=partial` 时 `uncertainty` 写清原因和影响。

## 8. 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 听起来合理但无出处 | claim 无 evidence | 删除或标 reasoning；补不上不要编 |
| 引用不支持该句 | 主题相关但断言不同 | 换源或改写句子 |
| 强度拔高 | “可能”写成“证明” | 恢复限定语、降 strength |
| 拼接两篇文献 | 结论是第三篇没说过的话 | 拆句，分别挂来源 |
| 丢失条件 | 省略样本量/温度/剂量 | 补回条件 |
| quote 被改写 | 引文与原文措辞不同 | 改回逐字摘录 |
| 摘要当全文 | “全文表明”但只有摘要 | 标“基于摘要”，降低结论范围 |
| 冲突被平均 | “综合来看约为……” | 并列双方，写清条件 |
| 数字对不上 | 正文 92%，quote 82% | 以可定位原文为准，记录冲突 |
| 台账与正文脱节 | work 目录没有 claims.json | 重新运行 finalize，按入口流程执行 |
