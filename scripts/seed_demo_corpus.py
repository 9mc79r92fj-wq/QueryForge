"""Seed the independent demo knowledge base with public technical notes.

The corpus is written to examples/demo/kb and uploaded through the real
multipart upload API, so ingestion, embedding and index activation all run
through the production pipeline. Re-running the script is safe: identical
content is deduplicated by SHA-256 and returns the existing version.

Usage (demo stack must be running):

    $env:DEMO_ADMIN_TOKEN = "..."
    python scripts/seed_demo_corpus.py
"""

import json
import os
import time

from pathlib import Path

import httpx


BASE_URL = os.environ.get("DEMO_API", "http://127.0.0.1:18001")
TOKEN = os.environ.get("DEMO_ADMIN_TOKEN", "")
ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "examples" / "demo" / "kb"
POLL_SECONDS = 240

DOCUMENTS: dict[str, str] = {
    "cache-consistency.txt": """Redis 缓存一致性

缓存一致性要解决的是数据库与缓存之间的数据偏差。最常见的写法是旁路缓存（Cache Aside）：读取时先查缓存，未命中再查数据库并回填；写入时先更新数据库，再删除缓存，而不是直接更新缓存。删除缓存可以让下一次读取自然回填最新值，也避免并发写导致的旧值覆盖。

先更新数据库再删除缓存仍有一个经典竞态：读请求在缓存失效后、数据库提交前读到旧值并回填，导致缓存停留旧数据。常见缓解手段是延迟双删、给缓存设置较短 TTL、或者通过订阅数据库变更日志（如 Canal）异步失效缓存。

强一致通常要引入分布式锁或读写互斥，代价较高。工程上更常用的是可观测的最终一致：为缓存写入记录来源版本号，读路径校验版本，配合 TTL 兜底。QueryForge 的检索缓存采用版本化键，索引修订版变化后旧键自然失效。

来源：https://redis.io/docs/latest/develop/use/patterns/
""",
    "cache-failure-modes.txt": """缓存穿透、击穿与雪崩

缓存穿透指查询的键在缓存和数据库中都不存在，请求每次都会落到数据库。常见防护是缓存空值并设置较短 TTL，或者在入口使用布隆过滤器提前判定键一定不存在。

缓存击穿指某个热点键过期瞬间，大量并发请求同时回源数据库。常用方案是互斥重建：只允许一个请求加载数据，其余请求等待或返回旧值；另一种是逻辑过期，键不真正过期，由后台线程异步刷新。

缓存雪崩指大量键在同一时间集中失效，或者缓存服 务整体不可用，请求瞬间压到数据库。应对方式包括给 TTL 加随机抖动、多级缓存、缓存集群高可用，以及在数据库前设置限流与熔断降级。

三类问题的共同点是保护下游，而不是消灭缓存。设计时要结合热点分布、可接受的数据陈旧度和故障预算来选择组合方案。

来源：https://redis.io/docs/latest/develop/use/patterns/
""",
    "celery-task-lifecycle.txt": """Celery 异步任务机制

Celery 是 Python 生态常用的分布式任务队列。生产者把任务消息发送到 Broker（如 Redis、RabbitMQ），Worker 进程从队列取出消息并执行，执行结果可以写回 Result Backend。

一次任务从调用 delay/apply_async 开始，消息进入队列后由 Worker 预取（prefetch）。默认每个 Worker 子进程会预取多条消息，长任务场景下应通过 worker_prefetch_multiplier 调低预取数量，避免任务在本地堆积而其他 Worker 空闲。

任务通过 task_id 追踪，状态由后端保存。对入库这类耗时任务，应该显式声明软超时（soft_time_limit）与硬超时（time_limit），并在任务内部按阶段更新进度，便于前端展示。

Celery 适合最终一致、可重试的后台工作，不适合强实时交互。生产环境还要考虑 Broker 持久化、Worker 并发模型（prefork、gevent、eventlet）和监控（Flower、Prometheus）。

来源：https://docs.celeryq.dev/en/stable/userguide/tasks.html
""",
    "celery-acks-retries.txt": """Celery 消息确认与重试

Celery 默认在任务执行前就确认（ack）消息：Worker 收到消息即从队列移除，进程崩溃时任务会丢失。需要至少一次投递时应开启 acks_late=True，让消息在任务成功后才被确认，失败或 Worker 掉线时由 Broker 重新投递。

重投递依赖 Broker 的可见性超时。Redis 作为 Broker 时要注意 visibility_timeout 必须大于最长任务执行时间，否则执行中的任务会被重复投递。配合 task_reject_on_worker_lost=True，可以让 Worker 异常退出时的任务重新入队。

自动重试通过 self.retry() 实现，可以指定 countdown 做指数退避，并设置 max_retries 上限。重试必须与幂等配合，否则重复执行会产生重复数据。

本项目的入库任务使用 late ack、可恢复阶段、文件互斥锁、指数退避和持久化的执行次数上限；超过上限后任务进入 failed 终态，由补偿命令或人工介入，而不是无限重试。

来源：https://docs.celeryq.dev/en/stable/userguide/tasks.html#task-options
""",
    "idempotency.txt": """任务幂等性

消息队列普遍提供至少一次投递，因此任务必须假设自己可能被执行多次。幂等意味着同一请求执行一次与执行多次对最终状态的影响一致。

常见实现有多层：业务层用唯一键或去重表记录已处理的请求；状态机用条件更新做 CAS（只有处于某状态时才允许推进）；外部写入使用确定性 ID，让重复写入覆盖同一条记录而不是新增。

以文档入库为例：分块 ID 由版本 ID 与序号确定性生成，重复写入 Qdrant 会覆盖相同 point；版本激活在单个数据库事务里完成，已完成的版本再次执行会直接跳过；文件级互斥锁避免同一版本被两个 Worker 同时处理。

幂等不是单一开关，而是整条链路的约束。设计时要明确每一步的重复执行代价，并要求所有外部副作用（向量写入、对象存储、消息发送）都可安全重放。

来源：https://docs.celeryq.dev/en/stable/userguide/tasks.html#avoiding-launching-synchronous-subtasks
""",
    "qdrant-vector-search.txt": """Qdrant 向量检索

Qdrant 是开源向量数据库，核心概念是 collection、point 与 payload。每个 point 包含 ID、向量和可选的 payload；payload 支持过滤条件，可以在向量检索时先按元数据缩小范围。

创建 collection 时需要指定向量维度与距离函数（Cosine、Dot、Euclid）。距离函数必须与生成向量的模型匹配，维度不一致会直接写入失败。生产环境常给 document_id、version_id 之类的字段建立 payload 索引，让过滤走索引而不是全量扫描。

写入使用 upsert，带相同 ID 的 point 会被覆盖，这为幂等重放提供了基础。可以按批写入并使用 wait=true 等待确认，再推进业务状态。

检索接口支持 limit、score_threshold、filter 和 with_payload。对于多版本知识库，检索时用版本白名单过滤，保证不会返回未激活版本的内容。

来源：https://qdrant.tech/documentation/concepts/
""",
    "hnsw-index.txt": """HNSW 索引

HNSW（Hierarchical Navigable Small World）是目前主流的近似最近邻图索引。它把向量组织成多层图：上层稀疏、用于快速跳转，底层密集、包含全部节点，查询从顶层贪心下降到目标区域，再在底层做局部搜索。

影响召回率与延迟的主要参数是 M（每层连边数）和 ef_construct（建图时候选队列大小），查询时用 ef_search 控制搜索广度。M 越大图越稠密，召回更高但内存和构建成本上升；ef_search 越大召回越高、延迟越高。

HNSW 的取舍是拿少量召回损失换取数量级的延迟下降。对于百万级向量需要评估内存占用，必要时使用量化或磁盘存储。

Qdrant 默认使用 HNSW，并通过 hnsw_config 暴露上述参数。选择参数时应以真实数据集上的 Recall@K 与 P95 延迟为目标，而不是照搬默认值。

来源：https://qdrant.tech/documentation/concepts/indexing/
""",
    "cosine-similarity.txt": """Cosine Similarity

余弦相似度衡量两个向量方向的一致性：cos(a, b) = a·b / (|a||b|)，取值在 -1 到 1 之间，与向量长度无关。文本嵌入通常只关心语义方向，因此余弦是常见选择。

当向量已经单位化时，余弦相似度等于内积，计算可以退化为一次点积，这也是很多向量库要求先归一化的原因。余弦与欧氏距离单调相关：|a-b|² = 2 - 2cos(a,b)（单位向量），所以排序结果一致。

工程上要注意三个细节：浮点误差可能导致范数略小于 1；零向量无法归一化，必须显式拒绝；不同模型输出的维度不同，混用会得到无意义分数。

本项目的嵌入结果在写入与打分前都做归一化，并对非有限值、维度和零向量做校验，避免脏数据进入索引。

来源：https://en.wikipedia.org/wiki/Cosine_similarity
""",
    "fastapi-async-concurrency.txt": """FastAPI 异步并发

FastAPI 基于 Starlette 与 ASGI，支持 async def 的协程处理器和普通 def 处理器。协程处理器在事件循环中执行，适合 I/O 密集操作；普通函数处理器会被放到线程池执行，避免阻塞事件循环。

关键原则是不要在协程里调用阻塞库。同步的数据库驱动、CPU 密集推理、阻塞式 HTTP 客户端都会卡住整个事件循环，导致所有请求排队。正确做法是使用异步驱动，或用 run_in_threadpool、独立线程池、消息队列把阻塞工作隔离出去。

并发能力还取决于进程模型。单进程单事件循环受 CPU 限制，生产环境用多个 Uvicorn Worker 或多副本分摊；同时要用连接池限制数据库与外部服务的并发，避免上游被打爆。

FastAPI 依赖注入适合放置鉴权、限流、请求上下文等横切逻辑，但依赖会按请求执行，重逻辑应缓存或下沉。

来源：https://fastapi.tiangolo.com/async/
""",
    "sse-streaming.txt": """SSE 流式传输

Server-Sent Events 使用 text/event-stream 响应，服务端持续写入以空行分隔的事件块，每个事件可以带 event、data、id、retry 字段。浏览器通过 EventSource 接收，或在前端用 fetch 读取流。

SSE 是单向的（服务端到客户端），基于普通 HTTP，天然支持断线重连：客户端会带上 Last-Event-ID 重新连接，服务端可以据此续传。相比 WebSocket，SSE 更简单，适合模型逐字输出这类场景。

工程上要处理缓冲：Nginx 需要 proxy_buffering off，避免响应被攒满才发出；还需要设置较长的读取超时，并定期发送注释行保活。

流式输出与一致性需要配合：模型输出的临时片段可能被后续校验推翻。稳妥做法是先发草稿 delta，收到完整结果并校验引用后，再发 final 覆盖；连接中断或校验失败时前端必须清空草稿。

来源：https://html.spec.whatwg.org/multipage/server-sent-events.html
""",
    "bm25.txt": """BM25 检索

BM25 是经典的词袋检索算法，脱胎于 TF-IDF。它对每个查询词计算 IDF 加权的词频得分，并用文档长度归一化抑制长文档优势：score = Σ IDF(qi) · tf·(k1+1) / (tf + k1·(1-b+b·|d|/avgdl))。

k1 控制词频饱和速度，b 控制长度归一化强度，通常取 k1≈1.2-2.0、b≈0.75。BM25 依赖分词，中文需要先用分词器切词，否则整句会被当作一个词项。

BM25 的优点是无需训练、可解释、对专有名词与精确关键词非常有效；缺点是缺乏语义泛化，同义改写容易漏召回。

实现上通常使用倒排索引：词项到文档列表的映射，查询时取候选集合并打分。自研实现要注意查询词去重与 IDF 取正值，避免负分干扰排序。

来源：https://en.wikipedia.org/wiki/Okapi_BM25
""",
    "dense-retrieval.txt": """Dense Retrieval

稠密检索用神经网络把查询和文档编码成固定维度向量，再用向量相似度找最近邻。常见结构是双塔（bi-encoder）：查询塔与文档塔独立编码，文档向量可以离线预计算并建立 ANN 索引，查询时只需编码一次查询。

相比 BM25，稠密检索能捕捉同义与语义相似，对自然语言问句更友好；代价是需要嵌入模型、向量存储，且对领域术语、稀有实体可能不如关键词精确。

衡量效果常用 Recall@K、MRR@K、NDCG@K。召回率看相关文档是否进入候选，MRR 看第一个相关结果的排名，NDCG 考虑分级相关性与位置折扣。

工程上要关注嵌入模型的语言与领域匹配、向量维度带来的存储成本、批量编码的吞吐，以及索引与业务数据的版本一致性。

来源：https://huggingface.co/BAAI/bge-small-zh-v1.5
""",
    "rrf.txt": """RRF 倒数排名融合

RRF（Reciprocal Rank Fusion）把多路检索结果按排名融合：score(d) = Σ 1/(k + rank_i(d))，k 是平滑常数（常取 60）。它只使用排名，不使用原始分数。

这带来一个关键好处：不同检索器的分数尺度不可比（BM25 分数无上界、余弦在 -1 到 1 之间），直接加权求和需要归一化且对分布敏感；RRF 天然规避了这个问题。

RRF 的局限是忽略了分数差距。如果第一名与第十名分数差距很大，RRF 仍然只按名次给分。加权 RRF 通过给每路乘一个权重来引入先验，例如关键词类查询提高 BM25 权重。

融合前通常对每路结果去重并截断到固定长度，避免长尾噪声；k 的取值影响头部与长尾的相对贡献。

来源：https://dl.acm.org/doi/10.1145/1571941.1572114
""",
    "adaptive-hybrid-retrieval.txt": """Adaptive Hybrid Retrieval

混合检索同时执行关键词召回与向量召回，再融合结果。它能兼顾精确匹配与语义泛化，但固定权重难以适配所有查询。

自适应方案在检索前增加查询路由：用轻量规则或分类器判断查询类型。包含代码、错误码、专有名词的查询更依赖关键词；自然语言长问句更依赖语义。路由结果映射成两路权重，再执行加权 RRF。

判断依据可以包括查询长度、是否含英文标识符、是否存在疑问词、分词后的词性分布等。路由必须保持轻量与可解释，避免引入新的高延迟组件。

需要诚实评估收益：在本项目的 T2Retrieval 实验中，Dense 在 Recall@10、MRR@10、NDCG@10 上优于 Hybrid 与 Adaptive。自适应权重的价值更多体现在混合语料与关键词型查询上，不能宣称它普遍最优。工程结论应以实际数据为准。

来源：https://dl.acm.org/doi/10.1145/1571941.1572114
""",
    "cross-encoder-reranker.txt": """Cross-Encoder Reranker

双塔检索为了可预计算牺牲了查询与文档的交互，Cross-Encoder 则把查询与候选文档拼接后送入模型，输出相关性分数。它能建模细粒度交互，精度通常高于双塔，但无法离线预计算。

因此 Cross-Encoder 一般用于重排：先由召回阶段取 Top-N（如 20-50），再对每个候选执行一次推理，最后截取 Top-K。N 越大精度越高，延迟线性增长。

CPU 推理是主要瓶颈。批大小、线程数、候选数量都会显著影响 P95。本项目的实测显示，两个候选的首次请求即耗时约 21.7 秒，暖机后 30 条查询的 P95 仍约 17.45 秒，远高于同次实验 Adaptive 的约 17.7 毫秒。

结论是 Reranker 默认关闭，只在本地模式或明确需要精排时开启，并限制候选数；公网演示应禁用以保护延迟与费用。

来源：https://huggingface.co/BAAI/bge-reranker-base
""",
    "rag-architecture.txt": """RAG 系统架构

检索增强生成把外部知识注入模型上下文，流程分为索引、检索、生成三部分。索引阶段解析文档、切片、编码并写入向量库；检索阶段按问题召回候选并可选重排；生成阶段把证据拼进提示词，要求模型基于证据作答。

RAG 的工程质量主要不在提示词，而在检索质量与可追溯性。切片要保留上下文完整性，召回要覆盖多策略，排序要可解释，最终答案要能引用到具体文档与片段。

引用校验是防止幻觉的关键：模型输出引用编号后，服务端必须校验编号确实来自本轮检索结果，否则按拒答处理。没有证据时应明确拒答，而不是让模型自由发挥。

还要处理版本一致性：文档更新后，旧版本不能出现在新请求的结果里。常见做法是给检索加上可见版本过滤，并用索引修订版标识当前快照。

来源：https://arxiv.org/abs/2005.11401
""",
    "function-calling.txt": """Function Calling

Function Calling 让模型在对话中返回结构化的工具调用请求，由应用执行后把结果回填给模型。典型循环是：模型决定调用工具 → 应用校验参数并执行 → 把 tool 结果作为新消息回传 → 模型继续生成或再次调用。

安全要点有三层。第一，工具白名单：只暴露明确允许的函数，不给文件写入、网络浏览、系统命令能力。第二，参数校验：用 Pydantic 等 schema 校验类型、范围与长度，拒绝越界输入。第三，预算与步数：限制最大轮次和总调用次数，防止模型陷入循环或消耗过多额度。

工具返回值应该是有界证据，而不是整篇文档。返回引用编号、片段摘要、文档 ID，便于最终答案标注来源。

可观测性同样重要：记录每次调用的工具名、参数摘要、耗时、状态与 Tool Call ID，但不要记录密钥或完整敏感上下文。

来源：https://platform.openai.com/docs/guides/function-calling
""",
    "mcp.txt": """Model Context Protocol

MCP 是面向模型应用的开放协议，用统一的客户端/服务端接口暴露工具、资源与提示模板。客户端（如 IDE 或 Agent 运行时）通过协议发现服务端能力，再按需调用。

传输层支持 stdio 与 HTTP。stdio 适合本地进程：客户端拉起服务端进程，通过标准输入输出交换 JSON-RPC 消息；HTTP 适合远程共享服务。stdio 模式下服务端的日志必须写 stderr，否则会污染协议流。

MCP 与 Function Calling 的区别在于层次：Function Calling 是模型与应用之间的调用约定，MCP 是应用与工具服务之间的标准化接口。两者可以组合——MCP 服务端把工具暴露给客户端，客户端再把工具注册给模型。

本项目实现了一个独立 stdio MCP 服务端，暴露 search_knowledge 与 get_document_metadata 两个只读工具，并通过脚本完成真实握手、工具发现与调用验证。

来源：https://modelcontextprotocol.io/docs/learn/architecture
""",
    "rate-limiting-degradation.txt": """服务限流与故障降级

限流用于保护服务不被突发流量击穿，常见算法有固定窗口、滑动窗口、令牌桶与漏桶。固定窗口实现简单，但窗口边界可能放行两倍流量；令牌桶允许短时突发，更适合有弹性的 API。

被限流的请求应返回 429，并携带 Retry-After 告诉客户端何时重试。并发保护则用信号量或有界线程池限制同时执行的数量，超出时快速失败返回 503，而不是无限排队。

降级是限流的补充：当某个依赖不可用时，返回可用的部分结果并标记 degraded，而不是整体失败。例如检索中一路召回超时，可以只返回另一路结果，并在响应里说明降级原因与耗时。

工程上还要区分进程内限流与网关限流。单进程固定窗口在多副本部署时不是全局精确的，公网入口应叠加反向代理的每 IP 限流；应用层限流用于兜底与保护昂贵下游（模型调用）。

来源：https://www.rfc-editor.org/rfc/rfc6585#section-4
""",
}


def upload(client: httpx.Client, name: str, path: Path) -> tuple[str, str]:
    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": (name, handle, "text/plain")},
        )
    response.raise_for_status()
    body = response.json()
    return body["task_id"], body["version_id"]


def wait_for(client: httpx.Client, task_id: str) -> dict:
    deadline = time.monotonic() + POLL_SECONDS
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/documents/tasks/{task_id}")
        response.raise_for_status()
        data = response.json()
        if data["status"] in {"completed", "failed"}:
            return data
        time.sleep(1)
    raise TimeoutError(f"ingestion timed out: {task_id}")


def main() -> None:
    if not TOKEN:
        raise SystemExit("DEMO_ADMIN_TOKEN is required")

    KB_DIR.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {TOKEN}"}
    versions: list[str] = []

    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=120.0) as client:
        for name, text in DOCUMENTS.items():
            path = KB_DIR / name
            path.write_text(text, encoding="utf-8")
            task_id, version_id = upload(client, name, path)
            status = wait_for(client, task_id)
            state = status["status"]
            print(f"{name:38s} {state:10s} {version_id}")
            if state != "completed":
                raise SystemExit(f"ingestion failed: {name} -> {status}")
            versions.append(version_id)

    print("\nApproved version ids (copy into DEMO_PUBLIC_VERSION_IDS):")
    print(json.dumps(versions, indent=2))


if __name__ == "__main__":
    main()
