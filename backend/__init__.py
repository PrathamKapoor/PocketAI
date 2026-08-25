"""PocketAI backend package."""

from pathlib import Path

__version__ = "1.0.0"

# PocketAI root (repo top level). All runtime paths are resolved from here so
# the USB drive letter can change freely.
ROOT_DIR = Path(__file__).resolve().parents[1]
