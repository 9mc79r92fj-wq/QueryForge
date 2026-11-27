import argparse
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


ROOT = Path(__file__).resolve().parent.parent


def pdf_fixture() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=700)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica")
            })
        })
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 25 650 Td (Celery Redis durable messages and task recovery) Tj ET")
    page[NameObject("/Contents")] = stream
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def upload(client, name, content, document_id=None):
    form = {"document_id": document_id} if document_id else {}
    response = client.post("/documents/upload", files={"file": (name, content)}, data=form)
    assert response.status_code == 202, (response.status_code, response.text)
    return response.json()


def completed(client, task_id):
    deadline = monotonic() + 120
    while monotonic() < deadline:
        status = client.get(f"/documents/tasks/{task_id}").raise_for_status().json()
        if status["status"] == "completed":
            return status
        if status["status"] == "failed":
            raise AssertionError(status)
        sleep(1)
    raise TimeoutError(f"Task did not complete: {task_id}")


def search(client, query, mode="bm25", rerank=False):
    result = client.post("/search", json={
        "query": query, "mode": mode, "rerank": rerank, "top_k": 5
    }).raise_for_status().json()
    assert not result["degraded"], result["warnings"]
    assert result["effective_mode"] == mode
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="API prefix in the isolated local test stack")
    args = parser.parse_args()
    run_id = uuid4().hex
    with httpx.Client(base_url=args.base_url, timeout=65, trust_env=False) as client:
        system = client.get("/ready").raise_for_status().json()
        assert system["run_mode"] == "local" and system["ready"]
        seed = (ROOT / "examples/demo/redis-aof.txt").read_bytes()
        first_bytes = seed + f"\nAudit marker {run_id}.\n".encode()
        first = upload(client, "redis-aof.txt", first_bytes)
        completed(client, first["task_id"])
        second = upload(client, "task-recovery.pdf", pdf_fixture())
        completed(client, second["task_id"])
        duplicate = upload(client, "duplicate.txt", first_bytes)
        assert duplicate["version_id"] == first["version_id"]
        print("TXT, PDF, async ingestion and SHA-256 content deduplication passed", flush=True)

        for mode in ("bm25", "dense", "hybrid", "adaptive"):
            result = search(client, "Redis AOF " + run_id, mode)
            assert any(hit["version_id"] == first["version_id"] for hit in result["hits"])
            print(f"{mode}: {len(result['hits'])} hits, {result['latency_ms']} ms", flush=True)
        result = search(client, "Redis AOF " + run_id, "adaptive", rerank=True)
        assert result["rerank_applied"] and result["score_type"] == "cross_encoder"
        print(f"Cross-encoder reranker passed: {result['latency_ms']} ms", flush=True)

        query = "Redis AOF " + run_id
        cached = search(client, query)
        assert cached["cache_hit"]
        changed = upload(client, "redis-aof.txt", first_bytes + b"Updated backup guidance.", first["document_id"])
        completed(client, changed["task_id"])
        result = search(client, query)
        assert result["index_revision"] > cached["index_revision"] and not result["cache_hit"]
        assert changed["version_id"] in {hit["version_id"] for hit in result["hits"]}
        assert first["version_id"] not in {hit["version_id"] for hit in result["hits"]}
        print(f"Cache hit, update, historical exclusion and revision change {cached['index_revision']} → {result['index_revision']} passed", flush=True)

        query = "unknownterm" + run_id
        nonstream = client.post("/chat/completions", json={
            "query": query, "mode": "bm25", "stream": False
        }).raise_for_status().json()
        assert nonstream["refused"] and not nonstream["citations"]
        stream = client.post("/chat/completions", json={
            "query": query, "mode": "bm25", "stream": True, "session_id": nonstream["session_id"]
        }).raise_for_status()
        assert "text/event-stream" in stream.headers["content-type"]
        assert "event: final" in stream.text and '"refused": true' in stream.text
        assert "event: delta" not in stream.text
        print("Real HTTP RAG refusal and SSE final validated without calling DeepSeek", flush=True)


if __name__ == "__main__":
    main()
