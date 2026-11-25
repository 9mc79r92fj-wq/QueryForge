import re
import json

from uuid import UUID
from functools import lru_cache

from redis import Redis
from redis.exceptions import RedisError

from pydantic import BaseModel, Field

from app.config import get_settings


settings = get_settings()

MAX_TURNS = 3
SESSION_TTL = 86400

MAX_QUESTION_LENGTH = 400
MAX_ANSWER_LENGTH = 600

KEY_PREFIX = "queryforge:session:v1:"


FOLLOW_UP_PATTERN = re.compile(
    r"^(它|那|这|上述|前面|刚才|还有|那么|"
    r"第二种|第一种|继续|详细说说|"
    r"what about|how about|and\b)"
    r"|上面|刚才|前面|两种机制",
    re.IGNORECASE
)


class ConversationTurn(BaseModel):

    question: str = Field(
        max_length=MAX_QUESTION_LENGTH
    )

    answer: str = Field(
        max_length=MAX_ANSWER_LENGTH
    )


@lru_cache(maxsize=1)
def get_memory_client() -> Redis:

    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1
    )


def session_key(session_id: UUID) -> str:

    return f"{KEY_PREFIX}{session_id}"


def load_history(
    session_id: UUID
) -> tuple[list[ConversationTurn], bool]:

    try:

        records = get_memory_client().lrange(
            session_key(session_id),
            -MAX_TURNS,
            -1
        )

        history = [

            ConversationTurn.model_validate_json(
                record
            )

            for record in records

        ]

        return history, True

    except (RedisError, ValueError):

        return [], False


def save_turn(
    session_id: UUID,
    question: str,
    answer: str
) -> bool:

    answer = re.sub(
        r"\[S\d+\]",
        "",
        answer
    )

    turn = ConversationTurn(

        question=question[:MAX_QUESTION_LENGTH],

        answer=answer[:MAX_ANSWER_LENGTH]
    )

    key = session_key(session_id)

    try:

        client = get_memory_client()

        with client.pipeline(
            transaction=True
        ) as pipe:

            pipe.rpush(
                key,
                turn.model_dump_json()
            )

            pipe.ltrim(
                key,
                -MAX_TURNS,
                -1
            )

            pipe.expire(
                key,
                SESSION_TTL
            )

            pipe.execute()

        return True

    except RedisError:

        return False


def contextual_query(
    query: str,
    history: list[ConversationTurn]
) -> str:

    if not history:

        return query

    if not FOLLOW_UP_PATTERN.search(
        query.strip()
    ):

        return query

    previous_question = (
        history[-1].question
    )

    remaining = max(
        0,
        2000 - len(query) - 1
    )

    if remaining == 0:

        return query

    return (
        query
        + "\n"
        + previous_question[:remaining]
    )


def history_json(
    history: list[ConversationTurn]
) -> str:

    return json.dumps(

        [
            turn.model_dump()
            for turn in history[-MAX_TURNS:]
        ],

        ensure_ascii=False
    )