import unittest

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from filelock import Timeout

from app.task_lock import get_task_lock


class TestReliability(unittest.TestCase):

    def setUp(self):

        self.temp = TemporaryDirectory()

        self.patcher = patch(
            "app.task_lock.settings.data_dir",
            Path(self.temp.name)
        )

        self.patcher.start()

        self.version_id = str(uuid4())

    def tearDown(self):

        self.patcher.stop()

        self.temp.cleanup()

    def test_duplicate_lock_rejected(self):

        first = get_task_lock(
            self.version_id
        )

        second = get_task_lock(
            self.version_id
        )

        with first:

            with self.assertRaises(Timeout):

                second.acquire()

    def test_lock_released(self):

        with get_task_lock(
            self.version_id
        ):
            pass

        with get_task_lock(
            self.version_id
        ):
            pass

    def test_different_versions(self):

        with get_task_lock(
            self.version_id
        ):

            with get_task_lock(
                str(uuid4())
            ):
                pass

    def test_exception_releases_lock(self):

        with self.assertRaises(RuntimeError):

            with get_task_lock(
                self.version_id
            ):

                raise RuntimeError(
                    "Simulated failure"
                )

        with get_task_lock(
            self.version_id
        ):
            pass

    def test_invalid_version_id(self):

        with self.assertRaises(ValueError):

            get_task_lock(
                "../invalid"
            )


if __name__ == "__main__":

    unittest.main()