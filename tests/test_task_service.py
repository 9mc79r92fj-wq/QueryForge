import unittest

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from app.database import build_engine, init_db
from app.models import Document, DocumentVersion
from app.schemas import TaskStatus

from app.task_service import (
    begin_attempt,
    update_task,
    get_task_status
)


class TestTaskService(unittest.TestCase):
    
    def test_total_attempt_limit(self):

        from app.task_service import (
            MAX_TOTAL_ATTEMPTS
        )

        for _ in range(MAX_TOTAL_ATTEMPTS):

            status = begin_attempt(
                self.version_id
            )

            self.assertEqual(
                status,
                TaskStatus.PARSING
            )

        status = begin_attempt(
            self.version_id
        )

        self.assertEqual(
            status,
            TaskStatus.FAILED
        )

        result = get_task_status(
            self.task_id
        )

        self.assertEqual(
            result.error.code,
            "ATTEMPT_LIMIT_EXCEEDED"
        )
        
    def setUp(self):

        self.temp = TemporaryDirectory()

        path = Path(self.temp.name) / "test.db"

        self.engine = build_engine(
            f"sqlite:///{path.as_posix()}"
        )

        init_db(self.engine)

        self.factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False
        )

        self.patcher = patch(
            "app.task_service.SessionLocal",
            self.factory
        )

        self.patcher.start()

        with self.factory.begin() as session:

            document = Document(name="test")

            session.add(document)
            session.flush()

            version = DocumentVersion(
                document_id=document.id,
                version_number=1,
                filename="test.txt",
                content_hash="a" * 64,
                file_path="uploads/test",
                task_id=str(uuid4())
            )

            session.add(version)
            session.flush()

            self.version_id = version.id
            self.task_id = version.task_id

    def tearDown(self):

        self.patcher.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def test_start_task(self):

        begin_attempt(self.version_id)

        result = get_task_status(self.task_id)

        self.assertEqual(
            result.status,
            TaskStatus.PARSING
        )

        self.assertEqual(result.progress, 5)

    def test_progress_update(self):

        begin_attempt(self.version_id)

        update_task(
            self.version_id,
            TaskStatus.CHUNKING,
            progress=30
        )

        result = get_task_status(self.task_id)

        self.assertEqual(result.progress, 30)

    def test_retry_state(self):

        begin_attempt(self.version_id)

        update_task(
            self.version_id,
            TaskStatus.RETRYING,
            error_code="TEMPORARY_FAILURE"
        )

        begin_attempt(self.version_id)

        result = get_task_status(self.task_id)

        self.assertEqual(
            result.status,
            TaskStatus.PARSING
        )

        self.assertIsNone(result.error)

    def test_terminal_state(self):

        update_task(
            self.version_id,
            TaskStatus.FAILED
        )

        status = begin_attempt(self.version_id)

        self.assertEqual(
            status,
            TaskStatus.FAILED
        )

        changed = update_task(
            self.version_id,
            TaskStatus.PARSING
        )

        self.assertFalse(changed)

    def test_invalid_progress(self):

        with self.assertRaises(ValueError):

            update_task(
                self.version_id,
                TaskStatus.PARSING,
                progress=101
            )

    def test_premature_completion(self):

        with self.assertRaises(ValueError):

            update_task(
                self.version_id,
                TaskStatus.COMPLETED,
                progress=100
            )


if __name__ == "__main__":
    unittest.main()