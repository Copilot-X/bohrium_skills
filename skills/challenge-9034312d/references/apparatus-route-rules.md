# 装置图 / 技术路线图规则

本文件只写“从材料到图”的判定与绘制规则。输出字段见
`references/output-contract.md`，接口见 `templates/spec.schema.json`。

## 1. 先判模式

| 材料特征 | 模式 | 证据形态 |
|---|---|---|
| 有实物部件、管路/线路、介质、工况参数 | `apparatus` | 组件 + 连接 + 工况 |
| 有阶段、输入输出、判据、交付物 | `route` | 阶段 + 决策 + 终点 |
| 两者都有（题面常见） | `both` | 两个 panel：`(a)` 装置 / `(b)` 路线 |

判据：**画的是“东西怎么接”还是“事情怎么推进”**。两者都有就分栏，
不要硬塞进一张单层流程图。

## 2. 装置模式

### 2.1 实体
- 只画材料中确认的部件。清单没有的泵、换热器、阀门不得“补常规件”。
- 同类部件用同一形状；编号 `A1…An` 与材料顺序一致。
- 每个部件必须有 `label` 和 `source {path, locator}`；材料只给范围时原样写
  范围，不取中值冒充实测。

### 2.2 连接、介质、端口、流向
- 每条连接必须有 `from`、`to`、`medium`、`direction`、`source`。
- `from -> to` 就是介质实际流向；`direction` 只描述材料表述：
  `forward`（明确正向）、`reverse`（明确反向，需在 spec 中仍按实际流向填写
  from/to）、`undetermined`（材料未定，画虚线双箭头并写 uncertainty）。
- 端口名（`from_port` / `to_port`）有材料依据才写；写了就会在 SVG 中渲染为
  端点小字，并进入 `data-from-port` / `data-to-port`。
- 不允许断头边：`from`/`to` 必须是已声明节点。分流的每一条支路都要有节点
  或明确的 waste/vent 出口。

### 2.3 工况
- 温度、压力、流量、浓度、GHSV 等挂在对应部件（`location`）上。
- `params` 保留材料单位与写法；范围写范围。
- 工况有独立 `<g id="P…">` 引出线标注，并在 `conditions[]` 与 `content_map`
  中带来源。

### 2.4 布局
- 默认 `direction: "LR"`：进料在左，产物/废料在右，正交折线。
- 主链优先；并行支路同 rank；回流用 `feedback: true` + 虚线。
- 图例只列实际出现的线型/介质类别。
- 字号 ≥ 7 pt；不用阴影/渐变/立体；灰度打印仍能靠线型区分。

## 3. 路线模式

### 3.1 阶段
- 每个阶段有 `label`、`source`；建议写 `inputs` / `outputs` / `method`，
  没给就不写，不虚构。
- 阶段顺序来自材料编号或明确措辞；按 `order` 自上而下。
- 每阶段必须有输入/输出语义或明确的方法动作；空阶段不要画。

### 3.2 决策
- 决策是菱形节点（`shape: "diamond"`），同时在 `decisions[]` 声明：
  `after`、`question`、`yes`、`no`、`criterion`、`criterion_source`。
- `yes` 走主线，`no` 走侧支；回流边用 `feedback: true`，虚线。
- 判据必须来自材料；材料未给判据时写 `criterion_source: "not_provided"`，
  在 `uncertainty` 说明，不得编阈值。
- 决策节点缺失时 `svg_gen.py` 会按声明生成菱形和 yes/no 边；
  但 `decisions[]` 仍是判据的唯一来源。

### 3.3 终点
- 终点是真实交付物/里程碑（椭圆），不是“结束”空框。
- 路线可以没有决策（纯线性），但不能把并列方法伪装成先后阶段。

## 4. 来源与证据

- 每个 element / connection / condition / decision 都要有：
  `source: {path, locator}`，`path` 必须是 `/input` 下真实文件。
- `locator` 用材料中的章节、图号、段落或端口表编号（如 `§2.3`, `Fig.1b`,
  `§4 K6`）。
- 材料未提供的部件/参数/阈值：不画、不猜；写入 `uncertainty`。
- 明确推断的关系可标 `kind: "inferred"` / `direction: "undetermined"`，
  但必须虚线并在 exceptions/uncertainty 中说明；不能悄悄当事实。

## 5. 双模式

- `mode: "both"` 必须给两个 panel；`svg_gen.py` 把它们合成为一张 SVG，
  各 panel 保留独立命名组与标题。
- 两 panel 共用图例语义、字号和画布宽度；装置 `(a)` 在前，路线 `(b)` 在后。
- 总高度不足时脚本会自动抬高到每 panel ≥45 mm；这属于排版修正，会写入
  `exceptions`。

## 6. 交付前闸门（audit.py）

| 编号 | 检查 | 通过标准 |
|---|---|---|
| C1 | SVG XML | 可解析、有 viewBox |
| C2 | 文字 | `<text>` 数量 >0，无 `foreignObject` |
| C3 | 自包含 | 无 http/file 外链、无 script |
| C4 | 元素覆盖 | 每个组件/阶段/决策有命名组 |
| C5 | 连接覆盖 | 每条连接有命名组，端点存在 |
| C6 | 来源 | 每项有来源且 path 可在 `/input` 解析 |
| C7 | 工况/判据 | 工况有参数与位置；判据有来源或 not_provided |
| C8 | 流向 | 方向合法、介质非空、无断头 |
| C9/C10 | 导出 | PDF `%PDF`、PNG 魔数与像素尺寸有效 |

任一失败：保持真实产物，`status=partial`，把失败项写进 `exceptions`。
不要通过删元素、删连接来“提高覆盖率”。

## 7. 常见失败模式

| 信号 | 原因 | 修复 |
|---|---|---|
| 部件名与材料对不上 | 用了领域常识补全 | 回到材料逐条列出 |
| 箭头反向 | 把 from/to 当阅读顺序 | from/to 只表示介质流向 |
| 断头箭头 | 分流/废料没有节点 | 补真实节点或删除该连接 |
| 工况悬空 | location 指向不存在部件 | 改正 location 或删标注 |
| 判定无判据 | 编了阈值 | 写 not_provided 并标 uncertainty |
| 只有截图 | 导出被当成源文件 | 保留 SVG，重新真实导出 |
| 图上多出“常见”设备 | 凭经验补件 | 删除材料外元素 |
