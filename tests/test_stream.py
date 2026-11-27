import json
import unittest

from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessageChunk

from app.schemas import (
    SearchHit,
    SearchResponse,
    SearchMode,
    ChatRequest
)

from app.stream_service import (
    stream_chat,
    encode_sse
)


class FakeLLM:

    def __init__(
        self,
        parts,
        fail=False
    ):

        self.parts = parts
        self.fail = fail

    async def astream(
        self,
        messages,
        **kwargs
    ):

        for part in self.parts:

            yield AIMessageChunk(
                content=part
            )

        if self.fail:

            raise RuntimeError(
                "Simulated network failure"
            )

        yield AIMessageChunk(

            content="",

            response_metadata={
                "finish_reason": "stop"
            },

            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15
            }
        )


class TestStream(
    unittest.IsolatedAsyncioTestCase
):

    def setUp(self):

        patcher = patch(
            "app.stream_service.save_turn",
            return_value=True
        )

        patcher.start()

        self.addCleanup(
            patcher.stop
        )
        self.hit = SearchHit(

            document_id=uuid4(),
            version_id=uuid4(),
            chunk_id=uuid4(),

            filename="redis.txt",
            chunk_index=0,

            text="Redis支持AOF。",
            score=0.9
        )

        self.result = SearchResponse(

            query="Redis",

            requested_mode=SearchMode.ADAPTIVE,

            effective_mode=SearchMode.ADAPTIVE,

            index_revision=1,

            hits=[self.hit],

            latency_ms=1.0
        )

    async def collect_events(self):

        events = []

        async for raw in stream_chat(

            ChatRequest(
                query="Redis",
                stream=True
            )

        ):

            lines = raw.splitlines()

            event = lines[0][7:]

            data = json.loads(
                lines[1][6:]
            )

            events.append(
                (event, data)
            )

        return events

    def test_sse_format(self):

        result = encode_sse(
            "delta",
            {"text": "Redis"}
        )

        self.assertTrue(
            result.endswith("\n\n")
        )

    async def test_valid_stream(self):

        with patch(
            "app.stream_service.prepare_rag",
            return_value=(
                self.result,
                "Redis支持AOF",
                {"S1": self.hit}
            )
        ), patch(
            "app.stream_service.get_llm",
            return_value=FakeLLM(
                ["Redis支持", "AOF。[S1]"]
            )
        ):

            events = await self.collect_events()

        names = [
            event for event, _ in events
        ]

        self.assertEqual(
            names,
            [
                "meta",
                "retrieval",
                "delta",
                "delta",
                "final"
            ]
        )

        final = events[-1][1]

        self.assertFalse(final["refused"])

        self.assertEqual(
            final["usage"]["total_tokens"],
            15
        )

        self.assertIsNotNone(
            final["ttft_ms"]
        )

    async def test_invalid_citation(self):

        with patch(
            "app.stream_service.prepare_rag",
            return_value=(
                self.result,
                "Redis支持AOF",
                {"S1": self.hit}
            )
        ), patch(
            "app.stream_service.get_llm",
            return_value=FakeLLM(
                ["Redis支持AOF。[S99]"]
            )
        ):

            events = await self.collect_events()

        self.assertTrue(
            events[-1][1]["refused"]
        )

    async def test_empty_retrieval(self):

        result = self.result.model_copy(
            update={"hits": []}
        )

        with patch(
            "app.stream_service.prepare_rag",
            return_value=(result, "", {})
        ), patch(
            "app.stream_service.get_llm"
        ) as model:

            events = await self.collect_events()

        self.assertTrue(
            events[-1][1]["refused"]
        )

        self.assertIsNone(
            events[-1][1]["ttft_ms"]
        )

        model.assert_not_called()

    async def test_stream_failure(self):

        with patch(
            "app.stream_service.prepare_rag",
            return_value=(
                self.result,
                "Redis支持AOF",
                {"S1": self.hit}
            )
        ), patch(
            "app.stream_service.get_llm",
            return_value=FakeLLM(
                ["Partial response"],
                fail=True
            )
        ):

            events = await self.collect_events()

        self.assertEqual(
            events[-1][0],
            "error"
        )

        self.assertTrue(
            events[-1][1]["discard_deltas"]
        )


if __name__ == "__main__":

    unittest.main()