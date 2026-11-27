from uuid import UUID

from filelock import FileLock

from app.config import get_settings


settings = get_settings()


def get_task_lock(
    version_id: str
) -> FileLock:

    version_id = str(UUID(version_id))

    lock_dir = settings.data_dir / "locks"

    lock_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    lock_path = (
        lock_dir / f"{version_id}.lock"
    )

    return FileLock(
        str(lock_path),
        timeout=0
    )