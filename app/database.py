from pathlib import Path
from collections.abc import Generator

from sqlalchemy import (
    create_engine,
    event,
    Engine,
)

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.dialects.sqlite import insert

from app.config import get_settings
from app.models import Base, IndexState


settings = get_settings()


def build_engine(database_url: str) -> Engine:

    url = make_url(database_url)

    if url.get_backend_name() != "sqlite":
        raise ValueError("Only SQLite is supported")

    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(
            parents=True,
            exist_ok=True
        )

    engine = create_engine(
        database_url,
        connect_args={
            "check_same_thread": False,
            "timeout": 10
        }
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, record):

        cursor = connection.cursor()

        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")

        cursor.close()

    return engine


engine = build_engine(settings.database_url)


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False
)


def get_db() -> Generator[Session, None, None]:

    with SessionLocal() as session:
        yield session


def init_db(db_engine: Engine = engine) -> None:

    with db_engine.connect() as connection:

        connection.exec_driver_sql(
            "PRAGMA journal_mode=WAL"
        ).scalar()

    Base.metadata.create_all(bind=db_engine)

    with db_engine.begin() as connection:

        statement = insert(IndexState).values(
            id=1,
            revision=0
        )

        statement = statement.on_conflict_do_nothing(
            index_elements=["id"]
        )

        connection.execute(statement)


if __name__ == "__main__":

    init_db()

    print("QueryForge database initialized successfully.")