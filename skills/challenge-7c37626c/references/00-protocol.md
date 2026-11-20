# 运行协议（Runtime Protocol）

## 0. 文件命名约定

提交包（zip / 上传目录）里全部是**真实文件名**：`SKILL.md`、`scripts/inspect_input.py`、
`templates/result_envelope.json`。

本地构建目录里出现的 `*.py.md`、`*.json.md` 只是**防加密的中间态**——本地加密软件按扩展名
加密非 `.md` 文件，所以落盘时加 `.md` 后缀，打包进 zip 时再剥掉。**运行时不需要做任何还原**，
直接按正常路径调用即可：

```bash
python3 /skills/weekly-skill/scripts/inspect_input.py /input
```

## 1. 目录约定

| 路径 | 用途 | 读写 |
|---|---|---|
| `/input/case.json` | 正式案例输入 | 只读 |
| `/input/task.md` | 任务说明（最权威） | 只读 |
| `/input/environment/resources.yaml` | 预置资源与工具清单 | 只读 |
| `/input/**` | 材料（PDF、数据、模板、图片） | 只读 |
| `/app/work` | 中间产物、索引、日志 | 读写 |
| `/app/output` | 最终交付物与 `result.json` | 读写 |
| `/skills/weekly-skill` | 本 Skill 安装路径 | 只读 |

部分题目把 Skill 装在 `/skills/literature-qa`，用 `ls /skills` 确认实际路径，不要硬编码。

## 2. 预算

- 64 次模型请求（含失败重试）
- 900 秒墙钟
- 单次响应 16384 token（含推理）
- 2 CPU / 4 GiB

策略：
1. 先做一次全局盘点（1 次请求内完成文件清单）。
2. 用脚本做确定性工作（解析、抽取、校验），把模型请求留给判断与写作。
3. 每 10 次请求检查剩余预算；低于 20% 时进入收尾。
4. 任何时刻 `/app/output/result.json` 都应是一个可提交的版本。

## 3. 工具

镜像预置（以 `resources.yaml` 为准，常见项）：

- Python 3.12.1（scientific-python：numpy / pandas / matplotlib / scipy）
- Node 22.23.1
- pdf-text-tool、pdf-render-tool、ocr-tool
- latex-compiler
- office-renderer（LibreOffice 类）
- browser-automation
- font-discovery

调用前先探测是否存在：

```bash
command -v soffice libreoffice pdftotext pdftoppm tesseract latexmk node python3
python3 -c "import matplotlib, numpy, pandas; print('sci ok')"
```

**评测阶段不安装外部依赖**。只使用预置能力；缺失时走降级路径并如实标注。

## 4. 落盘纪律

- 每完成一个阶段写一次检查点：`/app/work/checkpoint-<n>.json`（记录已完成项、待办项、耗时）。
- `result.json` 先写骨架（`case_id` + `status=partial`），再逐步填充；不要最后一次性写。
- 大文件放 `/app/work`，只把最终产物复制到 `/app/output`。
- **schema 优先级**：`task.md` 给出的输出契约最权威。技能中“参考定向融合”建议的附加字段（如 `checks`、`audit`、`*_log`）在契约允许时直接加；契约锁定字段时放入 `extras` / `notes` 或仅作内部检查项，不得挤掉、改名任何必需字段。

## 5. 可复现性

- 随机过程固定 seed（`random.seed(0)` / `np.random.seed(0)`）。
- 记录解释器与库版本到 `result.json` 的 `environment` 字段（可选但推荐）。
- 同一输入重复运行应产生同构结果；若使用了模型判断，记录判断依据。

## 6. 安全与合规

- 不读取 `/` 下与任务无关的目录。
- 不写入评分目录、不修改 `/input`。
- 不把凭据、token、个人路径写进产物。
- 引用他人材料时保留出处，不整段复制受版权保护的长文本（引文控制在支撑论断所需的最短长度）。
