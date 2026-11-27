import unittest

from unittest.mock import patch
from uuid import uuid4

from redis.exceptions import RedisError

from app.schemas import (
    SearchRequest,
    SearchResponse,
    SearchMode
)

from app.index_snapshot import IndexSnapshot

from app.retrieval_cache import (
    make_cache_key,
    get_cached_search,
    cache_search,
    CACHE_TTL
)

from app.retrieval_service import search


class TestRetrievalCache(unittest.TestCase):

    def setUp(self):

        self.snapshot = IndexSnapshot(
            revision=1,
            version_ids=(str(uuid4()),)
        )

        self.request = SearchRequest(

            query="Redis AOF",

            mode=SearchMode.HYBRID
        )

        self.result = SearchResponse(

            query=self.request.query,

            requested_mode=SearchMode.HYBRID,

            effective_mode=SearchMode.HYBRID,

            index_revision=1,

            hits=[],

            latency_ms=10.0
        )

        self.key = make_cache_key(
            self.request,
            self.snapshot
        )


    def test_deterministic_key(self):

        second = make_cache_key(
            self.request,
            self.snapshot
        )

        self.assertEqual(
            self.key,
            second
        )


    def test_revision_invalidation(self):

        updated = IndexSnapshot(

            revision=2,

            version_ids=(str(uuid4()),)
        )

        second = make_cache_key(
            self.request,
            updated
        )

        self.assertNotEqual(
            self.key,
            second
        )


    def test_request_isolation(self):

        changed = self.request.model_copy(
            update={"rerank": True}
        )

        second = make_cache_key(
            changed,
            self.snapshot
        )

        self.assertNotEqual(
            self.key,
            second
        )


    def test_cache_write_with_ttl(self):

        with patch(
            "app.retrieval_cache.get_cache_client"
        ) as client:

            success = cache_search(
                self.key,
                self.result
            )

            client.return_value.set.assert_called_once_with(

                self.key,

                self.result.model_dump_json(),

                ex=CACHE_TTL
            )

        self.assertTrue(success)


    def test_degraded_result_not_cached(self):

        degraded = self.result.model_copy(
            update={"degraded": True}
        )

        with patch(
            "app.retrieval_cache.get_cache_client"
        ) as client:

            success = cache_search(
                self.key,
                degraded
            )

            client.assert_not_called()

        self.assertFalse(success)


    def test_redis_failure(self):

        with patch(

            "app.retrieval_cache.get_cache_client",

            side_effect=RedisError("Unavailable")

        ):

            cached = get_cached_search(

                self.key,

                self.request,

                self.snapshot
            )

            self.assertIsNone(cached)

            self.assertFalse(

                cache_search(
                    self.key,
                    self.result
                )
            )


    def test_cache_hit_skips_retrieval(self):

        with patch(

            "app.retrieval_service.get_index_snapshot",

            return_value=self.snapshot

        ), patch(

            "app.retrieval_cache.get_cache_client"

        ) as client, patch(

            "app.retrieval_service.retrieve"

        ) as retrieve:

            client.return_value.get.return_value = (

                self.result.model_dump_json()
            )

            response = search(
                self.request
            )

            self.assertTrue(
                response.cache_hit
            )

            retrieve.assert_not_called()


if __name__ == "__main__":

    unittest.main()