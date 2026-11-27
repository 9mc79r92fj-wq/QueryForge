import argparse
import logging

from datetime import (
    datetime,
    timedelta,
    timezone
)

from filelock import Timeout

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.sqlite import insert

from app.database import SessionLocal
from app.models import DocumentVersion, RecoveryDispatch

from app.task_service import TERMINAL_STATES

from app.task_lock import get_task_lock
from app.tasks import ingest_document


logger = logging.getLogger(__name__)
DISPATCH_COOLDOWN = timedelta(seconds=60)


def recover_tasks(
    min_age: int = 900,
    limit: int = 20,
    task_id: str | None = None
) -> dict[str, int]:

    if min_age < 0 or not 1 <= limit <= 100:

        raise ValueError(
            "Invalid recovery parameters"
        )

    if min_age < 900 and task_id is None:

        raise ValueError(
            "Short recovery interval requires task_id"
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(seconds=min_age)
    dispatch_cutoff = now - DISPATCH_COOLDOWN

    with SessionLocal() as session:

        statement = select(
            DocumentVersion
        ).outerjoin(RecoveryDispatch).where(

            DocumentVersion.status.notin_(
                list(TERMINAL_STATES)
            ),

            DocumentVersion.updated_at <= cutoff,
            or_(RecoveryDispatch.reserved_at.is_(None),
                RecoveryDispatch.reserved_at <= dispatch_cutoff)
        )

        if task_id is not None:

            statement = statement.where(
                DocumentVersion.task_id == task_id
            )

        statement = statement.order_by(
            DocumentVersion.updated_at
        ).limit(limit)

        candidates = session.scalars(
            statement
        ).all()

        records = [
            (row.id, row.task_id)
            for row in candidates
        ]

    stats = {
        "scanned": len(records),
        "requeued": 0,
        "skipped": 0,
        "failed": 0
    }

    for version_id, original_task_id in records:

        lock = get_task_lock(version_id)

        try:

            with lock:

                with SessionLocal.begin() as session:

                    session.execute(text("BEGIN IMMEDIATE"))
                    version = session.get(
                        DocumentVersion,
                        version_id
                    )

                    if (
                        version is None
                        or version.status in TERMINAL_STATES
                        or version.updated_at > cutoff
                    ):

                        stats["skipped"] += 1

                        continue

                    claim = insert(RecoveryDispatch).values(
                        version_id=version_id, reserved_at=now
                    )
                    reserved = session.execute(claim.on_conflict_do_update(
                        index_elements=[RecoveryDispatch.version_id],
                        set_={"reserved_at": now},
                        where=RecoveryDispatch.reserved_at <= dispatch_cutoff
                    ))
                    if reserved.rowcount != 1:
                        stats["skipped"] += 1
                        continue

        except Timeout:

            stats["skipped"] += 1

            continue

        try:

            ingest_document.apply_async(

                args=[version_id],

                task_id=original_task_id,

                retry=False
            )

            stats["requeued"] += 1

        except Exception as exc:

            logger.warning(
                "Task recovery dispatch failed: %s %s", version_id, type(exc).__name__
            )

            stats["failed"] += 1

    return stats


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--min-age",
        type=int,
        default=900
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20
    )

    parser.add_argument(
        "--task-id",
        default=None
    )

    args = parser.parse_args()

    print(

        recover_tasks(

            min_age=args.min_age,

            limit=args.limit,

            task_id=args.task_id
        )
    )
