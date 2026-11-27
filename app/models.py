from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    CheckConstraint,
    Index,
    Enum,
    Text,
    func,
    text,
)

from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
)

from app.schemas import TaskStatus


class Base(DeclarativeBase):
    pass


class Document(Base):

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4())
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp()
    )


class DocumentVersion(Base):

    __tablename__ = "document_versions"

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_version"
        ),

        UniqueConstraint(
            "document_id",
            "content_hash",
            name="uq_document_content"
        ),

        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_version_progress"
        ),

        CheckConstraint(
            "version_number >= 1",
            name="ck_version_number"
        ),

        CheckConstraint(
            "chunk_count >= 0",
            name="ck_chunk_count"
        ),

        CheckConstraint(
            "is_active = 0 OR "
            "(status = 'completed' AND progress = 100)",
            name="ck_active_version_completed"
        ),

        Index(
            "uq_active_document_version",
            "document_id",
            unique=True,
            sqlite_where=text("is_active = 1")
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4())
    )

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id"),
        nullable=False,
        index=True
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False
    )

    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )

    task_id: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        nullable=False
    )

    status: Mapped[TaskStatus] = mapped_column(
        Enum(
            TaskStatus,
            values_callable=lambda items: [
                item.value for item in items
            ],
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            name="task_status"
        ),
        default=TaskStatus.QUEUED,
        nullable=False
    )

    progress: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )

    chunk_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp()
    )


class IndexState(Base):

    __tablename__ = "index_state"

    __table_args__ = (
        CheckConstraint("id = 1"),
        CheckConstraint("revision >= 0"),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    revision: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )


class IngestionControl(Base):

    __tablename__ = "ingestion_control"

    __table_args__ = (
        CheckConstraint("attempts >= 0"),
    )

    version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id"),
        primary_key=True
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )


class RecoveryDispatch(Base):
    __tablename__ = "recovery_dispatches"

    version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id"), primary_key=True
    )
    reserved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class DemoBudget(Base):
    __tablename__ = "demo_budget"
    __table_args__ = (CheckConstraint("id = 1"), CheckConstraint("calls >= 0"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
