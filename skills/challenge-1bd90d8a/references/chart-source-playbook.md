# 源数据图表参考

配合 `templates/chart-spec.json` 和 `scripts/check_chart_result.py` 使用。
**task.md 与数据表优先**；图上的每个点都要能回到源表行列，或标 `estimated`。

## 1. 数据索引

每列记 `name / unit / role (x|y|group|error|n)`；先检查：

- x 是否递增/分类是否完整；
- 缺失标记（NA、-、空）与异常值；
- 单位是否统一；误差列是 SD/SEM/CI；
- 行区间与筛选条件。

## 2. 序列表

```json
{"name":"Catalyst A","x":[300,350,400],"values":[72.1,84.3,91.0],
 "error":{"type":"SD","values":[2.1,1.8,2.4]},"n":3,"unit":"%",
 "source":{"file":"materials/results.csv","locator":"rows 2-4, cols C-E"}}
```

- 每条序列有 `source`（文件 + 行/列范围）。
- 只有图没有表：读数 + `estimated: true` + 读数误差；不伪造原始表。
- 缺失点不画/不插值，写 `uncertainty`。

## 3. 图表选型

| 目的 | 图型 |
|---|---|
| 分类比较 | 分组柱/条形/箱线 |
| 时间/剂量趋势 | 折线/散点 |
| 分布 | 箱线/小提琴/直方 |
| 相关 | 散点 |
| 矩阵 | 热图 |

不因好看选 3D、双轴或彩虹色；双轴只在材料明确要求时用并标注。

## 4. 轴、误差、显著性

- 两轴有 label + unit；柱状图 y 从 0 起；对数轴显式标注。
- 误差线标 SD/SEM/CI 与 n；没有误差数据不画。
- 显著性只在材料给出时画，并注明检验与比较对象。
- 数值不改精度、不四舍五入到“好看”。

## 5. 配色与版式

- Okabe–Ito 八色：`#E69F00 #56B4E9 #009E73 #F0E442 #0072B2 #D55E00 #CC79A7 #000000`。
- 颜色 + 标记/线型双重区分；灰度可辨；不用红绿对立。
- 字号 ≥7 pt（按最终印刷尺寸）；图例不遮数据；面板 `(a)(b)` 统一。
- 期刊预设优先：Nature 单栏 89 mm/双栏 183 mm、正文 7 pt；有 `journal` 用预设。

## 6. 数值回读

生成后逐点核对：

```json
{"all_match":true,"checked_points":9,"mismatches":[]}
```

`series_map` 写每条序列的 panel/source/values/error；value_check 不通过就修映射重绘，
不要“差不多”。

## 7. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| 图值与表值不符 | 抽检偏移 | 查映射/排序 |
| 轴无单位 | 只有数字 | 补单位 |
| 误差线无类型 | 有棒无说明 | 标 SD/SEM/CI+n |
| 双轴误导 | 缩放夸大差异 | 单轴/分面 |
| 颜色不可辨 | 仅红绿 | 加线型/标记 |
| 截断 y 轴 | 非零起点夸大 | 从零或注明 |

## 8. 交付前检查

- [ ] 每个点可追到源表行列或标 estimated。
- [ ] `value_check.all_match=true`，抽检点与表值一致。
- [ ] 误差类型/n/单位齐全。
- [ ] 轴标签与范围真实，未截断。
- [ ] SVG 可编辑、PNG/PDF 真实。
- [ ] `check_chart_result.py` 返回 `ok: true`。
