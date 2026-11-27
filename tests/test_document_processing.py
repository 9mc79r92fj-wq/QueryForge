import unittest

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from pypdf import PdfWriter

from app.document_processing import (
    clean_text,
    parse_document,
    split_pages,
    save_chunks,
    load_chunks
)


class TestDocumentProcessing(unittest.TestCase):

    def setUp(self):

        self.temp = TemporaryDirectory()

        self.directory = Path(self.temp.name)

        self.document_id = str(uuid4())
        self.version_id = str(uuid4())

    def tearDown(self):

        self.temp.cleanup()

    def make_chunks(self):

        return split_pages(
            pages=[
                (1, "Redis支持持久化。" * 200),
                (2, "Celery支持异步任务。" * 200)
            ],
            document_id=self.document_id,
            version_id=self.version_id,
            filename="manual.pdf"
        )

    def test_clean_text(self):

        self.assertEqual(
            clean_text("  Redis\r\nCelery\x00  "),
            "Redis\nCelery"
        )

    def test_txt_parsing(self):

        path = self.directory / "upload"

        path.write_text(
            "Redis supports AOF.",
            encoding="utf-8"
        )

        result = parse_document(
            path,
            "manual.txt"
        )

        self.assertEqual(
            result,
            [(None, "Redis supports AOF.")]
        )

    def test_empty_pdf(self):

        path = self.directory / "empty.pdf"

        writer = PdfWriter()

        writer.add_blank_page(
            width=200,
            height=200
        )

        with path.open("wb") as file:

            writer.write(file)

        with self.assertRaises(ValueError):

            parse_document(
                path,
                "empty.pdf"
            )

    def test_deterministic_chunk_ids(self):

        first = self.make_chunks()
        second = self.make_chunks()

        self.assertGreater(len(first), 2)

        self.assertEqual(
            first,
            second
        )

        self.assertEqual(
            len({chunk.chunk_id for chunk in first}),
            len(first)
        )

        self.assertTrue(
            all(
                len(chunk.text) <= 500
                for chunk in first
            )
        )

        self.assertEqual(
            {chunk.page for chunk in first},
            {1, 2}
        )

    def test_chunk_persistence(self):

        chunks = self.make_chunks()

        save_chunks(
            self.directory,
            self.version_id,
            chunks
        )

        restored = load_chunks(
            self.directory,
            self.version_id
        )

        self.assertEqual(chunks, restored)


if __name__ == "__main__":

    unittest.main()