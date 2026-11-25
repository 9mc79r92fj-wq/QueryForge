import math
import re
import unicodedata

from collections import Counter, defaultdict
from functools import lru_cache

import jieba

from app.config import get_settings

from app.document_processing import (
    ChunkRecord,
    load_chunks
)

from app.index_snapshot import IndexSnapshot
from app.schemas import SearchHit


settings = get_settings()


TOKEN_PATTERN = re.compile(
    r"[a-z0-9]+(?:[_.:-][a-z0-9]+)*"
    r"|[\u3400-\u9fff]+"
)


def tokenize(text: str) -> list[str]:

    text = unicodedata.normalize(
        "NFKC",
        text
    ).lower()

    tokens = []

    for part in TOKEN_PATTERN.findall(text):

        if re.search(r"[\u3400-\u9fff]", part):

            tokens.extend(
                word.strip()
                for word in jieba.lcut(
                    part,
                    HMM=False
                )
                if word.strip()
            )

        else:

            tokens.append(part)

    return tokens


class BM25Index:

    def __init__(
        self,
        chunks: list[ChunkRecord],
        k1: float = 1.5,
        b: float = 0.75
    ):

        if k1 <= 0 or not 0 <= b <= 1:

            raise ValueError(
                "Invalid BM25 parameters"
            )

        self.chunks = tuple(chunks)

        self.k1 = k1
        self.b = b

        self.document_count = len(chunks)

        self.document_lengths = []

        self.postings = defaultdict(dict)

        self.idf = {}

        self._build_index()


    def _build_index(self) -> None:

        for index, chunk in enumerate(
            self.chunks
        ):

            tokens = tokenize(chunk.text)

            frequencies = Counter(tokens)

            self.document_lengths.append(
                len(tokens)
            )

            for term, frequency in frequencies.items():

                self.postings[term][index] = frequency

        self.average_length = (

            sum(self.document_lengths)
            / max(self.document_count, 1)

        )

        for term, documents in self.postings.items():

            df = len(documents)

            self.idf[term] = math.log1p(

                (
                    self.document_count - df + 0.5
                )
                / (df + 0.5)

            )


    def search(
        self,
        query: str,
        top_k: int = 10
    ) -> list[SearchHit]:

        if top_k <= 0:

            raise ValueError(
                "top_k must be positive"
            )

        if not self.document_count:

            return []

        if not self.average_length:

            return []

        query_terms = set(
            tokenize(query)
        )

        scores = defaultdict(float)

        for term in query_terms:

            matching_documents = self.postings.get(
                term,
                {}
            )

            for index, frequency in matching_documents.items():

                document_length = (
                    self.document_lengths[index]
                )

                normalization = self.k1 * (

                    1 - self.b

                    + self.b
                    * document_length
                    / self.average_length

                )

                score = self.idf[term] * (

                    frequency * (self.k1 + 1)

                    / (frequency + normalization)

                )

                scores[index] += score

        ranked = sorted(

            scores.items(),

            key=lambda item: (
                -item[1],
                str(
                    self.chunks[item[0]].chunk_id
                )
            )

        )[:top_k]

        return [

            SearchHit(
                **self.chunks[index].model_dump(),
                score=score
            )

            for index, score in ranked

        ]


@lru_cache(maxsize=2)
def get_bm25_index(
    snapshot: IndexSnapshot
) -> BM25Index:

    chunks = []

    for version_id in snapshot.version_ids:

        chunks.extend(

            load_chunks(
                settings.data_dir,
                version_id
            )

        )

    return BM25Index(chunks)