import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import build_engine, init_db
from app.models import Document, DocumentVersion, IndexState
from app.schemas import TaskStatus


class TestDatabase(unittest.TestCase):

    def setUp(self):

        self.temp = TemporaryDirectory()

        path = Path(self.temp.name) / "test.db"

        self.engine = build_engine(
            f"sqlite:///{path.as_posix()}"
        )

        init_db(self.engine)

    def tearDown(self):

        self.engine.dispose()
        self.temp.cleanup()

    def test_initial_revision(self):

        with Session(self.engine) as session:

            state = session.get(IndexState, 1)

            self.assertEqual(state.revision, 0)

    def test_idempotent_initialization(self):

        with Session(self.engine) as session:

            state = session.get(IndexState, 1)
            state.revision = 5
            session.commit()

        init_db(self.engine)

        with Session(self.engine) as session:

            state = session.get(IndexState, 1)

            self.assertEqual(state.revision, 5)

    def test_document_persistence(self):

        with Session(self.engine) as session:

            document = Document(name="Redis手册")

            session.add(document)
            session.commit()

            document_id = document.id

        with Session(self.engine) as session:

            result = session.get(Document, document_id)

            self.assertEqual(result.name, "Redis手册")

    def test_only_one_active_version(self):

        with Session(self.engine) as session:

            document = Document(name="Redis手册")

            session.add(document)
            session.flush()

            for number in (1, 2):

                version = DocumentVersion(
                    document_id=document.id,
                    version_number=number,
                    filename="redis.txt",
                    content_hash=str(number) * 64,
                    file_path=f"uploads/{number}.txt",
                    task_id=str(uuid4()),
                    status=TaskStatus.COMPLETED,
                    progress=100,
                    is_active=True
                )

                session.add(version)

            with self.assertRaises(IntegrityError):

                session.commit()

            session.rollback()


if __name__ == "__main__":

    unittest.main()