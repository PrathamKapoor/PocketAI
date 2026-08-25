"""Tests for lightweight log rotation."""

from __future__ import annotations

from pathlib import Path

from backend.tools.log_rotation import LogRotator, get_log_rotator


def test_log_rotator_no_rotation_needed(tmp_path):
    """Test that small log files are not rotated."""
    rotator = LogRotator(max_size_mb=1.0)
    log_file = tmp_path / "test.log"
    log_file.write_text("Small log content")

    result = rotator.rotate(log_file)
    assert result is None
    assert log_file.exists()


def test_log_rotator_rotates_large_file(tmp_path):
    """Test that large log files are rotated."""
    rotator = LogRotator(max_size_mb=0.001)  # 1KB limit
    log_file = tmp_path / "test.log"
    # Create a file larger than 1KB
    log_file.write_text("x" * 2000)

    result = rotator.rotate(log_file)
    assert result is not None
    assert result.exists()
    assert not log_file.exists()


def test_log_rotator_rotation_numbering(tmp_path):
    """Test that rotation numbers are assigned correctly."""
    rotator = LogRotator(max_size_mb=0.001)
    log_file = tmp_path / "test.log"

    # Create first rotation
    log_file.write_text("x" * 2000)
    rotator.rotate(log_file)
    assert (tmp_path / "test.log.1").exists()

    # Create second rotation
    log_file.write_text("x" * 2000)
    rotator.rotate(log_file)
    assert (tmp_path / "test.log.2").exists()


def test_log_rotator_max_history(tmp_path):
    """Test that max history limit is enforced."""
    rotator = LogRotator(max_size_mb=0.001, max_history=3)
    log_file = tmp_path / "test.log"

    # Create multiple rotations
    for i in range(5):
        log_file.write_text("x" * 2000)
        rotator.rotate(log_file)

    # Should only keep the most recent 3
    assert (tmp_path / "test.log.1").exists()
    assert (tmp_path / "test.log.2").exists()
    assert (tmp_path / "test.log.3").exists()
    assert not (tmp_path / "test.log.4").exists()
    assert not (tmp_path / "test.log.5").exists()


def test_log_rotator_cleanup_old_logs(tmp_path):
    """Test cleanup of old rotated logs."""
    rotator = LogRotator(max_history=2)

    # Create some log files
    (tmp_path / "app.log").write_text("current")
    (tmp_path / "app.log.1").write_text("old 1")
    (tmp_path / "app.log.2").write_text("old 2")
    (tmp_path / "app.log.3").write_text("old 3")

    removed = rotator.cleanup_old_logs(tmp_path)
    assert removed == 1
    assert (tmp_path / "app.log.1").exists()
    assert (tmp_path / "app.log.2").exists()
    assert not (tmp_path / "app.log.3").exists()


def test_log_rotator_nonexistent_file(tmp_path):
    """Test rotation of non-existent file."""
    rotator = LogRotator()
    log_file = tmp_path / "nonexistent.log"

    result = rotator.rotate(log_file)
    assert result is None


def test_get_log_rotator():
    """Test that get_log_rotator returns a configured instance."""
    rotator = get_log_rotator()
    assert isinstance(rotator, LogRotator)


def test_log_rotator_preserves_content(tmp_path):
    """Test that rotated file contains the original content."""
    rotator = LogRotator(max_size_mb=0.001)
    log_file = tmp_path / "test.log"
    original_content = "x" * 2000
    log_file.write_text(original_content)

    rotated_path = rotator.rotate(log_file)
    assert rotated_path is not None
    assert rotated_path.read_text() == original_content
