from dataclasses import dataclass

from sqlalchemy import select, text

from app.config import get_settings
from app.database import SessionLocal
from app.models import DocumentVersion, IndexState
from app.schemas import TaskStatus


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
            statement = statement.where(
                DocumentVersion.id.in_([str(version) for version in settings.demo_public_version_ids])
            )
        version_ids = session.scalars(statement.order_by(DocumentVersion.id)).all()

        return IndexSnapshot(
            revision=state.revision,
            version_ids=tuple(version_ids)
        )
