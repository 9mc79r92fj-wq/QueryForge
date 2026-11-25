import unittest

from unittest.mock import patch
from uuid import uuid4

from app.query_router import route_query
from app.index_snapshot import IndexSnapshot

from app.schemas import (
    SearchRequest,
    SearchHit,
    SearchMode
)

from app.retrieval_service import retrieve


class TestAdaptiveRetrieval(unittest.TestCase):

    def setUp(self):

        self.document_id = uuid4()
        self.version_id = uuid4()

        self.snapshot = IndexSnapshot(
            revision=1,
            version_ids=(str(self.version_id),)
        )

        self.a = self.make_hit("Redis AOF")
        self.b = self.make_hit("数据恢复机制")

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

    def test_keyword_route(self):

        decision = route_query(
            "ERR_CONNECTION_RESET"
        )

        self.assertEqual(
            decision.query_type,
            "keyword"
        )

        self.assertGreater(
            decision.bm25_weight,
            decision.dense_weight
        )

    def test_semantic_route(self):

        decision = route_query(
            "如何减少系统响应时间"
        )

        self.assertEqual(
            decision.query_type,
            "semantic"
        )

        self.assertGreater(
            decision.dense_weight,
            decision.bm25_weight
        )

    def test_mixed_route(self):

        decision = route_query(
            "Redis AOF 如何恢复数据"
        )

        self.assertEqual(
            decision.query_type,
            "mixed"
        )

    def test_balanced_route(self):

        decision = route_query("Redis")

        self.assertEqual(
            decision.bm25_weight,
            decision.dense_weight
        )

    def test_adaptive_fusion(self):

        with patch(
            "app.retrieval_service.lexical_search",
            return_value=[self.a]
        ), patch(
            "app.retrieval_service.dense_search",
            return_value=[self.b]
        ):

            result = retrieve(
                SearchRequest(
                    query="Redis AOF",
                    mode=SearchMode.ADAPTIVE
                ),
                self.snapshot
            )

        self.assertEqual(
            result.hits[0].chunk_id,
            self.a.chunk_id
        )

        self.assertEqual(
            result.effective_mode,
            SearchMode.ADAPTIVE
        )

        self.assertEqual(
            result.score_type,
            "weighted_rrf"
        )

    def test_reranker_success(self):

        with patch(
            "app.retrieval_service.lexical_search",
            return_value=[self.a, self.b]
        ), patch(
            "app.retrieval_service.rerank_hits",
            return_value=[self.b]
        ):

            result = retrieve(
                SearchRequest(
                    query="数据恢复",
                    mode=SearchMode.BM25,
                    top_k=1,
                    rerank=True
                ),
                self.snapshot
            )

        self.assertTrue(result.rerank_applied)

        self.assertEqual(
            result.hits[0].chunk_id,
            self.b.chunk_id
        )

    def test_reranker_failure(self):

        with patch(
            "app.retrieval_service.lexical_search",
            return_value=[self.a]
        ), patch(
            "app.retrieval_service.rerank_hits",
            side_effect=RuntimeError("Unavailable")
        ):

            result = retrieve(
                SearchRequest(
                    query="Redis",
                    mode=SearchMode.BM25,
                    rerank=True
                ),
                self.snapshot
            )

        self.assertTrue(result.degraded)
        self.assertFalse(result.rerank_applied)
        self.assertEqual(len(result.hits), 1)

        self.assertEqual(
            result.score_type,
            "bm25"
        )


if __name__ == "__main__":

    unittest.main()