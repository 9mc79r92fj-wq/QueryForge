import json
import unittest

from pathlib import Path
from tempfile import TemporaryDirectory

from app import query_features, query_router


class TestQueryFeatures(unittest.TestCase):

    def test_technical_identifier(self):

        features = query_features.extract("BM25 的 k1 参数怎么调")

        self.assertGreaterEqual(features.technical_hits, 1)
        self.assertTrue(features.is_question)
        self.assertGreater(features.cjk_ratio, 0.3)

    def test_error_code(self):

        features = query_features.extract("接口返回 HTTP 429 怎么办")

        self.assertTrue(features.has_error_code)

    def test_empty_and_pure_ascii(self):

        features = query_features.extract("redis aof persistence")

        self.assertFalse(features.is_question)
        self.assertEqual(features.cjk_ratio, 0.0)


class TestClassify(unittest.TestCase):

    def _classify(self, text: str) -> str:

        return query_router.classify(query_features.extract(text))

    def test_keyword(self):

        self.assertEqual(self._classify("HNSW 的 M 参数是多少"), "keyword")

    def test_semantic(self):

        self.assertEqual(self._classify("如何防止缓存穿透"), "semantic")

    def test_mixed(self):

        self.assertEqual(self._classify("为什么 BM25 效果不好"), "mixed")

    def test_balanced(self):

        self.assertEqual(self._classify("知识库检索"), "balanced")


class TestRouteV2(unittest.TestCase):

    def test_weights_normalised(self):

        decision = query_router.route_query_v2("如何防止缓存穿透")

        self.assertAlmostEqual(
            decision.bm25_weight + decision.dense_weight, 1.0, places=6
        )
        self.assertEqual(decision.version, query_router.ROUTER_VERSION)
        self.assertIn(decision.strategy, {"bm25", "dense", "hybrid"})

    def test_policy_file_overrides(self):

        with TemporaryDirectory() as tmp:

            path = Path(tmp) / "policy.json"
            path.write_text(
                json.dumps({"weights": {"semantic": {"bm25": 1, "dense": 3}}}),
                encoding="utf-8",
            )

            decision = query_router.route_query_v2(
                "如何防止缓存穿透", policy_path=path
            )

        self.assertAlmostEqual(decision.dense_weight, 0.75)

    def test_invalid_policy_falls_back(self):

        with TemporaryDirectory() as tmp:

            path = Path(tmp) / "policy.json"
            path.write_text("{broken", encoding="utf-8")

            with self.assertLogs("app.query_router", level="WARNING"):
                decision = query_router.route_query_v2(
                    "如何防止缓存穿透", policy_path=path
                )

        self.assertAlmostEqual(decision.dense_weight, 0.75)

    def test_second_retrieval_requires_budget(self):

        decision = query_router.route_query_v2(
            "这是一个比较长的问题，需要额外一次检索", latency_budget_ms=200
        )

        self.assertFalse(decision.second_retrieval)

    def test_v1_router_remains(self):

        decision = query_router.route_query("HNSW 的 M 参数")

        self.assertEqual(decision.query_type, "keyword")


class TestSelectiveRerank(unittest.TestCase):

    def _decision(self, requested=True):

        return query_router.route_query_v2(
            "缓存穿透怎么防", rerank_requested=requested
        )

    def test_not_requested(self):

        allowed, _ = query_router.should_rerank(
            self._decision(False), [0.5, 0.49]
        )

        self.assertFalse(allowed)

    def test_budget_too_low(self):

        allowed, reason = query_router.should_rerank(
            self._decision(), [0.5, 0.49], latency_budget_ms=100
        )

        self.assertFalse(allowed)
        self.assertIn("budget", reason)

    def test_confident_ranking_skips(self):

        allowed, reason = query_router.should_rerank(
            self._decision(), [0.9, 0.2], latency_budget_ms=5000
        )

        self.assertFalse(allowed)
        self.assertIn("confident", reason)

    def test_close_candidates_trigger(self):

        allowed, reason = query_router.should_rerank(
            self._decision(), [0.50, 0.48], latency_budget_ms=5000
        )

        self.assertTrue(allowed)
        self.assertIn("reranking justified", reason)


if __name__ == "__main__":
    unittest.main()
