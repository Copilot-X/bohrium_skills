# 文献翻译规则

## 1. 段落与结构

- 以 `work/segments.json` 的 `Pxxx` 为单位，一段一译；不合并、不拆分、不重排。
- 标题、图注、表注、脚注、参考文献条目都是独立段落，留在原位。
- 跨页/跨栏被切碎的同一段，在 `bilingual.md` 中合并到同一个 `Pxxx`，
  并在 `result.uncertainty` 写明合并依据。

## 2. 事实保真

- 数字：值、有效数字、范围、百分数、样本量、年份不变；
  `1.2万 → 12,000` 只做等值展开；`°C/%/±/mM/mm/MPa` 原样，不擅自换算。
  检查器按数值多重集比较 `numbers.missing/extra`，等值展开也会被记录，
  需要在 `translation-notes.json` 说明。
- 引用：`[12]`、`[1, 2]`、`(Smith et al., 2020)`、`\cite{key}`、
  `[@key]` 的签名与次数不漂移；方括号内空格/逗号差异会归一化，
  编号或 key 变化会被判为 missing。
- 图表/公式编号：`Fig. 1/Figure 1/图 1` 归一为 `figure:1`，
  `Table/表` → `table`，`Eq./Equation/式` → `equation`；
  kind 或编号改变（如 `Table 1 → Table 2`）会被判为 missing。
- 公式、代码、占位符、DOI/URL、HTML 标签不改；代码块只译注释。
- 否定与情态：否定词存在与范围不变；`may < suggest < show < prove` 不升级；
  `associated with` 不改 `causes`。中文 `不同/未来/无机/不仅` 不是否定，
  检查器已排除这些常见假阳性；`表明/显示/说明` 视为中性陈述，
  不作为强断言升级。

## 3. 术语

1. 从标题、摘要、正文、图注与结论中抽取领域术语，建立 `work/glossary.json`；
2. 每条给 `source`、`target`、可选 `aliases`；也接受
   `{"源词": "译词"}` 或 `{"源词": {"term": "译词"}}` 映射；
3. 全文只使用 `target`/`aliases`，标题、正文、图注、结论一致；
4. 缩写首次出现按原文展开；原文没展开的不擅自补全称；
5. 术语表与原文冲突：以术语表为准，记录冲突与人工确认项；
6. 没有带 target 的术语表时，`glossary.locked=false`，结果只能
   `status=partial`，并在 uncertainty 写 `glossary_not_locked`。

## 4. 对齐与可核验

1. `bilingual.md` 的 `## Pxxx` 是首选对齐键；标题可带后缀
   （`## P004 摘要`），Target 可跨多行，表格行保留换行。
2. `translation.md` 也可用 `## Pxxx` 标题；没有标题时只有
   「空行块数 = 段数」才按顺序对齐。
3. 块数不一致时不做顺序错位填充；保留空 Target 并写
   `alignment_issues`，避免把 A 段译文贴到 B 段。
4. 每段在 `translation-check.json` 的 `entries[]` 中保留
   `locator/source_locator`，`evidence_map` 可直接回到原文。
5. 不确定译法写入 `work/translation-notes.json` 的 `flags`
   （`segment_id/issue/source_span/resolution/needs_human_review`），
   finalize 会复制到 `output/translation-notes.json`。

## 5. 语域

- 英文避免 `don't/can't` 等缩写；人称、时态、英式/美式全文一致。
- 中文避免翻译腔；长定语拆成短句，但不得改变逻辑关系。
- 不添加原文没有的解释、数据或结论；译者注用 `> **[译注]**：...` 与正文分开。

## 6. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 整段改写/摘要化 | 信息丢失 | 回到 `Pxxx` 原文逐句核对 |
| 术语前后不一致 | 可读性/专业性下降 | 用 `glossary.json` 回填并重跑 |
| 未写术语表/无 target | 术语不可验证 | 补 `glossary.json`；否则只能 `partial` |
| 数字/单位被换算 | 事实错误 | 恢复原值；只允许等值展开 |
| 引用编号漂移 | 证据链断裂 | 保留原编号与位置 |
| 图表/公式编号漂移 | 交叉引用失效 | 恢复 kind 与编号 |
| 否定/情态被加强 | 结论越界 | 恢复 `may/suggest` 等等级 |
| 漏段/重复段 | 覆盖不完整 | 对照 `segments.json` 检查 |
| 双语块数不一致仍强行顺序填充 | 段落错位 | 保留空 Target，写 `alignment_issues` |
