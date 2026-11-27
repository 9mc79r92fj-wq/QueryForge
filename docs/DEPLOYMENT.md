# QueryForge 部署与回滚

目标为香港 Linux 单机 Docker Compose。仓库配置使用 Docker Compose 2.24.4 或更新版本，以支持可选 env_file 和演示覆盖文件使用的合并标签。

## 开发与演示环境

开发项目默认为 queryforge，使用 .env 及已有数据卷。演示项目默认为 queryforge-demo，使用 .env.demo 和独立卷。保留演示项目名可以让其数据库、消息队列与向量索引处于独立命名空间。生产反向代理将 443 转到演示 API 的回环端口 8001，Redis、Qdrant 与 MCP 保持内网或本机访问。

```bash
cp -n .env.demo.example .env.demo
chmod 600 .env.demo
docker compose --env-file .env.demo -f docker-compose.yml -f docker-compose.demo.yml config --quiet
docker compose --env-file .env.demo -f docker-compose.yml -f docker-compose.demo.yml build api
docker compose --env-file .env.demo -f docker-compose.yml -f docker-compose.demo.yml up -d --no-build --wait
curl --fail http://127.0.0.1:8001/api/v1/ready
```

1. 用 python -c 'import secrets; print(secrets.token_hex(32))' 生成独立管理令牌并存入 .env.demo。管理操作在本机、受控接入或 TLS 下执行。浏览器管理面板仅从当前输入取令牌，页面刷新即清除。
2. 使用 examples/demo 里的公开文档。上传后等待 Celery 返回 completed，审核文件正文，再把返回的 version_id 写入 DEMO_PUBLIC_VERSION_IDS 的 JSON 数组。更新文档后，旧版本退出活跃状态。审核并批准新版本后重建 API。未批准的版本无法通过演示检索、RAG 或同一配置启动的 MCP 读取。
3. 需要问答时，固定 DeepSeek Flash 与官方端点，设置新的 LLM_API_KEY、非零 DEMO_LLM_TOTAL_CALLS 和 DEMO_RAG_ENABLED=true。建议专用低额度项目凭据并以供应商侧现有配额封顶。应用会在每个通过授权的 Chat 请求前原子扣一份部署预算，包括后续因无证据拒答的请求。重启继续使用同一 SQLite 计数，调用上游时 SDK 禁用自动重试，每次输出最多 768 tokens。
4. 保持 DEMO_SECURE_COOKIE=true。反向代理启用 HTTPS、限制每 IP 的请求速率及连接数，代理层上传上限与应用 20 MiB 加表单开销相匹配。SSE 关闭代理缓冲，保留 Retry-After 与 X-Request-ID，并给模型和检索留出代理读取超时。无需在代理层开启跨域访问。
5. 托管在 Linux 上前，管理员应验证 443 转发、匿名检索、未授权上传 401、问答禁用状态、镜像与数据卷权限。数据库与 Redis 暂存模型调用预算、会话和任务，公网入口只接到 FastAPI。在启用付费功能前额外做通过 TLS 的双客户端会话隔离验收，并验证一个真实模型请求的输出与实际计费。
6. 只运行单 API 进程与同主机 worker。应用速率是每进程共享固定窗口，窗口边界短时可能放行两批请求。单客户端可消耗整组配额，公网限流需要反向代理每 IP 保护。SQLite 的持久预算持续覆盖服务进程重启。

## 发布前检查

- 确认遗留模型凭据已在供应商侧撤销，并检查使用记录。首次推送前确认 GitHub 授权及历史处理方案。复核不会把环境文件、恢复点、卷转储或私有知识库送到远端。
- 用可信方式固定部署镜像标记或镜像 digest，并记录依赖清单及模型来源。源项目保留用户的 HF_ENDPOINT 镜像配置，公网主机按自身网络和供应链审查选择模型来源。
- 检查 Docker 卷属主。应用镜像用 UID/GID 10001；现有卷以相同身份挂载。备份 SQLite 使用在线备份机制或在服务停止后复制卷，备份向量服务按同一时间点使用 Qdrant 快照。备份还应包含上传原件与分块。
- 使用实际样本文档检查就绪、入库、索引修订版和检索，从已知问题检查 API 429 和 Retry-After。Worker 通过 celery inspect ping 独立检查。
- 如需自动恢复，用宿主机定时任务执行 app.recovery。默认 900 秒后置为候选，执行过程中同一版本使用文件锁，成功预留后 60 秒内重复补偿跳过。监控无法派发任务、工作进程异常、SQLite 占用与磁盘。
- 上传已做大小和页数约束，压缩 PDF 仍可能在解析期间消耗资源。开发和管理人员应验证来源，Worker 有任务时间与容器内存上限。

## 回滚

保留前一次验收过的镜像、配置文件、副本数据卷和备份。API 发布问题优先切回前一版本镜像，再执行 docker compose up -d --no-build --wait。回滚配置时保留 API、Redis 和 Qdrant 的安全监听地址以及当前密钥撤销状态。回滚数据库只在确认写入窗口后操作。若历史备份包含较早的付费预算计数，先关闭付费问答，再核对供应商剩余额度。停止临时项目可用 docker compose stop。已有数据卷由对应项目继续管理。

源码变更应用 git revert 逐个撤销已提交的修复。审计前恢复点包含 Git bundle、原始 Compose 文件、二进制 diff 和审计前未跟踪的测试样本。只在本机受控目录使用恢复点。

## 免费 ARM64 单机（Oracle Cloud Always Free）

目标形态：`VM.Standard.A1.Flex`，2 OCPU / 12 GB 内存，Ubuntu 24.04 LTS，引导卷 50–100 GB（在 Always Free 的 200 GB 总额度内），一块公网 IPv4。该规格足以同时运行 API、Worker、Redis、Qdrant 与两份 ONNX 模型；实测本地同规模空闲占用约 0.8 GB，BGE 推理峰值再增加约 1 GB。

ARM64 依赖核查（2026-11-25，按 `constraints.txt` 的 106 个锁定包逐个查询 PyPI）：29 个直接提供 aarch64 wheel，76 个为纯 Python，`jieba` 只有源码包但本身是纯 Python，可正常安装。Qdrant 与 Redis 官方镜像均发布 linux/arm64。因此无需改写 Dockerfile 或依赖。

```bash
# 1) 主机准备
sudo apt update && sudo apt install -y ca-certificates curl python3
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"    # 重新登录生效
docker compose version             # 需 >= 2.24.4

# 2) 代码与配置
git clone https://github.com/<owner>/QueryForge.git /opt/queryforge
cd /opt/queryforge
bash scripts/deploy_demo.sh        # 生成 .env.demo、建镜像、启动并等待就绪

# 3) 演示语料（使用 20 篇公开文档）
gedit .env.demo                    # 填入 LLM_API_KEY 与 DEMO_LLM_TOTAL_CALLS
bash scripts/deploy_demo.sh --seed # 输出 20 个 version_id
# 将 version_id 写入 .env.demo 的 DEMO_PUBLIC_VERSION_IDS，然后重建 API
docker compose --env-file .env.demo -f docker-compose.yml -f docker-compose.demo.yml \
  up -d --no-deps --force-recreate api

# 4) HTTPS（无需购买域名）：公网 IP 的 sslip.io 名称
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/{\$QF_DOMAIN}/<public-ip>.sslip.io/; s/{\$QF_ACME_EMAIL}/admin@example.com/' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

2 vCPU 实例建议在 `.env.demo` 收敛以下参数（默认值面向本机 8 核）：

```text
MAX_CONCURRENT_REQUESTS=8
RETRIEVAL_WORKERS=2
RETRIEVAL_MAX_PENDING=8
CHAT_REQUESTS_PER_MINUTE=6
API_REQUESTS_PER_MINUTE=300
DEMO_LLM_TOTAL_CALLS=200
```

注意：Oracle 的 Always Free 实例可能因账户闲置策略被回收，需按账户邮件保持活跃；免费容量按区域动态供应，创建失败属于容量问题而非配置问题；注册需要账户持有人完成实名与支付方式验证，无法由脚本代办。

上线后按"发布前检查"一节逐项验证：`/api/v1/ready`、匿名检索、匿名上传 401、`/docs` 404、HTTPS 证书、SSE 不被缓冲、重启后数据卷仍在。
