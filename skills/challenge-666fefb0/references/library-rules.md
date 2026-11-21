# 文献与资料自动分类：执行规则

## 1. 任务口径

- 目标：分类体系、批量归类、边界案例与人工复核。
- 主产物：`output/library-results.json`；辅助产物 `output/library-index.json`、
  `output/operations.log.json`；可选真实目录树 `output/library/<category>/<new_name>`。
- 必填字段：`taxonomy`, `items`, `evidence`。

## 2. 分类体系（先定义，再分类）

1. `taxonomy.categories` 是稳定类别名列表，不能为空，不能重复。
2. `taxonomy.criteria` 必须为每个类别写可执行判据，说明**看哪个字段/文本**：
   - 期刊论文：期刊名、卷期、DOI、同行评审标识；
   - 预印本：标题/页脚写 preprint、arXiv、bioRxiv、Research Square；
   - 会议论文：会议名、年份、proceedings 标识；
   - 学位论文：学位授予单位、thesis/dissertation 标识；
   - 数据集：表格/数组字段可解析，非叙述性文档；
   - 补充材料：SI/附录/图表源数据；
   - 无法可靠判断：进复核队列，不以“其他”草率归档。
3. `taxonomy.threshold` 默认 0.70；`confidence < threshold` 必须进 `review_queue`。
4. 一个文件只选一个主类别；跨主题维度写入 `tags`，不要一文件多类别。
5. 类别判据不得依赖文件名关键词单一信号；至少结合标题/摘要/元数据/正文结构中的两类证据。

## 3. 逐文件条目

每个输入文件恰好一条 item，字段：

`id`, `path`, `category`, `tags`, `confidence`, `new_name`, `hash`, `review`,
`rationale`, `evidence{path,locator,quote}`。

- `path` 必须是 `/input` 下真实存在的相对路径；不得虚构文件。
- `hash` 用文件 sha256；执行器会重算核对，不匹配即失败。
- `rationale` 说明“为什么归入该类”，引用具体字段/句子，不写空话。
- 元数据缺失写 `null`/`unknown`，不补造作者、年份、DOI。
- 不可读/加密/扫描件也保留 item，`review=true`，记录可确认的元数据与失败原因。

## 4. 证据

1. 每条关键判断/分类/操作必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 5. 复核队列与筛选

- `review_queue` 覆盖所有低置信度、跨类别、不可读、冲突项；每条写
  `item_id`、`reason`、建议动作（保留原位/人工确认/补充元数据）。
- `items[].review` 与队列成员必须一致。
- 复核中的条目不得移动/改名；操作日志里不得出现针对它们的 move/rename/copy。
- 题目有筛选条件时：`filters.must` / `filters.must_not` + `filter_execution`
  （kept/excluded 计数）；未命中写 `no_result: true` 与原因，不把未找到写成排除。
- 输入没有材料：`no_result: true`，summary 说明；不生成伪分类。

## 6. 去重与版本

- `duplicate_groups`：sha256 相同，或 DOI + 标题 + 作者年份完全一致；组内至少 2 个 item。
- `version_groups`：预印本/正式版/修订版/补充材料；写 `versions`、`preferred`、
  `reason`。不同版本保留全部，不删除、不互相覆盖。
- 同名目标文件已存在时，执行器追加 `-2`，原文件不覆盖；日志记录 rollback。

## 7. 操作与回滚

- 每个移动/改名/复制记录：`action/status/from/to/sha_before/sha_after/rollback`。
- `rollback` 必须可执行（例如 `delete library/<category>/<new_name>`）。
- 失败/跳过/权限不足要记录，不把未完成写成完成。
- 幂等：同一 plan 重复运行，已存在的同哈希目标记为 `skipped_identical`。

## 8. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 以结果定义分母 | 指标虚高 | 先冻结输入清单，再计算覆盖 |
| 把未找到当不相关 | 遗漏被掩盖 | 单列 `no_result` 与原因 |
| 只给结论不给证据 | 无法复核 | 补 path/locator/quote |
| 只按文件名猜类别 | 错分 | 结合标题/摘要/元数据/正文 |
| 低置信度也直接改名 | 不可逆 | 进复核队列，保留原位 |
| 合并不同版本 | 事实错误 | 版本组保留 preferred + reason |
| 伪造在线结果 | 学术不端风险 | 只用输入材料，写缺口 |
| 静默改数据 | 结果不可复现 | 保留 input_snapshot 与变更日志 |

## 9. 执行器与产物

```bash
python3 scripts/classify_library.py --input /input --work /app/work \
  --output /app/output --materialize --json
python3 scripts/run_case.py finalize --input /input --work /app/work --output /app/output
```

- 执行器先校验，后写产物；有 problems 时退出码 1，不写误导性结果。
- `--check-only` 只诊断；`--self-test` 构造合成数据验证覆盖/复核/证据/回滚。
- `classify_library.py` 在写产物前校验 taxonomy/证据/覆盖/复核队列；
  `fam_tracks.py` 的 classification 分支在 finalize 时复核 plan 与三个产物的一致性，
  不一致降级 `partial`。
