"""Tests for lightweight performance monitoring."""

from __future__ import annotations

import time

from backend.tools.metrics import PerformanceTracker, tracker


def test_performance_tracker_record():
    """Test recording timing measurements."""
    t = PerformanceTracker()
    t.record("test_operation", 100.5)
    records = t.get_recent(1)
    assert len(records) == 1
    assert records[0].operation == "test_operation"
    assert records[0].duration_ms == 100.5


def test_performance_tracker_context_manager():
    """Test context manager for timing."""
    t = PerformanceTracker()
    with t.track("test_block"):
        time.sleep(0.01)
    records = t.get_recent(1)
    assert len(records) == 1
    assert records[0].operation == "test_block"
    assert records[0].duration_ms > 0


def test_performance_tracker_max_records():
    """Test that max records limit is enforced."""
    t = PerformanceTracker(max_records=5)
    for i in range(10):
        t.record(f"operation_{i}", float(i))
    records = t.get_recent(10)
    assert len(records) == 5
    # Should keep the most recent records
    assert records[0].operation == "operation_5"
    assert records[-1].operation == "operation_9"


def test_performance_tracker_get_by_operation():
    """Test filtering records by operation."""
    t = PerformanceTracker()
    t.record("op_a", 100.0)
    t.record("op_b", 200.0)
    t.record("op_a", 150.0)

    a_records = t.get_by_operation("op_a")
    assert len(a_records) == 2
    assert all(r.operation == "op_a" for r in a_records)

    b_records = t.get_by_operation("op_b")
    assert len(b_records) == 1


def test_performance_tracker_get_stats():
    """Test statistics calculation."""
    t = PerformanceTracker()
    t.record("test_op", 100.0)
    t.record("test_op", 200.0)
    t.record("test_op", 300.0)

    stats = t.get_stats("test_op")
    assert stats["count"] == 3
    assert stats["min_ms"] == 100.0
    assert stats["max_ms"] == 300.0
    assert stats["avg_ms"] == 200.0
    assert stats["last_ms"] == 300.0


def test_performance_tracker_get_stats_empty():
    """Test statistics for non-existent operation."""
    t = PerformanceTracker()
    stats = t.get_stats("non_existent")
    assert stats == {}


def test_performance_tracker_clear():
    """Test clearing all records."""
    t = PerformanceTracker()
    t.record("test_op", 100.0)
    t.record("test_op", 200.0)
    assert len(t.get_recent(10)) == 2

    t.clear()
    assert len(t.get_recent(10)) == 0


def test_performance_tracker_metadata():
    """Test recording with metadata."""
    t = PerformanceTracker()
    t.record("test_op", 100.0, mode="fast", model="qwen")
    records = t.get_recent(1)
    assert len(records) == 1
    assert records[0].metadata == {"mode": "fast", "model": "qwen"}


def test_global_tracker_exists():
    """Test that the global tracker instance exists."""
    assert tracker is not None
    assert isinstance(tracker, PerformanceTracker)


def test_tracker_records_inference_time(client, mock_llama):
    """Test that inference time is tracked in the supervisor."""
    # Clear any existing records
    tracker.clear()

    # Send a chat message
    resp = client.post(
        "/chat",
        json={"message": "What is 2+2?"},
    )
    assert resp.status_code == 200

    # Check that inference was tracked
    inference_records = tracker.get_by_operation("inference")
    assert len(inference_records) > 0
    assert inference_records[0].duration_ms > 0


def test_tracker_records_rag_retrieval_time(client, pocket_app, mock_llama):
    """Test that RAG retrieval time is tracked."""
    # Clear any existing records
    tracker.clear()

    # Upload a document first
    content = "# Test\n\nTest content."
    files = {"file": ("test.md", content.encode(), "text/markdown")}
    resp = client.post("/documents/upload", files=files)
    assert resp.status_code == 200

    # Send a chat message with docs enabled
    resp = client.post(
        "/chat",
        json={"message": "What is this?", "use_documents": True},
    )
    assert resp.status_code == 200

    # Check that RAG retrieval was tracked
    rag_records = tracker.get_by_operation("rag_retrieval")
    assert len(rag_records) > 0
    assert rag_records[0].duration_ms > 0
