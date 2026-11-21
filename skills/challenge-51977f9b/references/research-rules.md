# 系统性文献综述：执行规则

## 1. 任务口径

- 目标：系统综述协议、检索/纳排、质量评价与证据综合。
- 主产物：`output/research-report.md`。
- 必填字段：`protocol`, `screening`, `quality_appraisal`, `subquestions`,
  `coverage`, `conflict_check`, `synthesis`, `findings`, `gap_analysis`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

PRISMA 式计数闭合；纳排理由可查；质量评价有依据。

## 4. 产物

- 主产物必须非空、可编辑、可复核；
- 每个声明 artifact 必须真实存在；
- 分析类任务保留原始快照、规则/代码、日志与结果文件；
- 失败/跳过/权限不足要记录，不把未完成写成完成。

## 5. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 以结果定义分母 | 指标虚高 | 先冻结参考集/输入，再计算 |
| 把未找到当不相关 | 遗漏被掩盖 | 单列未命中与原因 |
| 只给结论不给证据 | 无法复核 | 补 path/locator/quote |
| 合并不同对象 | 事实错误 | 保留 `keep_separate` 与依据 |
| 伪造在线结果 | 学术不端风险 | 只用输入材料，写缺口 |
| 静默改数据 | 结果不可复现 | 保留原始快照与变更日志 |

## 6. 本题指标与字段

- 结论：每条有 `statement/sources/limitations/evidence`；区分观测、推断与空白。
- 证据矩阵：按维度分组比较，保留每篇的结论、条件与局限；冲突并列。
- 系统综述：PRISMA 式检索/纳排/纳入计数闭合；每个排除有理由码。
- 趋势/选题：时间窗、检索范围与数量口径明确；不把共现当因果，不夸大创新性。
- 筛选计数闭合（identified-duplicates=screened; screened-excluded=included）。
- 质量评价有工具/维度/判断/依据；综合结论引用等级；未执行检索显著标注。

## 7. 系统综述的执行顺序

1. **协议先于筛选**：先写 `protocol`（检索式、数据库、字段、日期、限制、
   纳排条款、理由码字典、版本），再动筛选记录。协议变更必须升版本并在
   `change_log` 留痕。
2. **检索式**：把问题拆成 Population/Intervention/Comparison/Outcome
   概念块；每块给同义词与受控词；用 AND 连接概念块、OR 连接同义词；
   写明字段（title/abstract/full text）与数据库语法差异。
3. **题录级筛选**：对每条记录判 `include`（进入全文）、`exclude`
   （题录级排除）、`duplicate`；排除必须有 `reason_code`。
4. **全文级筛选**：对进入全文的记录再判；全文不可得写
   `full_text_unavailable`，不能当作不相关。
5. **计数闭合**：
   `identified - duplicates = screened`；
   `screened - excluded = included`；`included <= full_text <= screened`。
6. **质量评价**：每个 included 记录一条；工具与维度对应研究设计；
   证据必须指向质量评价材料的具体行；`effect_on_conclusion` 写清该等级
   如何影响综合（降级/敏感性分析/排除）。
7. **综合**：按结局或人群分组；结论强度不得高于最弱关键证据；冲突并列，
   写裁决依据；空白必须排除“检索遗漏”。

## 8. 算例（synthetic，仅演示计数）

输入 10 条记录，2 条重复（R03→R01，R07→R02），1 条全文不可得，
3 条题录级排除，4 条全文级纳入：

| 量 | 值 | 校验 |
|---|---|---|
| identified | 10 | 输入行数 |
| duplicates | 2 | R03,R07 |
| screened | 8 | 10-2 |
| excluded | 4 | 3 题录 + 1 全文不可得 |
| full_text | 5 | 进入全文 |
| included | 4 | 8-4 |

`records` 必须出现全部 10 个 ID；重复记录判 `duplicate` 并写
`duplicate_of`；每个 included 记录必须有质量评价。若 identified=10、
duplicates=2、screened=7，预检会报“PRISMA 不闭合”。

## 9. 六面覆盖检查表

| 面 | 通过标准 | 常见失败 |
|---|---|---|
| 问题拆解 | 子问题有 status 与证据 | 只写一句研究问题 |
| 检索覆盖 | 数据库/字段/日期/限制/executed | 只给关键词 |
| 证据回链 | 每条综合结论有 record/evidence | 结论无记录 ID |
| 冲突证据 | checked=true + items + 裁决 | 假装没有冲突 |
| 综合结论 | strength 与质量等级一致 | 单篇低质量研究写 high |
| 研究空白 | basis + why_not_omission | 把未检索到当空白 |

## 10. 预检脚本

`scripts/audit_work.py` 复算 PRISMA 计数与逐条 decision，检查 included
集合的质量评价覆盖、六面覆盖、冲突检查与空白依据。退出码非 0 时先修
工作文件，不要 finalize。
