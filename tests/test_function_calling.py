import unittest

from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from langchain_core.messages import AIMessage

from app.index_snapshot import IndexSnapshot

from app.function_calling import (
    SearchArguments,
    MetadataArguments,
    ToolChatRequest,
    execute_tool,
    tool_chat
)


class TestFunctionCalling(unittest.TestCase):

    def setUp(self):

        self.snapshot = IndexSnapshot(
            revision=1,
            version_ids=()
        )

    def test_invalid_top_k(self):

        with self.assertRaises(ValidationError):

            SearchArguments(
                query="Redis",
                top_k=100
            )

    def test_invalid_document_id(self):

        with self.assertRaises(ValidationError):

            MetadataArguments(
                document_id="invalid"
            )

    def test_unknown_tool_rejected(self):

        with self.assertRaises(ValueError):

            execute_tool(

                "execute_sql",

                {"sql": "SELECT 1"},

                self.snapshot,

                {}
            )

    def test_metadata_tool(self):

        document_id = uuid4()

        evidence = {}

        with patch(

            "app.function_calling.get_document_metadata",

            return_value={
                "document_id": str(document_id),
                "filename": "redis.txt"
            }

        ):

            result = execute_tool(

                "get_document_metadata",

                {
                    "document_id": str(document_id)
                },

                self.snapshot,

                evidence
            )

        self.assertEqual(
            result["status"],
            "ok"
        )

        self.assertIn("M1", evidence)

    def test_tool_calling_loop(self):

        document_id = uuid4()

        first = AIMessage(

            content="",

            tool_calls=[{

                "name": "get_document_metadata",

                "args": {
                    "document_id": str(document_id)
                },

                "id": "call_test"
            }]
        )

        final = AIMessage(
            content="文档名为redis.txt。[M1]"
        )

        with patch(

            "app.function_calling.get_index_snapshot",

            return_value=self.snapshot

        ), patch(

            "app.function_calling.get_document_metadata",

            return_value={
                "filename": "redis.txt"
            }

        ), patch(

            "app.function_calling.get_llm"

        ) as llm:

            model = (
                llm.return_value
                .bind_tools.return_value
            )

            model.invoke.side_effect = [
                first,
                final
            ]

            result = tool_chat(

                ToolChatRequest(
                    query="查询文档元数据"
                )
            )

            self.assertEqual(
                model.invoke.call_count,
                2
            )

        self.assertFalse(result["refused"])

        self.assertEqual(
            len(result["tool_calls"]),
            1
        )

        self.assertEqual(
            result["cited_sources"],
            ["M1"]
        )


if __name__ == "__main__":

    unittest.main()