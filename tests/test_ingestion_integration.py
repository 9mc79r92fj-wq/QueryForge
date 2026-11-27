import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi import HTTPException, UploadFile
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from app import (
    database, document_service, index_service, index_snapshot, ingestion, knowledge_tools,
    recovery, reranker, storage, task_lock, task_service, tasks, vector_store
)
from app.benchmark import build_chunks, calculate_metrics, evaluate
from app.config import get_settings
from app.document_processing import load_chunks
from app.index_snapshot import IndexSnapshot
from app.schemas import SearchHit, TaskStatus


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    engine = database.build_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.init_db(engine)
    sessions = database.sessionmaker(engine, expire_on_commit=False)
    for module in (document_service, index_service, index_snapshot, ingestion, knowledge_tools, recovery, task_service):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    monkeypatch.setattr(get_settings(), "qdrant_collection", "integration")
    yield sessions
    engine.dispose()


def txt(payload=b"Redis AOF persists write commands.", filename="../redis.txt"):
    return UploadFile(file=BytesIO(payload), filename=filename)


def create_version(payload=b"Redis AOF persists write commands.", document_id=None):
    filename, content_hash, file_path = storage.save_upload(txt(payload))
    return document_service.create_document_version(filename, content_hash, file_path, document_id)


def test_upload_validation_and_content_dedup(isolated, monkeypatch):
    settings = get_settings()
    first = create_version()
    second = create_version()
    assert first.id == second.id and first.task_id == second.task_id
    assert first.filename == "redis.txt"
    assert first.content_hash == hashlib.sha256(b"Redis AOF persists write commands.").hexdigest()
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    for filename, content, status in [
        ("document.exe", b"data", 415), ("document.pdf", b"bad", 400),
        ("document.txt", b"\xff", 400), ("document.txt", b"\x00", 400),
        ("document.txt", b"", 400), ("document.txt", b"a" * (1024 * 1024 + 1), 413)
    ]:
        with pytest.raises(HTTPException) as error:
            storage.save_upload(txt(content, filename))
        assert error.value.status_code == status
    assert list((settings.data_dir / "uploads").iterdir()) == [settings.data_dir / first.file_path]


def test_ingestion_activation_after_confirmed_vectors(isolated, monkeypatch):
    client = QdrantClient(":memory:")
    # The local adapter stores actual vectors, while payload index creation is a server-only operation.
    monkeypatch.setattr(client, "create_payload_index", Mock())
    monkeypatch.setattr(vector_store, "get_vector_client", lambda: client)
    monkeypatch.setattr(ingestion, "embed_documents", lambda texts: [[1.0] + [0.0] * 511 for _ in texts])
    first = create_version()
    task = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)
    result = tasks.execute_ingestion(task, first.id)
    assert result["activated"] and result["revision"] == 1
    assert task_service.get_task_status(first.task_id).status == TaskStatus.COMPLETED
    assert len(load_chunks(get_settings().data_dir, first.id)) == 1
    assert client.count(collection_name="integration").count == 1
    assert tasks.execute_ingestion(task, first.id)["skipped"]


def test_partial_vector_write_retries_before_activation(isolated, monkeypatch):
    client = Mock()
    info = client.get_collection.return_value
    info.config.params.vectors = models.VectorParams(size=512, distance=models.Distance.COSINE)
    info.payload_schema = {"document_id": {}, "version_id": {}}
    client.upsert.return_value.status = models.UpdateStatus.ACKNOWLEDGED
    monkeypatch.setattr(vector_store, "get_vector_client", lambda: client)
    monkeypatch.setattr(ingestion, "embed_documents", lambda texts: [[1.0] + [0.0] * 511 for _ in texts])
    version = create_version()
    task = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3, retry=Mock(return_value=RuntimeError("retry")))
    with pytest.raises(RuntimeError, match="retry"):
        tasks.execute_ingestion(task, version.id)
    assert task_service.get_task_status(version.task_id).status == TaskStatus.RETRYING
    task.retry.assert_called_once()
    assert task.retry.call_args.kwargs["countdown"] == 5

    client.upsert.return_value.status = models.UpdateStatus.COMPLETED
    assert tasks.execute_ingestion(task, version.id)["activated"]


def test_corrupt_or_escaped_stored_upload_fails_without_indexing(isolated):
    first = create_version()
    (get_settings().data_dir / first.file_path).write_text("changed", encoding="utf-8")
    task = SimpleNamespace(request=SimpleNamespace(retries=0), max_retries=3)
    with pytest.raises(ValueError, match="integrity"):
        tasks.execute_ingestion(task, first.id)
    assert task_service.get_task_status(first.task_id).status == TaskStatus.FAILED

    second = document_service.create_document_version("outside.txt", "b" * 64, "../outside.txt")
    with pytest.raises(ValueError, match="stored upload path"):
        tasks.execute_ingestion(task, second.id)


def test_recovery_reserves_one_delivery_and_respects_task_lock(isolated, monkeypatch):
    version = create_version()
    broker = Mock()
    monkeypatch.setattr(recovery.ingest_document, "apply_async", broker)
    with task_lock.get_task_lock(version.id):
        assert recovery.recover_tasks(0, task_id=version.task_id)["skipped"] == 1
        broker.assert_not_called()
    assert recovery.recover_tasks(0, task_id=version.task_id)["requeued"] == 1
    assert recovery.recover_tasks(0, task_id=version.task_id)["requeued"] == 0
    broker.assert_called_once_with(args=[version.id], task_id=version.task_id, retry=False)


def test_recovery_failed_publish_remains_recoverable(isolated, monkeypatch):
    version = create_version()
    broker = Mock(side_effect=OSError("broker unavailable"))
    monkeypatch.setattr(recovery.ingest_document, "apply_async", broker)
    assert recovery.recover_tasks(0, task_id=version.task_id)["failed"] == 1
    assert task_service.get_task_status(version.task_id).status == TaskStatus.QUEUED
    monkeypatch.setattr(recovery, "DISPATCH_COOLDOWN", recovery.timedelta(0))
    broker.side_effect = None
    assert recovery.recover_tasks(0, task_id=version.task_id)["requeued"] == 1


def test_vector_store_maps_retryable_status(isolated, monkeypatch):
    client = Mock()
    monkeypatch.setattr(vector_store, "get_vector_client", lambda: client)
    client.collection_exists.side_effect = UnexpectedResponse(503, "unavailable", b"", {})
    chunks, _ = build_chunks({"doc": {"title": "AOF", "text": "Redis"}})
    with pytest.raises(OSError, match="Temporary Qdrant"):
        vector_store.upsert_chunks(chunks, [[1.0] + [0.0] * 511])


def test_metrics_and_reference_candidate_cutoff(monkeypatch):
    from rank_bm25 import BM25Okapi
    from app import benchmark

    assert calculate_metrics(["x", "relevant", "relevant"], {"relevant": 2, "missing": 1})["mrr"] == 0.5
    assert calculate_metrics(["relevant"], {"relevant": 1, "missing": 1})["recall"] == 0.5
    data = {"first": {"title": "", "text": "AOF"}, "second": {"title": "", "text": "Celery"}}
    chunks, mapping = build_chunks(data)
    monkeypatch.setattr(benchmark, "STRATEGIES", [("bm25_reference", None, False)])
    reference = BM25Okapi([["aof"], ["celery"]])
    cases = [{"id": "measured", "query": "AOF", "relevant": {"first": 1}}]
    warmup = {"id": "warmup", "query": "Celery", "relevant": {"second": 1}}
    result = evaluate(cases, IndexSnapshot(1, ()), chunks, mapping, reference, warmup)
    metrics = result["bm25_reference"]
    assert metrics["recall_at_10"] == 1
    assert metrics["records"][0]["ranked"] == ["first"]
    assert len(metrics["records"]) == 1 and "warmup_ms" in metrics


def test_demo_snapshot_and_metadata_expose_only_approved_active_versions(isolated, monkeypatch):
    visible = create_version(b"Redis backups")
    private = create_version(b"Private deployment information")
    for version in (visible, private):
        task_service.begin_attempt(version.id)
        task_service.update_task(version.id, TaskStatus.CHUNKING)
        task_service.update_task(version.id, TaskStatus.EMBEDDING)
        task_service.update_task(version.id, TaskStatus.INDEXING)
        index_service.activate_version(version.id, 1)

    settings = get_settings()
    monkeypatch.setattr(settings, "run_mode", "demo")
    monkeypatch.setattr(settings, "demo_public_version_ids", (UUID(visible.id),))
    snapshot = index_snapshot.get_index_snapshot()
    assert snapshot.revision == 2 and snapshot.version_ids == (visible.id,)
    assert knowledge_tools.get_document_metadata(UUID(visible.document_id))["version_id"] == visible.id
    assert knowledge_tools.get_document_metadata(UUID(private.document_id)) is None
    versions = document_service.list_document_versions(10, 0, UUID(private.document_id))
    assert len(versions) == 1 and str(versions[0].id) == private.id


def test_reranker_model_scoring_and_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    monkeypatch.setattr(reranker, "_model", None)
    model = Mock()
    model.rerank.return_value = [0.2, 0.8]
    factory = Mock(return_value=model)
    monkeypatch.setattr(reranker, "TextCrossEncoder", factory)
    chunks, _ = build_chunks({
        "a": {"title": "", "text": "Celery"},
        "b": {"title": "", "text": "Redis"}
    })
    hits = [SearchHit(**chunk.model_dump(), score=0.5) for chunk in chunks]
    result = reranker.rerank_hits("Redis", hits, 1)
    assert result[0].text == "Redis" and result[0].score == 0.8
    assert hits[1].score == 0.5
    assert reranker.get_reranker() is model
    factory.assert_called_once()
    model.rerank.assert_called_once_with(query="Redis", documents=["Celery", "Redis"], batch_size=4)

    for scores, message in [([0.2], "score count"), ([0.2, float("nan")], "Invalid")]:
        model.rerank.return_value = scores
        with pytest.raises(RuntimeError, match=message):
            reranker.rerank_hits("Redis", hits, 1)
    assert reranker.rerank_hits("Redis", [], 1) == []
