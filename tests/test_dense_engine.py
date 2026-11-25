import unittest

from unittest.mock import patch
from uuid import uuid4

from qdrant_client import QdrantClient, models

from app.config import get_settings
from app.embedding import EMBEDDING_DIMENSION
from app.dense_engine import dense_search
from app.index_snapshot import IndexSnapshot


class TestDenseEngine(unittest.TestCase):

    def setUp(self):

        self.client = QdrantClient(":memory:")

        self.collection = (
            get_settings().qdrant_collection
        )

        self.client.create_collection(

            collection_name=self.collection,

            vectors_config=models.VectorParams(
                size=EMBEDDING_DIMENSION,
                distance=models.Distance.COSINE
            )
        )

        self.document_id = str(uuid4())

        self.old_version = str(uuid4())
        self.new_version = str(uuid4())

        self.old_chunk = str(uuid4())
        self.new_chunk = str(uuid4())

        self.vector = (
            [1.0] +
            [0.0] * (EMBEDDING_DIMENSION - 1)
        )

        self.client.upsert(

            collection_name=self.collection,

            points=[

                self.make_point(
                    self.old_chunk,
                    self.old_version,
                    "Old Redis manual"
                ),

                self.make_point(
                    self.new_chunk,
                    self.new_version,
                    "New Redis manual"
                )

            ]

        )

        self.client_patch = patch(

            "app.dense_engine.get_vector_client",

            return_value=self.client
        )

        self.embedding_patch = patch(

            "app.dense_engine.embed_query",

            return_value=self.vector
        )

        self.client_mock = (
            self.client_patch.start()
        )

        self.embedding_mock = (
            self.embedding_patch.start()
        )


    def make_point(
        self,
        chunk_id,
        version_id,
        text
    ):

        return models.PointStruct(

            id=chunk_id,

            vector=self.vector,

            payload={

                "document_id": self.document_id,
                "version_id": version_id,
                "chunk_id": chunk_id,
                "filename": "redis.txt",
                "page": None,
                "chunk_index": 0,
                "text": text

            }

        )


    def tearDown(self):

        self.embedding_patch.stop()
        self.client_patch.stop()

        self.client.close()


    def test_active_version_filter(self):

        snapshot = IndexSnapshot(
            revision=2,
            version_ids=(self.new_version,)
        )

        hits = dense_search(
            "Redis",
            snapshot
        )

        self.assertEqual(len(hits), 1)

        self.assertEqual(
            str(hits[0].version_id),
            self.new_version
        )


    def test_old_snapshot(self):

        snapshot = IndexSnapshot(
            revision=1,
            version_ids=(self.old_version,)
        )

        hits = dense_search(
            "Redis",
            snapshot
        )

        self.assertEqual(len(hits), 1)

        self.assertEqual(
            str(hits[0].version_id),
            self.old_version
        )


    def test_empty_snapshot(self):

        snapshot = IndexSnapshot(
            revision=0,
            version_ids=()
        )

        hits = dense_search(
            "Redis",
            snapshot
        )

        self.assertEqual(hits, [])

        self.embedding_mock.assert_not_called()
        self.client_mock.assert_not_called()


    def test_invalid_top_k(self):

        snapshot = IndexSnapshot(
            revision=1,
            version_ids=(self.old_version,)
        )

        with self.assertRaises(ValueError):

            dense_search(
                "Redis",
                snapshot,
                top_k=0
            )


    def test_invalid_query(self):

        snapshot = IndexSnapshot(
            revision=1,
            version_ids=(self.old_version,)
        )

        with self.assertRaises(ValueError):

            dense_search(
                "   ",
                snapshot
            )


if __name__ == "__main__":

    unittest.main()