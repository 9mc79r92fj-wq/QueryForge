import json
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from app import index_snapshot


class TestPublicVersionIds(unittest.TestCase):

    def setUp(self):

        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "public.json"
        self.env_id = uuid4()
        self.file_id = uuid4()
        self.second_id = uuid4()
        self.path.write_text(
            json.dumps([str(self.file_id)]),
            encoding="utf-8"
        )
        settings = type(
            "S",
            (),
            {
                "demo_public_version_ids": (self.env_id,),
                "demo_public_version_file": self.path
            }
        )()
        self.patcher = patch.object(
            index_snapshot,
            "get_settings",
            return_value=settings
        )
        self.patcher.start()
        index_snapshot._extra_cache = None

    def tearDown(self):

        self.patcher.stop()
        index_snapshot._extra_cache = None
        self.temp.cleanup()

    def test_env_and_file_are_merged(self):

        result = index_snapshot.public_version_ids()

        self.assertEqual(
            result,
            (self.env_id, self.file_id)
        )

    def test_file_change_invalidates_cache(self):

        index_snapshot.public_version_ids()

        self.path.write_text(
            json.dumps([str(self.second_id)]),
            encoding="utf-8"
        )
        import os
        stat = self.path.stat()
        os.utime(
            self.path,
            (stat.st_atime, stat.st_mtime + 5)
        )

        result = index_snapshot.public_version_ids()

        self.assertEqual(result, (self.env_id, self.second_id))

    def test_invalid_file_is_ignored(self):

        self.path.write_text("{not json", encoding="utf-8")
        index_snapshot._extra_cache = None

        with self.assertLogs("app.index_snapshot", level="WARNING"):
            result = index_snapshot.public_version_ids()

        self.assertEqual(result, (self.env_id,))

    def test_deduplicates(self):

        self.path.write_text(
            json.dumps([str(self.env_id)]),
            encoding="utf-8"
        )
        index_snapshot._extra_cache = None

        result = index_snapshot.public_version_ids()

        self.assertEqual(result, (self.env_id,))


if __name__ == "__main__":
    unittest.main()
