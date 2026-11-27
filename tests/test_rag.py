import unittest

from uuid import uuid4
from unittest.mock import patch

from app.schemas import (
    SearchHit,
    SearchResponse,
    SearchMode,
    ChatRequest
)

from app.rag_context import (
    build_context,
    build_messages
)

from app.rag_service import (
    validate_answer,
    answer_question
)


class TestRAG(unittest.TestCase):

    def setUp(self):

        self.hit = SearchHit(

            document_id=uuid4(),

            version_id=uuid4(),

            chunk_id=uuid4(),

            filename="redis.txt",

            chunk_index=0,

            text="Redis支持AOF持久化。",

            score=0.9
        )

        self.sources = {
            "S1": self.hit
        }

    def test_context_mapping(self):

        context, sources = build_context(
            [self.hit]
        )

        self.assertIn("S1", context)

        self.assertEqual(
            sources["S1"].chunk_id,
            self.hit.chunk_id
        )

    def test_context_budget(self):

        context, sources = build_context(
            [self.hit],
            budget=1
        )

        self.assertEqual(context, "")
        self.assertEqual(sources, {})

    def test_prompt_messages(self):

        messages = build_messages(
            "Redis如何持久化",
            "测试资料"
        )

        self.assertEqual(
            len(messages), 2
        )

        self.assertEqual(
            messages[0].type,
            "system"
        )

    def test_valid_citation(self):

        answer, citations, refused = (
            validate_answer(
                "Redis支持AOF。[S1]",
                self.sources
            )
        )

        self.assertFalse(refused)

        self.assertEqual(
            citations[0].chunk_id,
            self.hit.chunk_id
        )

    def test_invalid_citation(self):

        _, citations, refused = (
            validate_answer(
                "Redis支持AOF。[S99]",
                self.sources
            )
        )

        self.assertTrue(refused)
        self.assertEqual(citations, [])

    def test_missing_citation(self):

        _, _, refused = validate_answer(
            "Redis支持AOF。",
            self.sources
        )

        self.assertTrue(refused)

    def test_duplicate_citation(self):

        _, citations, refused = (
            validate_answer(
                "Redis支持AOF。[S1][S1]",
                self.sources
            )
        )

        self.assertFalse(refused)
        self.assertEqual(len(citations), 1)

    def test_empty_retrieval_skips_llm(self):

        result = SearchResponse(

            query="Redis",

            requested_mode=SearchMode.ADAPTIVE,

            effective_mode=SearchMode.ADAPTIVE,

            index_revision=0,

            hits=[],

            latency_ms=1.0
        )

        with patch(
            "app.rag_service.search",
            return_value=result
        ), patch(
            "app.rag_service.get_llm"
        ) as llm:

            response = answer_question(
                ChatRequest(
                    query="Redis",
                    stream=False
                )
            )

        self.assertTrue(response.refused)
        llm.assert_not_called()


if __name__ == "__main__":

    unittest.main()