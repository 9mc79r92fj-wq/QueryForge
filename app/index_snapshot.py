import json
import logging

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text

from app.config import get_settings
from app.database import SessionLocal
from app.models import DocumentVersion, IndexState
from app.schemas import TaskStatus


logger = logging.getLogger(__name__)

_extra_cache: tuple[tuple[float, int], tuple[UUID, ...]] | None = None


def public_version_ids() -> tuple[UUID, ...]:
    """Union of the configured whitelist and the optional runtime file.

    Single-container deployments cannot edit environment variables at runtime,
    so an operator bootstrap may publish approved versions through a JSON file.
    The file is cached by modification time to keep snapshot reads cheap.
    """

    settings = get_settings()

    ids = list(settings.demo_public_version_ids)

    path: Path | None = settings.demo_public_version_file

    if path is not None:

        global _extra_cache

        try:
            stat = path.stat()
        except OSError:
            stat = None

        if stat is not None:

            key = (stat.st_mtime, stat.st_size)

            if _extra_cache is not None and _extra_cache[0] == key:

                extra = _extra_cache[1]

            else:

                try:

                    payload = json.loads(
                        path.read_text(encoding="utf-8")
                    )

                    extra = tuple(
                        UUID(str(item))
                        for item in payload
                    )

                except (OSError, ValueError, TypeError):

                    logger.warning(
                        "Ignoring unreadable public version file"
                    )

                    extra = ()

                _extra_cache = (key, extra)

            ids.extend(extra)

    return tuple(dict.fromkeys(ids))


@dataclass(frozen=True)
class IndexSnapshot:

    revision: int
    version_ids: tuple[str, ...]


def get_index_snapshot() -> IndexSnapshot:

    with SessionLocal() as session:

        session.execute(text("BEGIN"))

        state = session.get(IndexState, 1)

        if state is None:
            raise RuntimeError(
                "Index state not initialized"
            )

        statement = select(DocumentVersion.id).where(
            DocumentVersion.is_active.is_(True),
            DocumentVersion.status == TaskStatus.COMPLETED
        )
        settings = get_settings()
        if settings.run_mode == "demo":
            approved = public_version_ids()
            statement = statement.where(
                DocumentVersion.id.in_([str(version) for version in approved])
            )
        version_ids = session.scalars(statement.order_by(DocumentVersion.id)).all()

        return IndexSnapshot(
            revision=state.revision,
            version_ids=tuple(version_ids)
        )
