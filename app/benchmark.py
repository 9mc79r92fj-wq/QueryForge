import argparse
import json
import math
import numpy as np

from pathlib import Path

from time import perf_counter

from uuid import uuid4, uuid5, NAMESPACE_URL

from unittest.mock import patch

from rank_bm25 import BM25Okapi

from app.config import get_settings

from app.document_processing import ChunkRecord

from app.embedding import embed_documents

from app.vector_store import (
    upsert_chunks,
    get_vector_client
)

from app.index_snapshot import IndexSnapshot

from app.bm25_engine import BM25Index, tokenize

from app.retrieval_service import retrieve
from app.reranker import RERANKER_MODEL

from app.schemas import SearchRequest, SearchMode


settings = get_settings()

K = 10

STRATEGIES = [

    ("bm25_reference", None, False),

    ("bm25", SearchMode.BM25, False),

    ("dense", SearchMode.DENSE, False),

    ("hybrid", SearchMode.HYBRID, False),

    ("adaptive", SearchMode.ADAPTIVE, False),

    ("adaptive_rerank", SearchMode.ADAPTIVE, True)
]


def calculate_metrics(ranked, relevant):

    ranked = list(dict.fromkeys(ranked))[:K]

    relevant_ids = {

        pid

        for pid, score in relevant.items()

        if score > 0
    }

    recall = (

        len(set(ranked) & relevant_ids)

        / max(len(relevant_ids), 1)
    )

    reciprocal_rank = 0.0

    for rank, pid in enumerate(ranked, 1):

        if pid in relevant_ids:

            reciprocal_rank = 1 / rank

            break

    dcg = sum(

        (2 ** relevant.get(pid, 0) - 1)

        / math.log2(rank + 1)

        for rank, pid in enumerate(ranked, 1)
    )

    ideal_scores = sorted(

        relevant.values(),

        reverse=True

    )[:K]

    idcg = sum(

        (2 ** score - 1)

        / math.log2(rank + 1)

        for rank, score in enumerate(
            ideal_scores,
            1
        )
    )

    return {

        "recall": recall,

        "mrr": reciprocal_rank,

        "ndcg": dcg / idcg if idcg else 0.0
    }


def build_chunks(corpus):

    chunks = []

    id_mapping = {}

    for pid, record in sorted(corpus.items()):

        document_id = uuid5(
            NAMESPACE_URL,
            f"benchmark:document:{pid}"
        )

        version_id = uuid5(
            NAMESPACE_URL,
            f"benchmark:version:{pid}"
        )

        chunk_id = uuid5(
            NAMESPACE_URL,
            f"benchmark:chunk:{pid}"
        )

        content = (

            record["title"]

            + "\n"

            + record["text"]

        ).strip()

        chunks.append(

            ChunkRecord(

                document_id=document_id,

                version_id=version_id,

                chunk_id=chunk_id,

                filename=f"{pid}.txt",

                page=None,

                chunk_index=0,

                text=content
            )
        )

        id_mapping[str(chunk_id)] = pid

    return chunks, id_mapping


def evaluate(
    cases,
    snapshot,
    chunks,
    id_mapping,
    reference,
    warmup_case=None
):

    results = {}

    for name, mode, rerank in STRATEGIES:

        records = []

        warmup_ms = None
        for case in ([warmup_case] if warmup_case is not None else []) + list(cases):

            started = perf_counter()

            if mode is None:

                terms = tokenize(case["query"])
                scores = reference.get_scores(terms)

                indexes = sorted(

                    (index for index, frequencies in enumerate(reference.doc_freqs)
                     if any(term in frequencies for term in terms)),

                    key=lambda i: (
                        -float(scores[i]),
                        str(chunks[i].chunk_id)
                    )

                )[:K]

                ranked = [

                    id_mapping[str(chunks[i].chunk_id)]

                    for i in indexes
                ]

            else:

                response = retrieve(

                    SearchRequest(

                        query=case["query"],

                        mode=mode,

                        top_k=K,

                        rerank=rerank
                    ),

                    snapshot
                )

                if response.degraded:

                    raise RuntimeError(
                        f"{name}: degraded retrieval"
                    )

                if rerank and not response.rerank_applied:

                    raise RuntimeError(
                        "Reranker was not applied"
                    )

                ranked = [

                    id_mapping[str(hit.chunk_id)]

                    for hit in response.hits
                ]

            latency = (

                perf_counter() - started

            ) * 1000

            if case is warmup_case:
                warmup_ms = round(latency, 3)
                continue

            metrics = calculate_metrics(

                ranked,

                case["relevant"]
            )

            records.append({

                "query_id": case["id"],

                "ranked": ranked,

                **metrics,

                "latency_ms": latency
            })

        results[name] = {

            "recall_at_10": round(
                float(np.mean([
                    r["recall"] for r in records
                ])),
                4
            ),

            "mrr_at_10": round(
                float(np.mean([
                    r["mrr"] for r in records
                ])),
                4
            ),

            "ndcg_at_10": round(
                float(np.mean([
                    r["ndcg"] for r in records
                ])),
                4
            ),

            "p95_ms": round(
                float(np.percentile([
                    r["latency_ms"] for r in records
                ], 95)),
                3
            ),

            "records": records
        }

        if warmup_ms is not None:
            results[name]["warmup_ms"] = warmup_ms

        print(

            name,

            {

                key: value

                for key, value in results[name].items()

                if key != "records"
            },

            flush=True
        )

    return results


def main():

    directory = settings.data_dir / "benchmark"
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=directory / "dataset.json")
    parser.add_argument("--output", type=Path, required=True, help="Path to a new report")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists. Select a new report path.")

    dataset = json.loads(

        args.dataset.read_text(
            encoding="utf-8"
        )
    )

    dev_ids = {record["id"] for record in dataset["dev"]}
    test_ids = {record["id"] for record in dataset["test"]}
    if len(dev_ids) < 2 or not test_ids or dev_ids & test_ids:
        raise ValueError("Expected disjoint nonempty splits and at least two development queries")
    warmup_case, *development = dataset["dev"]

    chunks, id_mapping = build_chunks(
        dataset["corpus"]
    )

    snapshot = IndexSnapshot(

        revision=1,

        version_ids=tuple(
            str(chunk.version_id)
            for chunk in chunks
        )
    )

    index = BM25Index(chunks)

    reference = BM25Okapi([

        tokenize(chunk.text)

        for chunk in chunks
    ])

    vectors = []

    for start in range(0, len(chunks), 32):

        batch = chunks[start:start + 32]

        vectors.extend(

            embed_documents([
                chunk.text for chunk in batch
            ])
        )

        print(
            "Embedded:",
            min(start + 32, len(chunks)),
            "/",
            len(chunks),
            flush=True
        )

    collection = (
        f"queryforge_benchmark_{uuid4().hex}"
    )

    client = get_vector_client()

    try:

        with patch.object(
            settings,
            "qdrant_collection",
            collection
        ), patch(

            "app.retrieval_service.get_bm25_index",

            return_value=index
        ):

            upsert_chunks(
                chunks,
                vectors
            )

            print("\nDevelopment set:")

            dev_results = evaluate(

                development,

                snapshot,

                chunks,

                id_mapping,

                reference,
                warmup_case=warmup_case
            )

            print("\nHeld-out test set:")

            test_results = evaluate(

                dataset["test"],

                snapshot,

                chunks,

                id_mapping,

                reference,
                warmup_case=warmup_case
            )

            report = {

                "dataset": dataset["dataset"],

                "dataset_revision": dataset["revision"],

                "seed": dataset["seed"],

                "corpus_size": len(chunks),

                "dev_queries": len(development),

                "test_queries": len(dataset["test"]),

                "embedding_model": settings.embedding_model,

                "reranker_model": RERANKER_MODEL,
                "warmup_query_id": warmup_case["id"],
                "reference_candidate_policy": "lexical_matches_only",

                "candidate_k": settings.retrieval_top_k,

                "top_k": K,

                "cache_enabled": False,

                "dev": dev_results,

                "test": test_results
            }

            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as file:
                json.dump(report, file, ensure_ascii=False, indent=2)
                file.write("\n")
            print("\nResults saved:", args.output)

    finally:

        if client.collection_exists(collection):

            client.delete_collection(collection)


if __name__ == "__main__":

    main()