# ModelScope Docker Studio image for QueryForge.
#
# Single container: FastAPI (public :7860), Celery worker, Redis and Qdrant
# listen on loopback only. Runtime data lives on the container disk; the
# persistent /mnt/workspace volume keeps model caches and periodic snapshots.
#
# ModelScope requires the service on 0.0.0.0:7860 and does not support
# Docker Compose or Docker-in-Docker, so process supervision is handled by
# supervisord instead of the local compose stack.

FROM modelscope-registry.cn-beijing.cr.aliyuncs.com/modelscope-repo/python:3.11

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HOME=/mnt/workspace/qf/hf \
    DATA_DIR=/app/runtime/data \
    DATABASE_URL=sqlite:////app/runtime/data/queryforge.db \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    CELERY_BROKER_URL=redis://127.0.0.1:6379/1 \
    CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/2 \
    QDRANT_URL=http://127.0.0.1:6333 \
    QDRANT_COLLECTION=queryforge_documents \
    RUN_MODE=demo \
    DEMO_SECURE_COOKIE=true \
    DEMO_RAG_ENABLED=true \
    DEMO_LLM_TOTAL_CALLS=300 \
    DEMO_PUBLIC_VERSION_FILE=/mnt/workspace/qf/public_versions.json \
    API_REQUESTS_PER_MINUTE=300 \
    CHAT_REQUESTS_PER_MINUTE=6 \
    MAX_CONCURRENT_REQUESTS=8 \
    RETRIEVAL_WORKERS=2 \
    RETRIEVAL_MAX_PENDING=8 \
    CSP_FRAME_ANCESTORS="https://www.modelscope.cn https://modelscope.cn" \
    BUILD_REVISION="ms-csp-20261125"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        redis-server supervisor curl ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt constraints.txt ./

RUN python -m pip install --no-cache-dir \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        -r requirements.txt

ARG QDRANT_VERSION=v1.20.2
RUN curl -fsSL -o /tmp/qdrant.tar.gz \
        "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/qdrant-x86_64-unknown-linux-musl.tar.gz" \
    && tar -xzf /tmp/qdrant.tar.gz -C /usr/local/bin qdrant \
    && chmod +x /usr/local/bin/qdrant \
    && rm /tmp/qdrant.tar.gz

COPY app ./app
COPY scripts ./scripts
COPY examples ./examples
COPY deploy/ms ./deploy/ms

RUN chmod +x /app/deploy/ms/*.sh

EXPOSE 7860

ENTRYPOINT ["/bin/bash", "/app/deploy/ms/start.sh"]
