import unittest

from uuid import uuid4

from app.bm25_engine import (
    tokenize,
    BM25Index
)

from app.document_processing import ChunkRecord


class TestBM25Engine(unittest.TestCase):

    def setUp(self):

        document_id = uuid4()
        version_id = uuid4()

        texts = [
            "Redis supports AOF persistence.",
            "Redis supports RDB snapshots.",
            "Celery processes asynchronous tasks.",
            "ERR_CONNECTION_RESET 表示连接被重置。"
        ]

        self.chunks = [

            ChunkRecord(
                document_id=document_id,
                version_id=version_id,
                chunk_id=uuid4(),
                filename="manual.txt",
                page=None,
                chunk_index=index,
                text=text
            )

            for index, text in enumerate(texts)

        ]

        self.engine = BM25Index(
            self.chunks
        )


    def test_english_tokenization(self):

        self.assertEqual(

            tokenize("Redis AOF"),

            ["redis", "aof"]

        )


    def test_error_code_tokenization(self):

        self.assertIn(

            "err_connection_reset",

            tokenize("ERR_CONNECTION_RESET")

        )


    def test_keyword_retrieval(self):

        results = self.engine.search(
            "Redis AOF"
        )

        self.assertGreater(
            len(results),
            0
        )

        self.assertEqual(

            results[0].chunk_id,

            self.chunks[0].chunk_id

        )


    def test_no_matching_documents(self):

        results = self.engine.search(
            "banana"
        )

        self.assertEqual(results, [])


    def test_empty_index(self):

        engine = BM25Index([])

        self.assertEqual(
            engine.search("Redis"),
            []
        )


    def test_top_k(self):

        results = self.engine.search(
            "Redis",
            top_k=1
        )

        self.assertEqual(
            len(results),
            1
        )


    def test_error_code_retrieval(self):

        results = self.engine.search(
            "ERR_CONNECTION_RESET"
        )

        self.assertEqual(

            results[0].chunk_id,

            self.chunks[3].chunk_id

        )


if __name__ == "__main__":

    unittest.main()