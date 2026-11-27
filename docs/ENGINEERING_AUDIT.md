# QueryForge 工程审计与验收

审计日期为 2026-11-25。原始提交为 b29e3e959d329c9f7f0bfc05325a7780705e7320。本轮在同一仓库修复和验证，目标为香港 Linux 单机部署。工作台运行在 http://127.0.0.1:8000/。

## 原始架构与恢复点

FastAPI 接受请求，SQLAlchemy 操作 SQLite 任务和索引元数据。Redis 承载 Celery 队列、版本化检索缓存和会话。Worker 将 PDF/TXT 提取为文本块，使用 BGE-small-zh-v1.5 编码并写入 Qdrant。BM25、向量召回、RRF、Adaptive Router 与可选 Cross-Encoder 复用同一 IndexSnapshot。LangChain 驱动 DeepSeek 的常规问答、SSE 和 Function Calling。MCP 是独立 stdio 进程。

审计开始时 docker-compose.yml 有一项本地 HF_ENDPOINT 修改，recovery_test.txt 已存在且未跟踪。恢复点保存了这两个文件、工作区 diff，以及经过 git bundle verify 的完整 Git 历史。位置为本机 Codex 可写附件目录的 queryforge-pre-audit 子目录。原始提交和该恢复点包含已发现的历史凭据，交由本机保管。恢复前请先核对变更，旧配置含异常限流。

## 问题与改良

| 优先级 | 实际发现 | 处理及当前结果 |
|---|---|---|
| P0 | 现有 .env 把普通 API 上限设为 1,000,000。Compose env_file 将值传入容器，环境变量优先于 Pydantic 默认值。实际中间件读到 1,000,000 / 12 / 24。 | 恢复 600 / 12 / 24 并重建本机 API、Worker、Qdrant。观测到 Chat 第 13 个请求返回 429 且携带 Retry-After，普通 API 在复测中也返回有效的 Retry-After。 |
| P0 | Git 跟踪的 .env.example 在提交 feeda28 中曾包含符合密钥特征的 DeepSeek 值，与当时本地运行配置一致。 | 当前示例和本机已移除该值，运行容器已清除凭据。旧值仍存在于 Git 历史，且初次读取示例时曾进入本轮诊断输出。账户持有人需要立即在供应商侧撤销。 |
| P0 | 默认沙箱网络中的 GitHub HTTPS 请求经本机代理失败。联网诊断得到 Repository not found，GitHub 仓库查询得到 404。 | origin 与相关 Git 配置仅做读取检查。检查中存在进程 HTTP_PROXY/HTTPS_PROXY，Git 配置未发现显式代理。执行了 ls-remote 与 push --dry-run，远端仓库存在性及访问授权仍需账户持有人核对。 |
| P0 | API 与 Qdrant 原先绑定所有网卡，本地 API 对文档和付费功能无访问隔离。 | 本机端口限定回环。演示独立 Compose 项目、独立卷，Redis/Qdrant 无宿主机端口。匿名请求只读允许的活跃版本，管理需独立令牌。付费入口默认关闭。 |
| P1 | 外部请求可在上传处理器运行前占用 multipart 缓冲。客户端可在校验错误里看到输入片段。 | ASGI 层限制累计 HTTP 体积与声明长度，在演示管理 API 的请求体解析前验证令牌。校验错误仅返回类型、位置和消息。 |
| P1 | 任务补偿在释放锁后才发布，重复补偿可反复分发同一任务。状态可跳过入库阶段，Worker 可读取异常的数据库路径。 | 同一文件锁内在 SQLite 事务原子预留分发，60 秒冷却后允许重试。更新限制合法状态与单次进度，路径解析后必须仍位于 uploads。 |
| P1 | 重复任务和跨组件故障需要保留原始可追踪状态。Qdrant 首次并发建集合存在检查与创建竞态。 | 继续使用确定性片段 ID、late ACK、版本文件锁、8 次持久执行上限与指数退避。兼容并发创建的已存在响应并验证集合配置。已确认的向量写完后才允许激活。 |
| P1 | SSE 连接异常时，生成器退出依赖于框架路径。一个会话可并发读旧历史。 | 连接结束时显式关闭 SSE 生成器。单个 API 进程按会话拒绝并发问答。启动时幂等创建 SQLite 表，兼容现有卷。 |
| P1 | 本机 PATH 为 Python 3.14.10，既有 .venv 缺少测试依赖。 | 建立独立 Python 3.11 虚拟环境，保留系统 Python。依赖直接锁定，以 Linux 3.11 解析结果约束运行时依赖。CI 与 Docker 目标统一为 3.11。 |
| P2 | Benchmark 参考 BM25 可能把未匹配的零分文档放进榜单。历史基准缺少统一预热标记。 | 新协议仅纳入命中词项的参考候选，单独保留开发预热查询。数据准备固定历史数据集修订版，实验输出仅接受新路径。 |
| P2 | 缺少状态和结果的统一交互入口。 | 新增 FastAPI 同源工作台、实时依赖探测、文档版本列表、实验归档 API 和示例公开知识库。 |

## 可靠性与安全审计

- **SQLite 与索引。** 状态推进和激活使用写事务。激活时先提交 Qdrant 写入，再原子切换当前生效版本并自增 revision。读取快照用同一个读事务固定 revision 与版本集合。旧版本的 chunk/向量继续保留，快照中的版本过滤负责隔离。
- **部分失败与重试。** 未确认的向量只属于未激活版本，重复写入相同 chunk ID。Worker 的任务锁、持久计数与状态检查处理重复消息。SQLite 提交与 Redis 发布之间仍存在窗口，管理员通过补偿命令重发；运维可把该命令接到宿主机调度。预留成功后进程若在发布前退出，会在冷却过后重新成为候选。跨 SQLite、文件系统和 Qdrant 保留最终一致性边界。
- **缓存与负载。** 请求使用版本化缓存。Redis 缓存异常走原检索链；并行召回和 Reranker 使用有界线程池。超时后正在执行的 C/C++ 推理可能继续占用线程，线程槽由任务完成时归还。固定窗口每进程独立，部署使用单 API 进程。
- **文件与数据库。** 上传接受 PDF 与 UTF-8 TXT，检查扩展名、文件头与大小，使用内容哈希存储原件。解析约束 PDF 页数和抽取文本，worker 拥有时间及内存上限。ORM 和 SQLite 语句绑定用户参数；BEGIN 等原生 SQL 为常量。压缩 PDF 仍需谨慎处理，演示写入只授予管理端。
- **访问与费用。** 演示版本白名单默认为空，更新后需重新批准新版本。管理使用 Bearer token，浏览器问答使用 HttpOnly/SameSite Cookie，Redis 验证归属。默认关闭演示模型和工具问答。启用演示 RAG 后，每次授权问答原子扣减持久计数，查询长度、Top-K、768 输出 tokens、关闭 SDK 重试和全局请求限额共同约束资源。预算随该部署的 SQLite 数据卷持续保存，包括无证据拒答所预留的额度。
- **提示注入与工具。** 文档内容作为不可信证据。模型工具调用受明确白名单、Pydantic 参数限制及总调用次数约束，没有文件写入、网络浏览、系统命令能力。引用编号校验只确认来源属于本轮结果。浏览器通过 textContent 渲染证据及模型内容。CORS 保持同源，demo 关闭 OpenAPI，MCP 使用 stdio。
- **凭据与错误。** 使用不输出实际值的 Git 历史匹配检查。匹配只证实所扫描格式的已知问题。 .env 与 .env.demo 未纳入 Git，Docker 构建上下文只允许应用、依赖清单与实验归档。响应错误采用有限字段与 Request ID。容器采用非 root 应用用户、防止提权和限制监听范围。
- **剩余边界。** 演示配额可被多个访客竞争，公开入口需反向代理增加每 IP 限流和 TLS。额度配置不代替供应商侧限制。SQLite、Redis、Qdrant 都是单机依赖，恢复策略需要备份和演练。

## 自动化测试

以同一 Linux amd64 Python 3.11.18 和相同依赖为基线，从原提交归档原始 app/tests 后重跑，108 passed，覆盖 1196 / 1732 行，69.05%。本轮为 123 passed，覆盖 1720 / 2049 行，83.94%，增加 14.89 个百分点。Linux 运行有 2 项第三方提示。Windows Python 3.11.5 独立虚拟环境也通过 123 项，覆盖率相同，保留 1 项测试适配器提示。详见 [覆盖率归档](audit_coverage.json)。

| 模块 | 原覆盖率 | 本轮变化 |
|---|---:|---|
| ingestion.py | 0% | 实际解析、切片、状态推进、向量写入、激活，文件完整性和路径拒绝 |
| recovery.py | 0% | 文件锁、冷却内去重、发布失败和后续重试 |
| storage.py | 20% | 哈希去重、类型、大小、格式、空值及临时文件清理 |
| document_service.py | 27% | 真实 SQLite 版本创建、去重、管理列表和版本隔离 |
| vector_store.py | 27% | Qdrant 本地适配器、未确认写入和可重试响应 |
| tasks.py | 35% | Worker 成功、可恢复失败、退避、终态幂等 |
| reranker.py | 38% | 分数排序、有限值、分数个数和模型复用 |
| knowledge_tools.py | 50% | 真 SQLite 的选中版本与演示白名单 |
| benchmark.py | 0% | 指标、匹配候选、预热剔除；另有真实外部数据集运行 |

纯单元测试使用可控的 LLM 或编码接口替身。集成测试使用临时 SQLite、ASGI 请求和 Qdrant 本地适配器。端到端测试使用真实 Docker、Redis、HTTP Qdrant、Celery 和本地 BGE。测试以 app 为统计范围，保留原始测试，未加全局 warning 过滤。Python 3.11 下已看到 jieba 的 pkg_resources 弃用提示和 Qdrant 本地适配器的精确搜索提示，后者由测试使用本地适配器而产生。

## 真实性能与复现

### 检索 Benchmark

原始实验完整保存于 [benchmark_results.json](benchmark_results.json)。T2Retrieval 修订版 921dd3af6e78d1ae7ee0368aa8d7eaee02c8f08e，种子 42，500 篇文档，10 条开发查询与 30 条测试查询。保存脚本从带标注的候选查询随机分组，显式把这批查询的相关文档纳入 500 篇语料，然后补随机负例。因此指标对应这个候选样本，不能扩用于完整语料或生产数据。开发/测试查询 ID 交集为空，语料共享。本轮未用这批测试查询调参，它们是已观察的历史测试样本。数据见 [T2Retrieval 数据集](https://huggingface.co/datasets/mteb/T2Retrieval/tree/main)。

| 原始策略 | Recall@10 | MRR@10 | NDCG@10 | P95 ms |
|---|---:|---:|---:|---:|
| BM25 Reference | 0.8314 | 0.9067 | 0.8434 | 2.595 |
| BM25 | 0.8314 | 0.8900 | 0.8306 | 1.349 |
| Dense | 0.9608 | 1.0000 | 0.9776 | 16.569 |
| Hybrid | 0.9431 | 0.9611 | 0.9334 | 16.642 |
| Adaptive | 0.9431 | 0.9611 | 0.9343 | 16.646 |
| Adaptive + Reranker | 0.9655 | 1.0000 | 0.9775 | 15959.58 |

Recall 按正相关集合计算，MRR 取首个正例的倒数排名，NDCG 用分级相关增益、对数折扣和该查询的理想排序。单查询先去重并截取 10，所有查询取宏平均。Benchmark 直连 retrieve，绕过 Redis 缓存。两种 BM25 使用同一分词器。自研 BM25 对查询去重并使用正值 IDF。rank_bm25 参考实现在查询中计入重复词，使用 Okapi IDF 与负值修正，新协议对参考榜单移除了无词项交集候选。

原档中 Dense 的 Recall、MRR、NDCG 与 P95 优于两种融合。Cross-Encoder 对查询与每个候选文本配对执行 CPU 推理，线程数为 2、批大小为 4。真实 HTTP 中仅两个候选的首次 Reranker 请求即用时 21,682.167 ms。暖机复测与冷启动的区分见下方独立报告结果。

暖机复测使用同一公开数据文件、同一 30 条已观察测试查询、独立 Qdrant collection 与同样的模型参数。1 条开发查询用于预热，剩余 9 条用于开发评估。运行环境为 AMD Ryzen 7 6800H、Docker Desktop Linux amd64、Python 3.11.18，Redis 检索缓存关闭。本轮原始记录独立保存在 [暖机复测](benchmark_audit_warmed.json)。

| 暖机策略 | Recall@10 | MRR@10 | NDCG@10 | P95 ms |
|---|---:|---:|---:|---:|
| BM25 Reference | 0.8314 | 0.9067 | 0.8434 | 7.530 |
| BM25 | 0.8314 | 0.8900 | 0.8306 | 3.002 |
| Dense | 0.9608 | 1.0000 | 0.9776 | 15.288 |
| Hybrid | 0.9431 | 0.9611 | 0.9334 | 15.927 |
| Adaptive | 0.9431 | 0.9611 | 0.9343 | 17.707 |
| Adaptive + Reranker | 0.9655 | 1.0000 | 0.9775 | 17452.847 |

Reranker 首次开发预热花费 34,732.886 ms，测试前再次预热仍花费 14,473.489 ms。预热后测试 P95 仍约 17.45 秒，而同一复测的 Adaptive P95 约 17.71 毫秒。两者都已预热 BGE 嵌入，因此对比把主要耗时定位到 CPU 交叉编码阶段。模型初始化还会增加首次耗时。该结果没有量化 ONNX 各算子、分词及容器调度的占比，也没有更改生产候选规模、线程或模型。

### Locust

原始 100 用户结果为 4,902 请求，4,883 个非降级成功，99.61% 成功率，110.03 成功 QPS，19 次 503，零次降级。热点和冷查询成功 P95 分别为 82.937 ms 与 115.381 ms。记录时长 44.379 秒。

原 protected_100 结果单独保留。4,853 请求、4,834 成功、19 次 503、零次 429、零次检索降级。结合实际配置发现，它未验证普通 API 限流。

| 2026-11-25 独立复测 | 请求数 | 成功数 | 成功 QPS | 成功 P95 ms | HTTP 429 | HTTP 503 |
|---|---:|---:|---:|---:|---:|---:|
| 热点 | 3,616 | 964 | 22.080 | 162.734 | 2,643 | 7 |
| 冷查询 | 889 | 231 | 5.291 | 321.822 | 656 | 2 |
| 合计 | 4,505 | 1,195 | 27.371 | — | 3,299 | 9 |

实测 43.659 秒，1,195 个非降级成功，成功率 26.53%，两次连接失败记录为 HTTP 0，3,299 次 429 均带数值 Retry-After。热点缓存命中率 100%，冷查询为 0%，降级为零。复测使用三份已生效的独立样本文档、四比一热点权重、预热五条热点查询、Hybrid Top-5、无 Reranker 与生成。服务端 Python 3.11.18，负载机 Python 3.14.10，AMD Ryzen 7 6800H，Docker Desktop WSL 16 逻辑 CPU、约 7.9 GB 内存。固定窗口横跨窗口切换会接纳两个时间桶中的请求。三组数据的语料与限流配置存在差异，复测用于验证保护行为。保存文件为 [原始 100 用户](loadtest/users_100.json)、[原保护实验](loadtest/protected_100.json) 和 [独立保护复测](loadtest/protected_100_audit.json)。

## 全功能验收

| 项目 | 结果 |
|---|---|
| Python 语法、依赖检查、全量 Pytest、覆盖率 | 最终结果见自动化测试和归档，CI 步骤由同等 Linux 环境执行 |
| Compose 构建和本机启动 | 构建成功，SQLite、Redis、Qdrant 就绪；独立 Celery inspect ping 成功 |
| TXT、PDF、SHA-256、解析、切片、真实文档向量入库 | 独立项目 HTTP 验收通过 |
| BM25、Dense、Hybrid、Adaptive、RRF、Weighted RRF 与 Reranker | 真实结果复核通过，Adaptive 权重和 BGE 得分类型已观测 |
| 缓存与版本一致性 | 命中后更新，revision 由 2 增至 3，旧版本剔除，新版本首次查询不命中旧缓存 |
| 任务恢复与补偿 | 独立未分发任务实测首次补发 1 次、随后补发 0 次，任务完成至 100% |
| HTTP 限流、并发与请求追踪 | 真实 Chat 429 与普通 API 429；有界执行器和并发限制有自动化用例，压力复测观测 503 |
| RAG 常规与 SSE | 真实 HTTP 无证据路径通过，SSE 发送 final 拒答；引用、草稿清除和断连回收由自动化覆盖 |
| 多轮、TTFT、Token、Function Calling | 无付费真实模型验证。Redis 会话边界与工具调用协议有测试，真实生成、模型工具请求与 Token 统计待新密钥复验 |
| MCP stdio | Smoke Test 通过，协议版本 2025-11-25，两个真实只读工具及非法参数拒绝已验证 |
| Web | 桌面、手机及 iPad 视口检查，浏览器完成 Adaptive 搜索、真实权重、缓存命中和历史归档浏览 |
| 公网演示权限 | 隔离容器中实测未授权上传与列表为 401，管理令牌通过，未批准版本不会被 BM25 或 Dense 返回。双 HTTP 客户端验证 Redis 归属，第三次授权请求达 SQLite 额度，重启后仍返回 429。该路径使用无证据查询，生成调用数为零。 |
| GitHub Actions | 本地解析 workflow 并验证 Python、依赖、语法和测试。远端 404，未观测到本轮 GitHub Actions 运行 |
| 香港部署 | 完成镜像、Compose、权限、环境样例与回滚文档。本轮在本机验证 |

## 变更位置

- **运行与数据**。app/config.py、app/access.py、app/main.py、app/middleware.py、app/models.py、app/index_snapshot.py、app/document_service.py、app/document_routes.py、app/schemas.py、app/ingestion.py、app/recovery.py、app/task_service.py、app/vector_store.py。
- **问答与接口**。app/chat_routes.py、app/rag_service.py、app/llm.py、app/function_calling.py、app/tool_routes.py、app/search_routes.py、app/system_routes.py、app/web。
- **实验与测试**。app/benchmark.py、scripts/prepare_benchmark.py、scripts/locustfile.py、scripts/integration_smoke.py、scripts/mcp_smoke.py、tests/test_demo_mode.py、tests/test_ingestion_integration.py、独立运行报告。
- **部署与文档**。Dockerfile、docker-compose.yml、docker-compose.demo.yml、.env.example、.env.demo.example、.gitignore、.dockerignore、CI workflow、各 requirements、constraints.txt、README.md、docs/DEPLOYMENT.md、examples/demo。

## 待操作与关键知识

1. 在 DeepSeek 后台撤销旧凭据并查看使用记录。新密钥仅配置服务端，随后用公开样本做一条常规问答、一条有证据的 SSE、一条追问以及一条 Function Calling，核对普通引用、SSE final、使用统计和实际计费。
2. 用有权限的 GitHub 账号核对 origin 指向的仓库名称、可见性和写权限。确认密钥已撤销，再决定历史清理与首次推送策略。建议提交顺序为安全与运行配置、可靠性及测试、工作台及文档实验。密钥、恢复点、数据卷和已有未跟踪样本一并排除在提交之外。
3. 在线上使用独立 demo 数据卷、批准的版本白名单、TLS 与访问频率限制。切换配置前看 [部署与回滚说明](DEPLOYMENT.md)。
4. 设计与面试重点包括 SQLite 发布事务如何承诺一致的 IndexSnapshot，Qdrant 幂等写入与 Celery late ACK 如何处理跨组件失败，版本键为何让 Redis 历史缓存自然失效，固定窗口与有界线程池如何保护不同资源，句级 Dense 和 Cross-Encoder 的效果及成本差异，以及引用验证与证据真实性的边界。

本轮变更留在工作区，原始提交、未跟踪样本和实验归档均保留。本机工作台继续运行，两个临时验收项目已经停止，持久化卷已保留。后续提交可用 git revert 回滚，运行镜像可固定到已验收标记。远端同步应在凭据处置和 GitHub 授权确认后进行。
