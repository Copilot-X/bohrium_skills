# 学术规范与合规检查：执行规则（v1）

## 1. 任务口径

- 目标：学术规范、引用/数据/伦理/利益冲突与合规检查。
- 主产物：`output/review-report.md`。
- 必填字段：`compliance_checklist`, `compliance_matrix`,
  `guideline_source`, `review_status`, `findings`, `evidence`。
- 专项脚本：`scripts/compliance_check.py` 负责抽取声明、建立矩阵、
  生成 findings/uncertainties；模型负责复核、补全与写报告。
- 不负责：论文润色、代写代投、数据重新分析、实验方案设计；不提供
  法务、伦理委员会或目标期刊编辑的正式意见。

## 2. 状态口径（硬规则）

| 状态 | 含义 | 不意味着 |
|---|---|---|
| `pass` | 输入材料中有对应声明，且在输入范围内未发现缺口 | 不表示机构认证或外部事实为真 |
| `issue` | 该检查项适用，但声明缺失，或声明与材料直接矛盾 | 不推断作者实际未做审批/未取得同意 |
| `needs_recheck` | 有声明但不完整、范围不明，或需批件/同意书/许可/指南原件核对 | 不表示违规已成立 |
| `not_checked` | 输入不足（无查重语料、无期刊指南等），无法判断 | **不等于合规** |
| `not_applicable` | 材料明确说明不适用（如“不涉及人或动物”），且无相反指标 | 不表示其他合规项也通过 |

**未报告 ≠ 合规；未检查 ≠ 通过。** 报告与 `result.json` 必须保留
这个区分，不能把缺失项写成“符合规范”。

## 3. 检查项与规则编号

| check_id | rule_id | 必查内容 | 通过所需证据 | 常见缺口 |
|---|---|---|---|---|
| `ethics_approval` | CMP-ETH-01 | 涉及人或动物研究是否有伦理审批/豁免 | 委员会名称、批件编号、日期、覆盖范围 | 仅写“approved by IRB”但无编号/委员会 |
| `informed_consent` | CMP-CON-01 | 人研究是否说明知情同意或豁免 | 谁、何时、何种形式；图像发表范围 | 同意声明未覆盖可识别照片/影像 |
| `data_availability` | CMP-DAT-01 | 数据/代码是否可获取 | 仓库/DOI/登录号/补充材料/受限访问流程 | 仅写“upon request”；受限但无申请流程 |
| `conflict_of_interest` | CMP-COI-01 | 利益冲突声明是否完整 | 逐项披露或无冲突声明 | “无利益冲突”与商业资助/企业参与冲突 |
| `funding` | CMP-FUN-01 | 基金、资助方与项目编号 | 资助方全称 + 项目编号/范围 | 只写“supported by a grant” |
| `author_contributions` | CMP-AUT-01 | 作者贡献与责任分配 | 逐人角色（CRediT 或等价） | 只有“contributed equally” |
| `acknowledgments` | CMP-ACK-01 | 致谢与商业关系 | 致谢文本；被致谢者同意情况 | 企业员工协助编辑/写作但未披露 |
| `duplicate_publication` | CMP-DUP-01 | 重复发表/预印本/会议版/学位论文披露 | 先前版本引用、增量与许可 | 披露了却未给引用/许可 |
| `plagiarism` | CMP-PLG-01 | 抄袭/查重可核验性 | 查重报告或可比对语料 | 未提供外部语料，无法判定 |
| `text_reuse` | CMP-PLG-02 | 输入材料内部文字复用 | 跨文件逐字重复句 + 位置 | 可能是方法模板/政策文本，需人工排除 |
| `copyright_license` | CMP-CPY-01 | 图/表/代码/数据来源与许可 | 来源 + 许可类型 + 适用范围 | 复用第三方图但无许可声明 |
| `privacy` | CMP-PRV-01 | 可识别个人信息与受限数据 | 去标识化/授权/访问控制说明 | 可识别人物图像无发表授权 |
| `submission_requirements` | CMP-SUB-01 | 目标期刊/会议要求 | 指南版本/日期 + 逐条映射 | 只给期刊名，无指南文本 |
| `reporting_checklist` | CMP-REP-01 | 报告规范/清单 | 设计与清单引用（版本） | RCT/系统综述/动物实验未提清单 |

规则来自本题检查项与通用学术规范，不是对某一期刊条款的复制。具体
判定必须绑定输入提供的指南版本/日期；没有指南时只能写“无法判定”。

## 4. 证据规则

1. 每条 finding 必须有 `path + locator + quote`；quote 必须逐字来自
   输入材料，允许空白折叠与省略号分段，不允许改写或翻译。
2. `locator` 至少给出段落号（`P3`）或行号（`L12`）；矩阵 evidence
   同时保留 `line_start/line_end` 与 `section`。
3. 缺失类 finding 使用“负向检索”：
   - `absence_check.scope` 写检索范围（例如 `all supplied material`）；
   - `absence_check.patterns_checked` 写检索词/规则；
   - `absence_check.matches` 写真实命中数（通常 0）；
   - 选择声明章节标题或稿件标题作为 `absence_search_anchor`；
   - `false_positive_check` 说明未命中不等于不存在。
4. 同一材料的不同格式/版本不构成独立证据；不得用“常识”补全材料。
5. 不得编造审批号、同意范围、DOI、许可、期刊条款、数据或运行记录。

## 5. 误报抑制

判定前至少核对以下一项，并写入 `false_positive_check`：

- **学科/期刊惯例**：不同期刊对声明位置与格式要求不同；没有指南时
  只报“未报告/无法核验”，不报“违规”。
- **合法豁免/受限**：知情同意豁免、受限数据、预印本披露本身可能是
  合规行为；问题应是“未说明依据/范围”，不是“存在即违规”。
- **风格差异**：引用格式、章节命名、贡献声明写法差异不作为学术不端。
- **模板文本**：方法模板、伦理声明模板、政策文本可能逐字重复；
  内部文字复用只作 `needs_recheck` 信号，不作抄袭结论。
- **非人图像/联系人**：通讯作者邮箱、机构地址不自动视为受试者 PII；
  只有与患者/受试者关联的可识别信息才报隐私风险。
- **图像内容未读**：图片本身需人工查看；无文字层 PDF 的未读区域
  不能写成“已检查”。

无法排除时：`status=needs_recheck`，并把原因写入 `uncertainties`。

## 6. 严重度锚点

| 级别 | 适用 |
|---|---|
| `critical` | 涉及人研究缺伦理/同意，或可识别个人信息的直接暴露 |
| `major` | 缺关键声明、声明与材料矛盾、第三方材料无许可、数据不可核验 |
| `minor` | 声明存在但信息笼统、可选声明缺失、内部文字复用信号 |
| `info` | 建议性改进，不影响合规判断 |

## 7. 人工复核边界（必须写进报告）

- 本工具与报告不替代机构审查、伦理委员会、法务或期刊编辑意见。
- 声明存在性来自关键词/段落规则；未命中不等于不存在。
- 不联网、不查重、不核验批件/文献/DOI/期刊指南真伪。
- 未提供批件、同意书、许可、指南或查重语料时，只报待复核/未检查。
- PDF/扫描件无文字层、图片内容与补充材料需人工查看。
- 材料缺失、无法判断与运行失败必须分别说明，不能合并成“通过”。

## 8. 脚本协议

```bash
python3 "$SKILL_DIR/scripts/compliance_check.py" \
  --input /input --work /app/work --output /app/output --merge-work
```

产物：

- `output/compliance-matrix.json`：完整矩阵 + findings + uncertainties；
- `output/compliance-findings.json`：findings 与 uncertainties；
- `output/compliance-evidence-map.json`：扁平化的逐字证据；
- `output/compliance-check.md`：可读草案（不是最终报告）；
- `work/compliance-draft.json`：work 文件形态的草稿；
- `work/review-findings.json`：仅在加 `--merge-work` 时填充空字段，
  不覆盖模型已写内容。

`--strict` 在状态不是 `pass` 时返回 1，仅用于自测/CI；正式流程不加，
以便入口在缺材料时仍写合法 `result.json` 并退出 0。

## 9. 复跑一致性

- 规则是确定性的：同一 `/input`、同一脚本版本应得到相同的矩阵状态与
  finding 顺序；`generated_at` 除外。
- 重复运行不得引入新的实体或数值；所有 quote 应能按 `path+locator`
  回到原文。
- 自测：`python3 scripts/selftest.py`（正常、空输入、缺伦理/数据声明；
  有 `run_case.py` 时还会跑 prepare→finalize 与缺产物/缺字段回归）。
