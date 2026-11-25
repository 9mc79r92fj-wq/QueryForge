import argparse
import json
import random

from pathlib import Path
from collections import defaultdict

import pyarrow.dataset as ds

from huggingface_hub import snapshot_download


SEED = 42

CORPUS_SIZE = 500

DEV_SIZE = 10

TEST_SIZE = 30

DATASET = "mteb/T2Retrieval"

HISTORICAL_REVISION = "921dd3af6e78d1ae7ee0368aa8d7eaee02c8f08e"
OUTPUT = Path("data/benchmark/dataset.json")


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", default=HISTORICAL_REVISION)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists. Select a new dataset path.")
    rng = random.Random(SEED)

    root = Path(snapshot_download(

        repo_id=DATASET,

        repo_type="dataset",
        revision=args.revision,

        allow_patterns=[
            "corpus/*.parquet",
            "queries/*.parquet",
            "data/*.parquet"
        ]

    ))

    print("Dataset revision:", root.name)

    queries = {

        row["_id"]: row["text"]

        for row in ds.dataset(
            root / "queries",
            format="parquet"
        ).to_table().to_pylist()

        if row["text"].strip()
    }

    qrels = defaultdict(dict)

    for row in ds.dataset(
        root / "data",
        format="parquet"
    ).to_table().to_pylist():

        if row["score"] > 0:

            qrels[row["query-id"]][
                row["corpus-id"]
            ] = int(row["score"])

    eligible = sorted(
        set(queries) & set(qrels)
    )

    selected = rng.sample(
        eligible,
        DEV_SIZE + TEST_SIZE
    )

    required_ids = {

        pid

        for qid in selected

        for pid in qrels[qid]
    }

    corpus_dataset = ds.dataset(
        root / "corpus",
        format="parquet"
    )

    all_ids = set(

        corpus_dataset.to_table(
            columns=["_id"]
        ).column("_id").to_pylist()

    )

    assert required_ids <= all_ids

    remaining = sorted(
        all_ids - required_ids
    )

    negatives = rng.sample(

        remaining,

        max(0, CORPUS_SIZE - len(required_ids))
    )

    corpus_ids = required_ids | set(negatives)

    rows = corpus_dataset.to_table(

        filter=ds.field("_id").isin(
            sorted(corpus_ids)
        )

    ).to_pylist()

    corpus = {

        row["_id"]: {

            "title": row.get("title") or "",

            "text": row["text"]

        }

        for row in rows
    }

    assert set(corpus) == corpus_ids

    assert all(
        item["text"].strip()
        for item in corpus.values()
    )

    payload = {

        "dataset": DATASET,

        "revision": root.name,

        "seed": SEED,

        "corpus": corpus,

        "dev": [

            {
                "id": qid,
                "query": queries[qid],
                "relevant": qrels[qid]
            }

            for qid in selected[:DEV_SIZE]
        ],

        "test": [

            {
                "id": qid,
                "query": queries[qid],
                "relevant": qrels[qid]
            }

            for qid in selected[DEV_SIZE:]
        ]
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")

    print("Documents:", len(corpus))

    print("Dev queries:", len(payload["dev"]))

    print("Test queries:", len(payload["test"]))

    print("Saved:", args.output)


if __name__ == "__main__":

    main()