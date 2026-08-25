"""Lightweight local log rotation.

Prevents logs from growing indefinitely by rotating log files when they
exceed a size limit. Keeps a configurable number of historical log files.

All rotation is local; no external services are required.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional


class LogRotator:
    """Simple size-based log rotation."""

    def __init__(
        self,
        max_size_mb: float = 10.0,
        max_history: int = 5,
        suffix_pattern: str = ".{n}",
    ) -> None:
        """Initialize log rotator.

        Args:
            max_size_mb: Maximum log file size in MB before rotation.
            max_history: Maximum number of historical log files to keep.
            suffix_pattern: Pattern for rotation suffixes. {n} is replaced
                           with the rotation number.
        """
        self._max_size_bytes = int(max_size_mb * 1024 * 1024)
        self._max_history = max_history
        self._suffix_pattern = suffix_pattern

    def rotate(self, log_path: Path) -> Optional[Path]:
        """Rotate a log file if it exceeds the size limit.

        Args:
            log_path: Path to the log file to check.

        Returns:
            Path to the rotated file if rotation occurred, None otherwise.
        """
        if not log_path.exists():
            return None

        # Check file size
        try:
            size = log_path.stat().st_size
        except OSError:
            return None

        if size <= self._max_size_bytes:
            return None

        # Find the next available rotation number
        rotation_num = 1
        while True:
            suffix = self._suffix_pattern.replace("{n}", str(rotation_num))
            rotated_path = log_path.with_suffix(log_path.suffix + suffix)
            if not rotated_path.exists():
                break
            rotation_num += 1
            if rotation_num > self._max_history:
                # Delete the oldest rotation
                oldest = log_path.with_suffix(
                    log_path.suffix + self._suffix_pattern.replace("{n}", "1")
                )
                if oldest.exists():
                    oldest.unlink()
                # Shift all rotations down by one
                for i in range(1, self._max_history):
                    src = log_path.with_suffix(
                        log_path.suffix + self._suffix_pattern.replace("{n}", str(i + 1))
                    )
                    dst = log_path.with_suffix(
                        log_path.suffix + self._suffix_pattern.replace("{n}", str(i))
                    )
                    if src.exists():
                        src.rename(dst)
                # The new rotation gets the highest number
                rotated_path = log_path.with_suffix(
                    log_path.suffix + self._suffix_pattern.replace("{n}", str(self._max_history))
                )
                break

        # Rotate the current log file
        try:
            log_path.rename(rotated_path)
            return rotated_path
        except OSError:
            return None

    def cleanup_old_logs(self, log_dir: Path, pattern: str = "*.log.*") -> int:
        """Remove old rotated log files that exceed the history limit.

        Args:
            log_dir: Directory containing log files.
            pattern: Glob pattern to match rotated log files.

        Returns:
            Number of files removed.
        """
        if not log_dir.exists():
            return 0

        removed = 0
        for log_file in sorted(log_dir.glob(pattern)):
            if log_file.is_file():
                try:
                    # Extract rotation number from filename
                    name = log_file.name
                    parts = name.split(".")
                    if len(parts) >= 3:
                        # Try to parse the rotation number
                        try:
                            num = int(parts[-1])
                            if num > self._max_history:
                                log_file.unlink()
                                removed += 1
                        except ValueError:
                            pass
                except OSError:
                    pass

        return removed


def get_log_rotator() -> LogRotator:
    """Get a configured log rotator instance."""
    return LogRotator(
        max_size_mb=10.0,
        max_history=5,
    )
