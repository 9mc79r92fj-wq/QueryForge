import logging

from celery.exceptions import SoftTimeLimitExceeded

from sqlalchemy.exc import OperationalError

from app.celery_app import celery_app
from app.config import get_settings
from app.schemas import TaskStatus

from filelock import Timeout

from celery.exceptions import Ignore

from app.task_lock import get_task_lock
from app.task_service import (
    TERMINAL_STATES,
    begin_attempt,
    update_task
)


settings = get_settings()

logger = logging.getLogger(__name__)


@celery_app.task(name="queryforge.worker_ping")
def worker_ping(message: str) -> dict[str, str]:

    return {
        "status": "success",
        "message": message,
        "worker": "QueryForge Celery Worker"
    }


@celery_app.task(
    bind=True,
    name="queryforge.ingest_document",
    max_retries=settings.max_retries
)
def ingest_document(self, version_id: str) -> dict:

    lock = get_task_lock(version_id)

    try:

        lock.acquire()

    except Timeout:

        logger.warning(
            "Duplicate concurrent task ignored: %s",
            version_id
        )

        raise Ignore()

    try:

        return execute_ingestion(
            self,
            version_id
        )

    finally:

        lock.release()


def execute_ingestion(
    self,
    version_id: str
) -> dict:

    from app.ingestion import run_ingestion

    try:

        status = begin_attempt(version_id)

        if status in TERMINAL_STATES:

            return {
                "version_id": version_id,
                "status": status.value,
                "skipped": True
            }

        return run_ingestion(version_id)

    except (ValueError, FileNotFoundError):

        logger.exception(
            "Invalid document: %s",
            version_id
        )

        update_task(
            version_id,
            TaskStatus.FAILED,
            error_code="INVALID_DOCUMENT",
            error_message="Document validation failed"
        )

        raise

    except (
        OSError,
        OperationalError,
        SoftTimeLimitExceeded
    ) as exc:

        logger.exception(
            "Temporary ingestion failure: %s",
            version_id
        )

        if self.request.retries >= self.max_retries:

            update_task(
                version_id,
                TaskStatus.FAILED,
                error_code="RETRY_EXHAUSTED",
                error_message="Maximum retries exceeded"
            )

            raise

        delay = min(
            2 ** self.request.retries * 5,
            60
        )

        update_task(
            version_id,
            TaskStatus.RETRYING,
            error_code="TEMPORARY_FAILURE",
            error_message="Task will retry"
        )

        raise self.retry(
            exc=exc,
            countdown=delay
        )

    except Exception:

        logger.exception(
            "Unexpected ingestion failure: %s",
            version_id
        )

        update_task(
            version_id,
            TaskStatus.FAILED,
            error_code="INTERNAL_ERROR",
            error_message="Document processing failed"
        )

        raise