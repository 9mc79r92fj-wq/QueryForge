# V2 Agentic RAG 与端到端评测（设计，未实现）

## 状态

**本轮未实现。** 目标链路：

```
User Query → Context Resolution → Router → Retrieval
→ Evidence Sufficiency → (Query Rewrite → Second Retrieval)?
→ Generation → Citation Verification
```

当前线上行为仍是：检索 → 生成 → 引用校验，证据不足直接拒答；没有改写与二次检索。

## 设计要点（待实现）

1. **证据充分性判断**：先用低成本规则（命中数、覆盖率、分数差、是否命中查询关键词），必要时调用 DeepSeek 做一次判断，调用计入预算。
2. **改写与二次检索**：最多一次；改写只为补全指代或补关键词，不做开放域扩展；第二轮结果与原结果合并去重后重新排序。
3. **预算**：新增模型调用必须计入 `DEMO_LLM_TOTAL_CALLS` 与 Token 上限，并在超时预算内完成。
4. **引用校验分级**：ID 存在 → 映射到真实文档 → 证据是否支持结论（第三级需要模型或人工，不能等同于格式校验）。

## 评测方案（待执行）

参考 RAGChecker（Amazon Science）与 Google Sufficient Context 的划分方式，自建可追溯样本：

| 类别 | 数量（目标） |
|---|---:|
| 单文档问答 | 30 |
| 多文档综合 | 20 |
| 精确术语/错误码 | 20 |
| 多轮指代 | 20 |
| 知识不足（应拒答） | 20 |
| 冲突证据 | 10 |
| 无关文档干扰 | 10 |
| 版本更新 | 10 |

指标：Answer Correctness、Citation Accuracy、Evidence Coverage、Faithfulness、Abstention Precision/Recall、Multi-turn Consistency、TTFT、Token Cost。

错误分层：检索失败 / 上下文构造失败 / 生成失败 / 引用失败 / 拒答失败。

标注口径：人工标签、确定性规则、LLM Judge 三类分别标注，报告时不得混称。

## 为什么不现在编数字

没有标注样本就没有真实指标；用 LLM Judge 单独评测也不能称为人工评测。本轮只记录方案，等样本与预算到位后再执行。
