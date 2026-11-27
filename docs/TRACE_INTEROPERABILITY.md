# QueryForge ↔ TracePilot Trace 互通（设计，未实现）

## 状态

**本轮未实现导出与导入。** 本文定义协议草案，等双方代码就绪后再落地。

## 传输方式

- QueryForge 导出单个 JSON 文件（一次请求或一次会话），字段版本化 `schema_version`
- TracePilot 通过文件导入，不做运行时强耦合
- 两项目仍独立部署，不共享数据库

## 字段草案

```json
{
  "schema_version": "qf-trace-1",
  "trace_id": "uuid",
  "session_id": "uuid|null",
  "created_at": "ISO8601",
  "query": "脱敏后的用户问题",
  "route": {"version": "2.0.0", "query_type": "semantic", "strategy": "hybrid",
             "bm25_weight": 0.25, "dense_weight": 0.75, "rerank": false},
  "nodes": [
    {"id": "n1", "type": "retrieval.bm25", "status": "ok", "latency_ms": 3.2,
     "tokens": 0, "output_digest": "sha256:..."},
    {"id": "n2", "type": "retrieval.dense", "status": "ok", "latency_ms": 16.1},
    {"id": "n3", "type": "fusion.rrf", "status": "ok", "latency_ms": 0.2},
    {"id": "n4", "type": "generation.llm", "status": "ok", "latency_ms": 1200,
     "tokens": {"input": 1518, "output": 60}},
    {"id": "n5", "type": "citation.verify", "status": "ok"}
  ],
  "edges": [{"from": "n1", "to": "n3"}, {"from": "n2", "to": "n3"},
            {"from": "n3", "to": "n4"}, {"from": "n4", "to": "n5"}],
  "result": {"refused": false, "citations": ["S1"], "ttft_ms": 640}
}
```

## 脱敏要求

- 用户问题可选保留（按开关），必须剥离 API Key、Cookie、Authorization
- 文档正文只保留摘要与 chunk ID，不导出全文
- 不记录模型凭据与管理员令牌

## 闭环目标

```
QueryForge 真实请求 → 导出 trace.json → TracePilot 导入
→ DAG 展示 → 故障归因 → 修复验证 → QueryForge 回归
```

本轮只完成协议设计，未做实际导入验证。
