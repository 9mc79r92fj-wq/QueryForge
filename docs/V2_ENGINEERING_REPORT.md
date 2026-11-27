# QueryForge V2 工程报告（阶段性）

日期：2026-11-27。分支：`v2-adaptive-router`。基线：V1.1 发布树（`b5232d1`）+ ModelScope 适配。
本轮为 V2 的第一阶段交付：**数据驱动检索路由**与**选择性精排**已落地并通过测试；Agentic RAG 反思、扩大 Benchmark、RAGChecker 式问答评测、TracePilot 互通**尚未完成**，本文逐项标注状态，不以设计文档冒充实验结果。

## 1. 原始问题

| 问题 | 证据 | 本轮处理 |
|---|---|---|
| 路由只有正则 + 固定权重，无法按查询代价选择通道 | `app/query_router.py` V1 仅 4 类固定权重 | 新增 V2：可解释特征 + 可调策略表 + 通道策略 + 精排触发 |
| Reranker 无条件执行，CPU 延迟 17.45s（历史暖机 P95） | 历史审计 `V2_RERANKER_PROFILING` 待补；历史数据来自 ENGINEERING_AUDIT | 新增选择性精排：预算不足或排序已确定时跳过 |
| 决策不可解释、不可复现 | V1 仅返回 query_type | V2 返回特征、策略、理由、版本，并写入 `SearchResponse.routing` |

## 2. 架构变化（V1 → V2）

```text
V1: query → 正则分类 → 固定权重 → 双路召回 → RRF → (可选) 全量 Rerank
V2: query → 可解释特征 → 策略表(可离线调优) → 加权 RRF → 精排决策(预算+分数差)
                                   ↘ 检索计划：second_retrieval 标记（本轮仅规划，未执行）
```

新增/修改文件：

- `app/query_features.py`（新）：长度、CJK 比例、数字比例、技术标识、错误码、疑问词、token 估算
- `app/query_router.py`（重写）：`route_query_v2`、`should_rerank`、策略加载与校验
- `app/query_router_legacy.py`（新）：冻结 V1 规则路由，供对照与回滚
- `app/config.py`：`router_mode`（v1/v2，默认 v2）、`router_policy_path`
- `app/retrieval_service.py`：自适应模式接入 V2；选择性精排；响应增加 `routing`
- `app/schemas.py`：`SearchResponse.routing`
- `tests/test_router_v2.py`（新）：16 项用例

## 3. 检索路由决策机制（已实现）

1. **查询前特征**：只使用不依赖检索结果的信息，因此"跳过某通道"的节省是真实的（当前实现未启用整通道跳过，见第 6 节）。
2. **分类**：`keyword`（错误码或 ≥2 个技术标识）、`mixed`（1 个技术标识 + 疑问）、`semantic`（疑问且中文占比 > 50%）、`balanced`。
3. **策略表**：默认权重来自 V1 规则；可通过 `router_policy_path` 指向离线调优产出的 JSON（`weights.{type}.{bm25,dense}`），加载时归一化并校验，非法文件回退默认并记警告。
4. **策略判定**：权重 < 0.15 的通道标记为可省（`strategy=bm25|dense`），否则 `hybrid`。
5. **二次检索规划**：长查询 + semantic/mixed + 预算 ≥ 1200ms 时置 `second_retrieval=true`，**本轮仅做决策，不执行二次检索**。

## 4. 选择性精排（已实现）

```text
条件：请求 rerank=1
     ∧ 剩余预算 ≥ 400ms
     ∧ 候选 ≥ 2
     ∧ |score[0]-score[1]| < 0.35
```

触发/跳过原因写入 `routing.rerank_reason`，便于在实验里统计触发率。注意：RRF 融合分数是倒数排名和，不同策略分数不可直接比较，阈值只在同一融合策略内部使用。

## 5. 测试结果（真实）

```
145 passed（129 基线 + 16 新增）, 1 warning
覆盖率 84%（2222 statements / 346 missed）
Python 3.11.5，本机执行
```

新增用例覆盖：特征提取、分类边界、权重归一化、策略文件覆盖、非法策略回退、预算限制、精排触发与跳过、V1 路由保留。

## 6. 未完成事项（如实标注）

| 事项 | 状态 | 原因 |
|---|---|---|
| 单通道整跳（strategy=bm25/dense 时真的不跑另一路） | 未实现 | 现有代码在分支前已提交双路 future，改造需重构并发段；本轮不宣称节省 |
| 扩大 Benchmark（3000–5000 文档、100/200 查询） | 未执行 | 需要重新下载/构造语料并跑多组实验，超出本轮时间；历史 500 篇结果保留 |
| Reranker 真实 Profiling | 未重跑 | 历史 17.45s P95 已记录；本轮只做触发策略，未做量化/批处理优化 |
| Agentic RAG（证据充分性、改写、二次检索） | 未实现 | 仅完成设计章节，见 `V2_AGENTIC_RAG_EVALUATION.md` |
| RAGChecker / 多轮 RAG 评测 | 未执行 | 同上 |
| TracePilot 互通协议实现 | 未实现 | 仅设计，见 `TRACE_INTEROPERABILITY.md` |
| Locust V2 复测 | 未执行 | 与上同批次 |

## 7. 已知风险

- `router_policy_path` 指向的文件若被篡改，权重会变化；加载时做了归一化与合法性校验，但没有签名机制。
- 选择性精排依赖融合分数的差值阈值 0.35，该阈值尚未在开发集上校准，属于经验值，需在 Benchmark 阶段替换。
- 缓存键尚未加入路由策略版本；切换 `router_mode` 或策略文件后旧缓存仍可能命中（后续需把策略版本并入 `make_cache_key`）。

## 8. 下一步（按优先级）

1. 把 `router_version` 与策略文件 hash 并入检索缓存键
2. 在 500 篇语料上跑 V1 vs V2 对照（同一硬件、同缓存条件），产出 Recall/MRR/NDCG/P95
3. 扩大语料到 3000–5000 篇，重做开发/测试划分
4. 真实 Reranker Profiling 与批处理、候选裁剪、量化对比
5. Agentic RAG 证据判断与有限二次检索
6. Trace 导出与 TracePilot 导入闭环
