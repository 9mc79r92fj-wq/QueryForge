import unittest

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from app.database import build_engine, init_db

from app.models import (
    Document,
    DocumentVersion,
    IndexState
)

from app.schemas import TaskStatus

from app.index_service import activate_version


class TestIndexService(unittest.TestCase):

    def setUp(self):

        self.temp = TemporaryDirectory()

        path = Path(self.temp.name) / "test.db"

        self.engine = build_engine(
            f"sqlite:///{path.as_posix()}"
        )

        init_db(self.engine)

        self.factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False
        )

        self.patcher = patch(
            "app.index_service.SessionLocal",
            self.factory
        )

        self.patcher.start()

        with self.factory.begin() as session:

            document = Document(name="manual")

            session.add(document)
            session.flush()

            self.versions = []

            for number in (1, 2, 3):

                version = DocumentVersion(
                    document_id=document.id,
                    version_number=number,
                    filename="manual.txt",
                    content_hash=str(number) * 64,
                    file_path="uploads/test",
                    task_id=str(uuid4()),
                    status=TaskStatus.INDEXING,
                    progress=95
                )

                session.add(version)
                session.flush()

                self.versions.append(version.id)

    def tearDown(self):

        self.patcher.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def test_activation(self):

        result = activate_version(
            self.versions[0], 3
        )

        self.assertTrue(result["activated"])
        self.assertEqual(result["revision"], 1)

    def test_version_switch(self):

        activate_version(self.versions[0], 3)
        activate_version(self.versions[1], 4)

        with self.factory() as session:

            old = session.get(
                DocumentVersion,
                self.versions[0]
            )

            new = session.get(
                DocumentVersion,
                self.versions[1]
            )

            self.assertFalse(old.is_active)
            self.assertTrue(new.is_active)

    def test_idempotent_activation(self):

        activate_version(self.versions[0], 3)

        result = activate_version(
            self.versions[0], 3
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["revision"], 1)

    def test_out_of_order_completion(self):

        activate_version(self.versions[2], 5)

        result = activate_version(
            self.versions[1], 4
        )

        self.assertFalse(result["activated"])

        with self.factory() as session:

            latest = session.get(
                DocumentVersion,
                self.versions[2]
            )

            self.assertTrue(latest.is_active)

            state = session.get(IndexState, 1)

            self.assertEqual(state.revision, 1)

    def test_invalid_chunk_count(self):

        with self.assertRaises(ValueError):

            activate_version(
                self.versions[0], 0
            )


if __name__ == "__main__":

    unittest.main()