# 限制与降级（limits）

本 skill 是 **材料约束型** 论文写作工作流：它把输入材料变成可编辑稿件与可回查
台账，不替代作者的领域判断，也不补造材料外事实。以下限制与对应降级行为是交付
的一部分。

## 1. 输入限制

| 情况 | 行为 |
|---|---|
| 无 `case.json`/`task.md` | 不写正文；`result.json` 记 `input_error`，说明缺哪个文件 |
| 无可读材料 | 输出限制骨架，`status=insufficient_input`，`preserved_facts=[]` |
| 仅图片/扫描件 | 无 OCR 时标 `readable=false`；不猜图内数值；写入 `uncertainty` |
| PDF 无 `pdftotext`/`pdf-text-tool` | 该文件标不可读，其余材料继续；不伪造解析结果 |
| 文件超过 4 MB | 不整篇读入；标 `size-limit`，由作者拆分后重跑 |
| 材料超长/重复 | 抽取有定位的块，最多 120 块/文件、80 条 fact；余量进 `uncertainty` |

## 2. 证据限制

- 只记录材料中出现的数值/陈述；不推断缺失的实验条件、样本量或统计检验。
- 图注与正文冲突、表格与摘要冲突：并列进 `conflicts[]`，不选边、不平均。
- 近似值（图读数、“约/approx/~”）标 `estimated`，不进入摘要与结论的确定表述。
- 引用字段不全时保留可得字段；不生成 DOI、卷期、页码或作者。
- `evidence_map` 只覆盖能绑定到 fact 的句子；背景句可以无 fact，但不得携带新数字。

## 3. 生成限制

- 正文由模型写作。`prepare`/`run` 在缺少模型稿时只写一个明确标注
  `PARTIAL SCAFFOLD` 的骨架，并始终以 `partial`/`insufficient_input` 交付；
  骨架不是抽取式成稿，不得冒充 `completed`。
- 默认模板只覆盖 research-article/review/letter/case-report/abstract 五种体裁；
  其他体裁按 task.md 的章节清单执行，未知章节保留标题并标 `partial`。
- 字数预算来自 task.md；未给出时用体裁默认值，超限只警告，不截断正文。
- 不生成或伪造基金号、伦理批号、作者贡献、利益冲突声明；统一写
  `not_provided_in_materials`。
- 不导出 DOCX 时 `deliverable.format=markdown`；需要 Word 用运行时工具另存，
  并回读校验。

## 4. 检查限制

- `scripts/task_specific.py` 检查结构、摘要数字子集、引用编号/条目、数字句绑定、
  因果措辞、声明字段与占位符；它不能证明论证质量或领域正确性。
- 检查失败不阻塞主稿落盘：`status` 降为 `partial`，问题写入
  `manuscript-audit.json` 与 `uncertainty`，作者可继续编辑。
- 自动冲突检测只识别材料中的显式冲突措辞（conflict/inconsistent/矛盾等）；
  隐含数值冲突需人工复核。
- 本 skill 不访问网络、不查询 DOI 真实性、不调用外部文献数据库。

## 5. 降级矩阵

| 缺失项 | status | 正文 | result |
|---|---|---|---|
| 材料齐全、模型稿、结构完整 | completed | 完整可编辑稿 | 四字段齐全、路径真实 |
| 部分章节无材料 | partial | 缺项章节写“材料未提供” | `uncertainty` + `not_supported` |
| 无参考文献 | partial | 参考文献节明确为空 | 不生成引用 |
| 无任何数值 | partial | 结果/结论写无法数值支持的边界 | `preserved_facts=[]` |
| 只有骨架、无模型稿 | partial | `PARTIAL SCAFFOLD` | 骨架标记进 `uncertainty` |
| 无 case/task | input_error | 不生成正文 | `deliverable=null`，说明原因 |
| 入口异常 | partial | 已有正文保留 | 异常文本写入 `uncertainty` |

## 6. 最小重跑

```bash
python3 "$SKILL_DIR/scripts/main.py" prepare  --input /input --work /app/work --output /app/output
# 模型把正文写到 /app/output/manuscript.md 或 /app/work/draft.md
python3 "$SKILL_DIR/scripts/main.py" assemble --input /input --work /app/work \
  --output /app/output --draft /app/work/draft.md
```

`assemble` 可重复运行，幂等重写 `source-map.json`、`change-log.json`、
`manuscript-plan.json`、`manuscript-audit.json` 与 `result.json`；
`manuscript.md` 只在显式 `--draft` 时替换。
