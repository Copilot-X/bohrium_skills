# result.json 字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `case_id` | string | 与输入一致 |
| `status` | string | completed / partial / input_error |
| `deliverable` | object | `{path, format, exists}`；path 指向 cited manuscript |
| `preserved_facts` | array | 数字/引用 token + source/locator |
| `evidence_map` | array | insertion → claim/anchor/source |
| `change_log` | array | 实际完成的步骤 |
| `artifacts` | array | 所有真实产物路径 |
| `result` | object | summary + audit |
| `result.audit.insertions_total` | int | 计划插入数 |
| `result.audit.applied` | int | 成功插入数 |
| `result.audit.blocked` | array | 锚点/条目缺失的插入 |
| `result.audit.dedupe` | array | 合并/保留决策 |
| `result.audit.final_numbers` | array | 正文最终编号 |
| `result.audit.entry_numbers` | array | 文末列表编号 |
| `result.audit.missing_numbers` | array | 正文有、列表无的编号 |
| `result.audit.unknown_citation_tokens` | array | 无对应条目的引用 |
| `result.audit.duplicate_keys` | array | 重复文献 key |
| `output_contract` | object | 输入契约原样保留 |
| `contract_coverage` | array | 每个契约字段是否满足 |
