import unittest

from uuid import uuid4
from unittest.mock import patch

from redis.exceptions import RedisError

from app.session_memory import (
    ConversationTurn,
    contextual_query,
    load_history,
    save_turn,
    session_key,
    history_json,
    SESSION_TTL,
    MAX_TURNS
)


class TestSessionMemory(unittest.TestCase):

    def setUp(self):

        self.session_id = uuid4()

        self.history = [

            ConversationTurn(

                question="Redis有哪些持久化机制？",

                answer="AOF和RDB。"
            )

        ]

    def test_follow_up_query(self):

        result = contextual_query(
            "这两种机制有什么区别？",
            self.history
        )

        self.assertIn("Redis", result)

    def test_independent_query(self):

        result = contextual_query(
            "Celery是什么？",
            self.history
        )

        self.assertEqual(
            result,
            "Celery是什么？"
        )

    def test_load_history(self):

        with patch(
            "app.session_memory.get_memory_client"
        ) as client:

            client.return_value.lrange.return_value = [

                self.history[0].model_dump_json()

            ]

            history, success = load_history(
                self.session_id
            )

        self.assertTrue(success)

        self.assertEqual(
            history,
            self.history
        )

    def test_save_turn(self):

        with patch(
            "app.session_memory.get_memory_client"
        ) as client:

            pipe = (
                client.return_value
                .pipeline.return_value
                .__enter__.return_value
            )

            success = save_turn(

                self.session_id,

                "Redis是什么？",

                "内存数据存储。[S1]"
            )

            key = session_key(
                self.session_id
            )

            pipe.ltrim.assert_called_once_with(
                key,
                -MAX_TURNS,
                -1
            )

            pipe.expire.assert_called_once_with(
                key,
                SESSION_TTL
            )

            pipe.execute.assert_called_once()

            record = pipe.rpush.call_args.args[1]

            self.assertNotIn(
                "[S1]",
                record
            )

        self.assertTrue(success)

    def test_redis_failure(self):

        with patch(
            "app.session_memory.get_memory_client",
            side_effect=RedisError("Unavailable")
        ):

            history, success = load_history(
                self.session_id
            )

            self.assertEqual(history, [])

            self.assertFalse(success)

            self.assertFalse(

                save_turn(
                    self.session_id,
                    "question",
                    "answer"
                )

            )

    def test_history_serialization(self):

        result = history_json(
            self.history
        )

        self.assertIn("Redis", result)

        self.assertIn("AOF", result)


if __name__ == "__main__":

    unittest.main()