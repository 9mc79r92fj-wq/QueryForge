import unittest

from uuid import uuid4
from unittest.mock import patch

from app.schemas import (
    SearchHit,
    SearchRequest,
    SearchMode
)

from app.rrf import reciprocal_rank_fusion

from app.index_snapshot import IndexSnapshot

from app.retrieval_service import (
    retrieve,
    RetrievalUnavailable
)


class TestRetrieval(unittest.TestCase):

    def setUp(self):

        self.document_id = uuid4()
        self.version_id = uuid4()

        self.a = self.make_hit("Redis AOF")
        self.b = self.make_hit("Redis RDB")
        self.c = self.make_hit("Celery Worker")

        self.snapshot = IndexSnapshot(
            revision=1,
            version_ids=(str(self.version_id),)
        )

    def make_hit(self, text):

        return SearchHit(
            document_id=self.document_id,
            version_id=self.version_id,
            chunk_id=uuid4(),
            filename="manual.txt",
            chunk_index=0,
            text=text,
            score=0.9
        )

    def test_rrf_ranking(self):

        hits = reciprocal_rank_fusion(
            [[self.a, self.b],
             [self.c, self.a]]
        )

        self.assertEqual(
            hits[0].chunk_id,
            self.a.chunk_id
        )

        self.assertAlmostEqual(
            hits[0].score,
            1 / 61 + 1 / 62
        )

    def test_rrf_deduplication(self):

        hits = reciprocal_rank_fusion(
            [[self.a, self.a], [self.a]]
        )

        self.assertEqual(len(hits), 1)

        self.assertAlmostEqual(
            hits[0].score,
            2 / 61
        )

    def test_original_score_unchanged(self):

        reciprocal_rank_fusion(
            [[self.a], [self.a]]
        )

        self.assertEqual(self.a.score, 0.9)

    def test_weighted_rrf(self):

        hits = reciprocal_rank_fusion(
            [[self.a], [self.b]],
            weights=[0.8, 0.2]
        )

        self.assertEqual(
            hits[0].chunk_id,
            self.a.chunk_id
        )

    def test_parallel_retrieval(self):

        with patch(
            "app.retrieval_service.lexical_search",
            return_value=[self.a]
        ) as lexical, patch(
            "app.retrieval_service.dense_search",
            return_value=[self.a, self.b]
        ) as dense:

            result = retrieve(
                SearchRequest(
                    query="Redis",
                    mode=SearchMode.HYBRID
                ),
                self.snapshot
            )

        self.assertFalse(result.degraded)
        self.assertEqual(len(result.hits), 2)

        lexical.assert_called_once()
        dense.assert_called_once()

        self.assertIs(
            lexical.call_args.args[1],
            dense.call_args.args[1]
        )

    def test_dense_failure(self):

        with patch(
            "app.retrieval_service.lexical_search",
            return_value=[self.a]
        ), patch(
            "app.retrieval_service.dense_search",
            side_effect=OSError("Unavailable")
        ):

            result = retrieve(
                SearchRequest(
                    query="Redis",
                    mode=SearchMode.HYBRID
                ),
                self.snapshot
            )

        self.assertTrue(result.degraded)

        self.assertEqual(
            result.effective_mode,
            SearchMode.BM25
        )

    def test_all_routes_failed(self):

        with patch(
            "app.retrieval_service.lexical_search",
            side_effect=OSError()
        ), patch(
            "app.retrieval_service.dense_search",
            side_effect=OSError()
        ):

            with self.assertRaises(
                RetrievalUnavailable
            ):

                retrieve(
                    SearchRequest(
                        query="Redis",
                        mode=SearchMode.HYBRID
                    ),
                    self.snapshot
                )


if __name__ == "__main__":
    unittest.main()