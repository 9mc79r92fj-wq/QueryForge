# QueryForge 最终交付报告

交付日期：2026-11-25。本轮在本地真实环境完成代码复验、演示语料建设、安全检查与**真实 DeepSeek 端到端验收**。**公网部署与 GitHub CI 两项因缺少外部条件（服务器、仓库权限）尚未完成**，本文逐项标注"已验证 / 未验证 / 待部署"，不把未验证项写成通过。

## 1. 项目定位

QueryForge 是面向企业知识库的单机异步 RAG 后端与自适应混合检索引擎。目标岗位为 AI 后端、RAG 工程、Agent 评测与 Python 后端。它不是分布式系统，也不是多租户 SaaS：部署形态是单台 Linux 主机上的 Docker Compose（API、Celery Worker、Redis、Qdrant）。

## 2. 最终架构

```text
浏览器 / API 客户端
   │  HTTP（同源，限流 + 并发保护 + Request ID）
   ▼
FastAPI ── SQLite WAL（文档、版本、任务、索引修订版、执行计数、演示预算）
   │  └─ IndexSnapshot（单事务固定 revision 与可见版本）
   ├─ BM25（自研倒排，jieba 分词）
   ├─ Dense（FastEmbed BGE-small-zh-v1.5 → Qdrant）
   ├─ RRF / 加权 RRF（Adaptive Router 决定权重）
   ├─ Cross-Encoder 重排（BGE-reranker-base，默认关闭）
   └─ LangChain → DeepSeek（普通问答、SSE 流式、Function Calling）
        ▲
        │  证据与引用校验
Celery Worker ← Redis（队列、版本化检索缓存、多轮会话）→ Qdrant（向量）
```

## 3. 本轮实际验证结果

| 项目 | 结果 | 证据 |
|---|---|---|
| 全量自动化测试 | **123 passed**（Python 3.11.5，Windows） | `python -m pytest -q --cov=app` |
| 语句覆盖率 | **84%**（1720 / 2049 行） | 同上，`--cov-report=term-missing` |
| 本地栈健康 | api / worker / redis / qdrant 全部 healthy | `docker compose ps` |
| 本地接口 | `/api/v1/health` 返回 `ok` | 实跑 |
| 演示栈健康 | queryforge-demo 四容器 healthy，`ready=true` | 实跑 `docker compose -p queryforge-demo` |
| 演示可见版本 | 20 个版本，`index_revision=21` | `GET /api/v1/ready` |
| 演示检索 | Dense / BM25 / Hybrid / Adaptive 均返回正确文档 | 见第 5 节 |
| MCP stdio | 握手成功，协议 2025-11-25，2 个工具，调用通过 | `python scripts/mcp_smoke.py` |
| 真实模型问答 | **已验证**（`deepseek-flash`，本轮 6 组真实场景、约 10 次上游调用） | 见第 6 节 |

## 4. 本轮改动

| 文件 | 说明 |
|---|---|
| `scripts/seed_demo_corpus.py` | 新增。内置 19 篇公开技术文档，写入 `examples/demo/kb/` 后走真实上传 API 入库并轮询任务；重复执行按 SHA-256 去重，可安全重跑。 |
| `examples/demo/kb/*.txt` | 新增 19 篇演示语料（缓存、Celery、Qdrant、HNSW、BM25、Dense、RRF、Adaptive、Reranker、RAG、Function Calling、MCP、限流降级等），每篇附公开来源。 |
| `app/web/index.html` | 检索实验室默认策略改为 Dense，并在说明中解释 Adaptive 的路由含义。 |
| `.env.demo`（未入库） | `DEMO_PUBLIC_VERSION_IDS` 从 1 个扩展为 20 个，仅管理员批准的版本对公网可见。 |
| `docs/FINAL_DELIVERY.md` | 本文件。 |

其余未提交的审计改动（`app/access.py`、`app/system_routes.py`、`app/web/`、`docker-compose.demo.yml` 等）已作为检查点提交到本地 Git：`53f9a69 chore: checkpoint audited release candidate and demo corpus tooling`。

## 5. 检索实测（演示栈，20 篇语料）

| 查询 | 策略 | 首条结果 | 分数 | 延迟 |
|---|---|---|---:|---:|
| Redis AOF 持久化 | Dense | public-aof.txt | 0.5023 | 30.4 ms |
| 缓存穿透 布隆过滤器 空值缓存 | BM25 | cache-failure-modes.txt | 17.75 | 5.0 ms |
| 缓存雪崩 大量键同时失效 | Dense | cache-failure-modes.txt | 0.6595 | 16.1 ms |
| HNSW 的 M 与 ef_search 如何影响召回率 | Adaptive | hnsw-index.txt | 0.0164 | 17.6 ms |
| Celery acks_late 消息确认与重试 | BM25 | celery-acks-retries.txt | 14.66 | 3.0 ms |
| RRF 融合排名 k 值 | Hybrid | rrf.txt | 0.0328 | 16.0 ms |

说明：融合策略的分数是 RRF 倒数排名和，与单路分数的量纲不同，**不能跨策略比较数值大小**，只能比较排序。

历史消融实验与压测原始数据未改动，仍为：Dense 在 T2Retrieval 子集上优于 Hybrid/Adaptive（Recall@10 0.9608 对 0.9431），Reranker 精度略高但 P95 约 17.45 s。详见 `docs/ENGINEERING_AUDIT.md`、`docs/benchmark_results.json`、`docs/benchmark_audit_warmed.json`、`docs/loadtest/`。

## 6. 模型能力验证：已验证

密钥由用户在本机 `.env` 配置，仅输出布尔状态，未读取或记录明文。容器内 `get_settings()` 读取结果：`llm_api_key_present = True`。

| 配置项 | 实际值 |
|---|---|
| LLM_MODEL | `deepseek-flash` |
| LLM_BASE_URL | `https://api.deepseek.com` |
| 调用参数 | `max_tokens=768`、`timeout=30s`、`max_retries=1`（local）/ `0`（demo）、`thinking.type=disabled` |

厂商 `/models` 接口返回 `['deepseek-flash', 'deepseek-v4-pro']`，配置名与端点匹配。测试使用本地知识库（revision 5，5 个可见版本：`redis_test.txt`、`queryforge_manual_utf8.txt` 两版、`rag_test.txt`、`qf_smoke.txt`）。

### 6.1 普通 RAG 问答

问："Redis 支持哪些持久化机制？"，`stream=false`。

- `refused=false`，`retrieval_mode=adaptive`，`index_revision=5`，`latency_ms=1695.871`
- 答案："Redis 支持 AOF 和 RDB 持久化机制。[S1][S2]"
- 引用 2 条，均映射到 `queryforge_manual_utf8.txt` 的两个版本片段（chunk_index 0）

### 6.2 SSE 流式与引用校验

问："Redis 支持哪些持久化机制？"，`stream=true`：24 个事件（meta、retrieval、21 个 delta、final）。

- `refused=false`，最终答案 [S1][S2]
- `ttft_ms=547.187`，`llm_ttft_ms=533.598`
- `usage={input_tokens: 660, output_tokens: 21, total_tokens: 681, cache_read: 512}`
- 引用 2 条，chunk_id `a92eeaee…`、`f3561f20…`

反向验证：问"AOF 和 RDB 的主要区别是什么？"，SSE 收到 38 个 delta 后 `final.refused=true`，答案被替换为"当前知识库中没有足够的证据回答该问题。"——草稿内容未成为正式答案，说明引用/证据校验生效。

### 6.3 知识不足拒答

问："请推导量子色动力学中的渐近自由公式"，`refused=true`，`citations=0`，答案同上句拒答文案。

### 6.4 多轮会话

同一 `session_id` 连续两问：

1. "Redis 的 AOF 是什么？" → `refused=false`，"[S1][S2][S3]"
2. "那 RDB 呢？和刚才说的有什么区别？" → 返回的 `session_id` 与首次相同，答案同时引用上一轮上下文，并明确说明"资料中没有说明 AOF 与 RDB 的具体区别"，未编造。会话按 TTL 保存最近三轮。

### 6.5 Function Calling

单工具调用（`/api/v1/chat/tool-completions`）：

| 字段 | 实测 |
|---|---|
| tool | `search_knowledge` |
| status | ok |
| latency_ms | 54.709（另一次 61.377） |
| tool_call_id | `call_00_G3xHJ8aa79njZiTMy18P1220` |
| source_ids | S1–S5 |
| usage | 真实 token（见下） |

连续工具调用（"先搜索…再查询第一条结果的元数据"）：

| 顺序 | 工具 | 状态 | 耗时 | 证据 |
|---|---|---|---:|---|
| 1 | `search_knowledge` | ok | 61.377 ms | S1–S3 |
| 2 | `get_document_metadata` | ok | 0.966 ms | M4 |

- 第二次调用的参数来自第一次结果的 `document_id`（`08225058-1692-5745-ba0e-f824ca96eea4`）
- `usage` 返回 **3 段真实上游调用**：572/71、1302/100、1527/326 tokens（合计 3,898）
- 总延迟 4,020.513 ms，最终答案给出文件名、版本号 1、状态 completed

### 6.6 前端接收验证

`app/web/workbench.js` 与后端字段逐项对齐：`final` 事件渲染 `refused/index_revision/retrieval_mode/latency_ms/ttft_ms/llm_ttft_ms/usage/citations`；`delta` 作为草稿、`final` 覆盖；无 `final` 时抛错并清空草稿；工具页读取 `tool_calls`（工具、状态、耗时、来源）与 `evidence`（含 `cited_sources` 标记）。`/api/v1/ready` 现在返回 `chat_enabled=true`、`tool_chat_enabled=true`，页面不再显示"服务端尚未配置模型凭据"。

说明：受工具限制，本轮未做真实浏览器点击与截图，前端结论来自接口返回与脚本代码核对，**未做视觉验收**。

## 7. 安全检查结果

| 检查 | 结果 |
|---|---|
| 工作区是否含 `sk-` 形式密钥 | 无（`.env`、`.env.demo`、`.env.example`、`.env.demo.example` 均干净） |
| Git 跟踪文件中是否含密钥 | 无 |
| Git 历史中的密钥 | **存在**：提交 `feeda28` 曾把 `LLM_API_KEY=sk…` 写入 `.env.example`；当前工作区已移除，但历史仍在 |
| 演示模式未授权上传 | `POST /api/v1/documents/upload` 无令牌 → **401** |
| 演示模式文档/接口暴露 | `/docs` → **404**，`/openapi.json` → **404** |
| 演示模式付费问答 | `/api/v1/chat/completions` → **403 Demo RAG is disabled** |
| 演示模式工具问答 | `/api/v1/chat/tool-completions` → **403** |
| 匿名检索 | 200，仅返回白名单版本 |
| 容器以非 root 运行 | 是（UID 10001） |
| 数据卷与知识库隔离 | 演示项目独立卷，未触碰开发库 |

### 7.1 费用保护核查（本轮复查）

- 演示预算持久化在 SQLite `demo_budget` 表，`reserve_demo_call()` 用单条 `UPDATE ... WHERE calls < cap` 原子扣减，服务重启不清零。
- 扣减发生在授权通过之后、上游调用之前，包含最终被引文校验拒答的请求。
- Function Calling 内部会产生多次上游调用（本轮实测一次链式调用 = 3 段 usage）。演示模式下 `/api/v1/chat/tool-completions` 由 `allow_tool_chat()` **硬性 403**（与 `DEMO_RAG_ENABLED` 无关），因此公网访客无法触发多段计费。
- 结论：**现状安全**；但若未来开放演示 Function Calling，需要把预算从"每请求 1 次"改为"按上游调用次数扣减"，否则一次工具链可消耗 3–4 倍额度。本轮不开放，保持关闭。
- 未授权探测仍为：上传 401、文档列表 401、`/docs` 404、演示 chat 403。

**处理建议（需要用户确认后执行，本轮未做）：**

1. 立即在 DeepSeek 控制台撤销旧密钥（历史中的那个）。
2. Git 历史清理三选一：
   - 用 `git filter-repo` 重写历史后强推（会改写提交 ID，需所有人重新克隆）；
   - 保留本地历史，另建一个干净的发布仓库（推荐，风险最低）；
   - 若仓库始终私有且不公开，可接受风险并轮换密钥，但不建议。
3. 新密钥只放服务端 `.env` / `.env.demo`，这两个文件已被 `.gitignore` 覆盖。

## 8. GitHub 与 CI

- `origin = https://github.com/XI0ZI123/QueryForge.git`
- `git ls-remote origin` 返回 `remote: Repository not found`（404），与审计记录一致。
- 本地 `gh` CLI 未安装，凭据管理器为 `manager`。
- 因此 **远程 CI 未验收**，本轮不能声称 GitHub Actions 已通过。

需要用户完成：确认仓库是否存在/是否私有、账号是否有权限、凭据是否过期。仓库可用后：

```powershell
git push origin main   # 先完成第 7 节的密钥处理
```

## 9. 部署状态：待部署（无公网地址）

服务器未提供，**没有公网链接**，不会编造。以下是按现有 `docker-compose.yml` + `docker-compose.demo.yml` 整理的单机部署清单，拿到香港轻量服务器（建议 4C8G，SSD ≥ 60 GB）后逐条执行即可。

```bash
# 0) 本机准备发布包（排除环境文件、数据卷、恢复点）
git archive --format=tar.gz -o queryforge-release.tar.gz HEAD
scp queryforge-release.tar.gz ubuntu@<server>:/opt/queryforge/

# 1) 服务器基础环境
sudo apt update && sudo apt install -y ca-certificates curl nginx
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # 重新登录
docker compose version          # 需 >= 2.24.4

# 2) 解包并配置演示环境（密钥只写服务器）
cd /opt/queryforge && tar xzf ../queryforge-release.tar.gz
cp .env.demo.example .env.demo && chmod 600 .env.demo
# 编辑 .env.demo：DEMO_ADMIN_TOKEN、LLM_API_KEY、DEMO_RAG_ENABLED、DEMO_LLM_TOTAL_CALLS、DEMO_PUBLIC_VERSION_IDS

# 3) 构建并启动
docker compose --env-file .env.demo -f docker-compose.yml -f docker-compose.demo.yml build api
docker compose --env-file .env.demo -f docker-compose.yml -f docker-compose.demo.yml up -d --no-build --wait
curl --fail http://127.0.0.1:8001/api/v1/ready

# 4) 演示语料入库（在服务器上执行，需 DEMO_ADMIN_TOKEN）
cd /opt/queryforge && DEMO_ADMIN_TOKEN=*** DEMO_API=http://127.0.0.1:8001 \
  python3 scripts/seed_demo_corpus.py
# 把输出的 20 个 version_id 写入 .env.demo 的 DEMO_PUBLIC_VERSION_IDS，再重建 api

# 5) Nginx + HTTPS（SSE 必须关缓冲）
#    proxy_buffering off; proxy_read_timeout 300s;
#    client_max_body_size 21m; 只转发 443 → 127.0.0.1:8001
sudo certbot --nginx -d <domain>

# 6) 安全组只放行 22/80/443；Redis 6379、Qdrant 6333 不映射宿主机端口
```

服务器上的验证项：`/api/v1/ready`、匿名检索、无令牌上传 401、`/docs` 404、HTTPS 证书、SSE 不被缓冲、重启后数据卷仍存在。

## 10. 本地访问

| 入口 | 地址 | 状态 |
|---|---|---|
| 本地工作台 | http://127.0.0.1:8000/ | 可访问 |
| 本地 OpenAPI | http://127.0.0.1:8000/docs | 可访问（local 模式） |
| 演示栈（本机预演） | http://127.0.0.1:18001/ | 可访问，`/docs` 已关闭 |
| 演示管理入口 | 工作台"知识库管理"页，输入 `DEMO_ADMIN_TOKEN` | 仅本次页面持有，刷新即清 |

管理员令牌不写入前端源码、不落浏览器持久化、不出现在 URL。

## 11. 多端验证

| 端 | 状态 |
|---|---|
| Windows PC + Chrome/Edge | 页面与静态资源返回 200，响应式 CSS 已存在；**未做人工视觉验收** |
| iPhone 视口 | 未验证（无真实设备/浏览器工具） |
| iPad 视口 | 未验证 |

CSS 含 `@media` 断点（侧边栏折叠、表格横向滚动），但本轮没有截图证据，不宣称通过。

## 12. 已知局限

1. 单机架构：SQLite 单写者、Redis/Qdrant 单实例，不是高可用集群。
2. Reranker CPU 推理 P95 约 17 秒，公网默认关闭。
3. 在 T2Retrieval 子集上 Dense 优于融合策略，Adaptive 的收益依赖查询类型，不能宣传为普遍最优。
4. 演示预算按部署累计，多访客共享；上游供应商侧额度才是最终闸门。
5. 固定窗口限流在多实例下不精确，公网必须叠加 Nginx 每 IP 限制。
6. 真实模型链路已在本地验证（RAG、SSE、多轮、引用校验、Function Calling）；演示栈默认关闭付费问答与工具问答，公网成本边界依赖部署方配置。

## 13. 维护与回滚

- 停止：`docker compose --env-file .env.demo -f docker-compose.yml -f docker-compose.demo.yml stop`
- 重启：把 `stop` 换成 `up -d --no-build --wait`
- 回滚：保留上一版镜像 tag；`docker compose ... up -d --no-build --wait` 指回旧镜像；配置回滚同步恢复 `.env.demo`；数据库只在确认写入窗口后回滚。
- 备份：SQLite 在线备份 + Qdrant 快照 + `data/uploads` 原件。
- 任务补偿：`docker compose exec -T api python -m app.recovery --min-age 900 --limit 20`（建议宿主机定时任务）。

## 14. 用户需要手动完成的事项

1. **撤销旧 DeepSeek 密钥**，并决定 Git 历史处理方式（见第 7 节）。新密钥已配置到本机 `.env`，旧钥仍需确认已在供应商侧撤销。
2. ~~提供新密钥~~（已完成）：真实 RAG/SSE/Function Calling 验收见第 6 节。
3. 确认 GitHub 仓库状态与访问权限；决定是否推送以及是否清理历史。
4. 提供香港服务器（或确认由你执行第 9 节命令）。
5. 域名与 DNS（可选，但正式演示建议绑定域名以获得稳定 HTTPS 地址）。
6. 真机访问验收（手机/iPad）。

## 15. 简历项目描述（基于真实数据）

> **QueryForge｜企业知识库高可靠异步 RAG 后端与自适应混合检索引擎**（Python 3.11 / FastAPI / Celery / Redis / Qdrant / Docker）
>
> - 设计并实现 PDF/TXT 异步入库流水线：SHA-256 去重、Celery late-ack + 指数退避、文件互斥锁、持久化重试上限与补偿任务，保证重复投递下的幂等。
> - 自研 BM25 倒排索引（jieba 分词），与 BGE-small-zh-v1.5 + Qdrant 稠密召回并行，按查询类型做加权 RRF 融合，可选 Cross-Encoder 精排；在 T2Retrieval 500 篇子集上完成六组消融，Dense Recall@10 0.9608、MRR@10 1.0000，并如实记录融合策略未超过 Dense 的结论。
> - 实现 IndexSnapshot + revision 机制与版本化 Redis 缓存，文档更新后旧版本从新快照中移除，缓存键随修订版失效。
> - 基于 LangChain/DeepSeek 实现带引用校验的 RAG 与 SSE 流式输出，引用非法即拒答；Function Calling 限制白名单、参数校验与调用轮次；提供独立 stdio MCP 服务端（两个只读工具）。
> - 工程化：123 项 Pytest 用例、84% 语句覆盖率、GitHub Actions 工作流、Locust 100 用户压测（4,502 请求、3,299 次真实 429 验证限流与 Retry-After）、Docker Compose 单机部署与演示/开发双模式隔离（Bearer 管理员令牌、版本白名单、持久化模型预算）。
