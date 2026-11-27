from celery import Celery

from app.config import get_settings


settings = get_settings()


celery_app = Celery(
    "queryforge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"]
)


celery_app.conf.update(

    # 序列化与安全
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # 时间设置
    timezone="UTC",
    enable_utc=True,

    # 任务执行状态
    task_track_started=True,
    task_ignore_result=False,
    result_expires=86400,

    # 消息可靠性
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Worker 调度
    worker_prefetch_multiplier=1,

    # Broker 连接恢复
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,

    # 任务执行时间限制
    task_soft_time_limit=540,
    task_time_limit=600,

    # Redis 消息可见性超时
    broker_transport_options={
        "visibility_timeout": 3600
    },

    result_backend_transport_options={
        "visibility_timeout": 3600
    },

    visibility_timeout=3600,

    # 默认任务队列
    task_default_queue="queryforge"
)