"""Lightweight local performance diagnostics.

Tracks basic timing information for key operations:
- Startup time
- Model initialization time
- OCR processing time
- Document extraction time
- RAG retrieval time
- Inference time
- Total response time

All data stays local; no telemetry is collected or transmitted.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TimingRecord:
    """A single timing measurement."""
    operation: str
    duration_ms: float
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class PerformanceTracker:
    """Simple performance tracker that stores timing records locally."""

    def __init__(self, max_records: int = 1000) -> None:
        self._records: list[TimingRecord] = []
        self._max_records = max_records

    def record(self, operation: str, duration_ms: float, **metadata: Any) -> None:
        """Record a timing measurement."""
        self._records.append(
            TimingRecord(
                operation=operation,
                duration_ms=duration_ms,
                metadata=metadata,
            )
        )
        # Keep only the most recent records
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records:]

    @contextmanager
    def track(self, operation: str, **metadata: Any):
        """Context manager to track timing of a block of code."""
        start = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self.record(operation, duration_ms, **metadata)

    def get_recent(self, n: int = 10) -> list[TimingRecord]:
        """Get the most recent n timing records."""
        return self._records[-n:]

    def get_by_operation(self, operation: str) -> list[TimingRecord]:
        """Get all timing records for a specific operation."""
        return [r for r in self._records if r.operation == operation]

    def get_stats(self, operation: str) -> dict[str, float]:
        """Get statistics for a specific operation."""
        records = self.get_by_operation(operation)
        if not records:
            return {}
        durations = [r.duration_ms for r in records]
        return {
            "count": len(durations),
            "min_ms": min(durations),
            "max_ms": max(durations),
            "avg_ms": sum(durations) / len(durations),
            "last_ms": durations[-1],
        }

    def clear(self) -> None:
        """Clear all timing records."""
        self._records.clear()


# Global performance tracker instance
tracker = PerformanceTracker()


def track_startup() -> None:
    """Track startup time."""
    pass  # Will be called by the launcher


def track_model_load() -> None:
    """Track model initialization time."""
    pass  # Will be called by the model loader


def track_ocr() -> None:
    """Track OCR processing time."""
    pass  # Will be called by the OCR processor


def track_document_extraction() -> None:
    """Track document extraction time."""
    pass  # Will be called by the RAG extractor


def track_rag_retrieval() -> None:
    """Track RAG retrieval time."""
    pass  # Will be called by the RAG retriever


def track_inference() -> None:
    """Track inference time."""
    pass  # Will be called by the llama client


def track_response() -> None:
    """Track total response time."""
    pass  # Will be called by the supervisor
