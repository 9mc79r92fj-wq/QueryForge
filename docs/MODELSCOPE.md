# ModelScope Docker 创空间部署

ModelScope 的 Docker 创空间是单容器、单对外端口（7860，不可改）。因此这里不沿用 Compose，而是在一个容器里用 supervisord 同时运行 FastAPI、Celery Worker、Redis 与 Qdrant；Redis 与 Qdrant 只监听 127.0.0.1，公网只暴露 7860。

```
浏览器 ──HTTPS──> 创空间代理 ──> FastAPI :7860
                                    ├── Redis  127.0.0.1:6379（缓存、Celery、会话）
                                    ├── Qdrant 127.0.0.1:6333（向量）
                                    └── Celery Worker ── 入库与嵌入
/mnt/workspace/qf  持久卷：模型缓存、SQLite 快照、公开版本白名单
```

## 账户前置条件

Docker 创空间要求**实名认证 + 绑定阿里云账号**，这两步只能由账户持有人完成。创空间创建后，用平台访问令牌即可通过 Git 推送代码。

## 文件清单

| 文件 | 作用 |
|---|---|
| `deploy/ms/Dockerfile` | 单容器镜像：apt 安装 redis/supervisor，pip 装依赖，下载 qdrant 二进制 |
| `deploy/ms/supervisord.conf` | 进程编排与自动重启 |
| `deploy/ms/start.sh` | 恢复快照、把 FastEmbed 缓存指向持久卷、初始化 SQLite、拉起 supervisord |
| `deploy/ms/maintenance.sh` | 等待 API → 首次发布 20 篇演示语料 → 每 5 分钟写一次一致性快照 |
| `deploy/ms/qdrant.yaml` | Qdrant 仅监听回环，存储目录在容器盘 |
| `scripts/ms_bootstrap.py` | 通过真实上传接口入库，把版本 ID 写入持久卷白名单文件 |
| `app/index_snapshot.py` | 演示白名单 = 环境变量 ∪ 白名单文件（按 mtime 缓存） |

## 部署步骤

1. 在 https://www.modelscope.cn/studios/create 创建 **Docker** 类型创空间，名称 `QueryForge`，选择免费 CPU 规格。
2. 克隆创空间仓库并放入发布代码：

```bash
git lfs install
git clone https://oauth2:<ACCESS_TOKEN>@www.modelscope.cn/<username>/QueryForge.git
cd QueryForge

# 把 QueryForge-release 的内容复制到仓库根目录（不要带 .git / .env / venv）
rsync -a --exclude .git --exclude '.env*' --exclude data --exclude .venv311 \
      /path/to/QueryForge-release/ ./

cp deploy/ms/Dockerfile Dockerfile
cp deploy/ms/dockerignore .dockerignore
git add -A && git commit -m "deploy: ModelScope single-container build" && git push
```

> 必须替换根目录的 `.dockerignore`：本地 Compose 用的那份白名单只允许 `app/`、`requirements*` 与部分文档进入构建上下文，会排除 `scripts/`、`examples/`、`deploy/ms/`，导致创空间构建时 COPY 失败。`deploy/ms/dockerignore` 是为创空间准备的版本。

3. 在创空间「设置 → 环境变量」中添加（只在运行时注入，不进入镜像或仓库）：

| 变量 | 值 |
|---|---|
| `LLM_API_KEY` | 你的 DeepSeek 密钥 |
| `DEMO_ADMIN_TOKEN` | `python -c "import secrets;print(secrets.token_hex(32))"` 生成 |
| `DEMO_LLM_TOTAL_CALLS` | 演示累计预算，默认 300 |
| `HF_ENDPOINT` | 默认 `https://hf-mirror.com`，模型走国内镜像 |

4. 点击「启动」，在构建日志里等待镜像构建完成；运行日志出现 `[start] launching supervisord` 与 `[maintenance] published 20 versions` 后即可访问。

## 运行时行为

- **模型缓存**：FastEmbed 缓存软链到 `/mnt/workspace/qf/models`，重启后无需重新下载（首次仍约 100 MB）。
- **数据**：SQLite、上传原件、分块在容器盘；`maintenance.sh` 每 5 分钟执行 `wal_checkpoint` 后打包到 `/mnt/workspace/qf/runtime.tar.gz`，启动时若容器盘为空则自动恢复。
- **白名单**：首次启动由 `ms_bootstrap.py` 通过真实上传接口入库 20 篇文档，把版本 ID 写入 `/mnt/workspace/qf/public_versions.json`，无需重启即生效（快照按文件 mtime 读取）。
- **预算**：`DEMO_LLM_TOTAL_CALLS` 由 SQLite 原子扣减，随快照持久化。Function Calling 在演示模式仍硬性 403。
- **Redis**：缓存、Celery 队列与浏览器会话在容器内，重启会丢失；缓存可重建，未完成任务由 `app.recovery` 兜底。

## 已知限制

1. `/mnt/workspace` 可能是网络文件系统，SQLite WAL 与 Qdrant 不适合直接运行在上面，因此运行数据放在容器盘，仅持久化模型缓存与快照。
2. 快照是 5 分钟粒度：极端情况下重启会回退到上一次快照（预算计数同）。
3. 平台重启后模型需重新加载进内存（缓存命中，约 10–20 秒），首个请求会变慢。
4. 创空间改归属或改名时持久卷会清空，届时需重新初始化语料。
5. Qdrant 快照为尽力而为：在运行中打包存储目录，重启依赖 Qdrant 自身 WAL 恢复。
6. 该部署尚未在真实创空间上跑通，本文档中的启动流程需以首次构建日志为准。
