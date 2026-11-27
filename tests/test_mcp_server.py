import unittest

from uuid import uuid4

from unittest.mock import patch

from pydantic import ValidationError

from app.mcp_server import (
    search_knowledge,
    get_document_metadata
)

from app.schemas import (
    SearchResponse,
    SearchMode
)


class TestMCPServer(unittest.TestCase):

    def test_search_tool(self):

        response = SearchResponse(

            query="Redis",

            requested_mode=SearchMode.ADAPTIVE,

            effective_mode=SearchMode.ADAPTIVE,

            index_revision=1,

            hits=[],

            latency_ms=1.0
        )

        with patch(

            "app.mcp_server.run_search",

            return_value=response

        ) as search:

            result = search_knowledge(
                "Redis",
                3
            )

            search.assert_called_once_with(
                query="Redis",
                top_k=3
            )

        self.assertEqual(
            result,
            response
        )

    def test_invalid_top_k(self):

        with self.assertRaises(
            ValidationError
        ):

            search_knowledge(
                "Redis",
                100
            )

    def test_metadata_found(self):

        document_id = uuid4()

        with patch(

            "app.mcp_server.run_metadata",

            return_value={
                "filename": "redis.txt"
            }

        ):

            result = get_document_metadata(
                document_id
            )

        self.assertEqual(
            result["status"],
            "ok"
        )

    def test_metadata_not_found(self):

        with patch(

            "app.mcp_server.run_metadata",

            return_value=None

        ):

            result = get_document_metadata(
                uuid4()
            )

        self.assertEqual(
            result["status"],
            "not_found"
        )

    def test_retrieval_failure(self):

        with patch(

            "app.mcp_server.run_search",

            side_effect=OSError()

        ):

            with self.assertRaises(
                RuntimeError
            ):

                search_knowledge(
                    "Redis",
                    3
                )


if __name__ == "__main__":

    unittest.main()