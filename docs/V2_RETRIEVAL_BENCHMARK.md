# V2 检索 Benchmark 说明

## 历史基线（V1，真实归档）

来源：`docs/benchmark_results.json`、`docs/benchmark_audit_warmed.json`、`docs/ENGINEERING_AUDIT.md`。

- 数据集：`mteb/T2Retrieval`，修订版 `921dd3af6e78d1ae7ee0368aa8d7eaee02c8f08e`，种子 42
- 规模：500 篇候选文档，10 条开发查询，30 条已观察测试查询
- 抽样方式：从带标注的候选查询中随机分组，把该查询的相关文档纳入 500 篇语料，再补随机负例

| 策略 | Recall@10 | MRR@10 | NDCG@10 | P95 ms |
|---|---:|---:|---:|---:|
| BM25 Reference | 0.8314 | 0.9067 | 0.8434 | 2.595 |
| BM25 | 0.8314 | 0.8900 | 0.8306 | 1.349 |
| Dense | 0.9608 | 1.0000 | 0.9776 | 16.569 |
| Hybrid | 0.9431 | 0.9611 | 0.9334 | 16.642 |
| Adaptive | 0.9431 | 0.9611 | 0.9343 | 16.646 |
| Adaptive + Reranker | 0.9655 | 1.0000 | 0.9775 | 15959.58 |

结论（V1 原档）：Dense 在该子集上优于 Hybrid/Adaptive；Reranker 提升约 0.47pp Recall，但 P95 约 16 秒。该结论属于受控子集，不代表完整 T2Retrieval 榜单。

## V2 本轮状态

**未执行 V2 对照实验。** 原因：本轮只完成了路由与精排策略代码，扩大语料与多策略对照需要独立的数据准备与运行窗口。为避免把旧数据冒充新结果，这里不提供 V2 数字。

## V2 实验设计（待执行）

1. 语料：3000–5000 篇，保留随机种子与文档 ID 清单
2. 划分：100 条开发查询（调权重/阈值）+ 200 条独立测试查询（只评估一次）
3. 对照：Dense Only / Hybrid / V1 Adaptive / 全量 Rerank / Selective Rerank / V2 Router+Selective Rerank
4. 指标：Recall@5、Recall@10、MRR@10、NDCG@10、P50/P95/P99、Reranker 触发率、CPU/内存
5. 统计：Bootstrap 置信区间；按查询类型分层报告
6. 约束：相同硬件、语料、缓存与候选范围；冷启动与暖机分开记录

## 相关性标注风险

T2Retrieval 的相关性标注不完整，未标注候选不能直接当负样本。V2 扩充实验必须区分"已标注相关 / 未标注（未知）/ 确认负例"，或引入人工复核子集。
