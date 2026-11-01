"""Backup and restore using SQLite's online backup API."""

from __future__ import annotations

import sqlite3


def backup_database(source: str, destination: str) -> str:
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    return destination


def restore_database(backup: str, destination: str) -> str:
    """Restore by copying the backup over the destination."""
    return backup_database(backup, destination)
