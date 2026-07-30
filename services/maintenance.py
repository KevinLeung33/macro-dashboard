"""Operational maintenance helpers for server deployments."""
import os
import sqlite3
import shutil
from pathlib import Path

from db.schema import DB_PATH
from services.runtime_controls import read_task_status
from services.time_utils import app_now


def verify_backup(backup_path):
    """Run SQLite integrity_check against a backup file."""
    path = Path(backup_path)
    if not path.exists():
        return {"status": "missing", "path": str(path), "integrity": None}
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return {"status": "ok" if result == "ok" else "error", "path": str(path), "integrity": result}


def restore_database(backup_path, restore_path, overwrite=False):
    """Restore a backup into a separate SQLite file for a safe recovery drill."""
    source_path = Path(backup_path)
    target_path = Path(restore_path)
    if not source_path.exists():
        return {"status": "missing", "restore_path": str(target_path)}
    if target_path.exists() and not overwrite:
        return {"status": "exists", "restore_path": str(target_path)}

    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path), timeout=60)
    target = sqlite3.connect(str(target_path), timeout=60)
    try:
        source.backup(target, pages=256, sleep=0.1)
        target.commit()
    finally:
        target.close()
        source.close()
    result = verify_backup(target_path)
    result["restore_path"] = str(target_path)
    return result


def _cleanup_backups(backup_dir, current_path):
    retention_count = max(1, int(os.getenv("BACKUP_RETENTION_COUNT", "14")))
    retention_days = max(0, float(os.getenv("BACKUP_RETENTION_DAYS", "30")))
    cutoff = app_now().timestamp() - retention_days * 86400
    backups = sorted(
        Path(backup_dir).glob("macro_data_*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for index, path in enumerate(backups):
        if path == Path(current_path):
            continue
        if index >= retention_count or (retention_days and path.stat().st_mtime < cutoff):
            try:
                path.unlink()
                removed.append(str(path))
            except OSError:
                continue
    return removed


def backup_database(backup_dir=None):
    backup_dir = Path(backup_dir or os.getenv("BACKUP_DIR", "backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    if not os.path.exists(DB_PATH):
        return {"status": "missing", "path": str(DB_PATH), "backup_path": None}

    min_free_bytes = int(os.getenv("BACKUP_MIN_FREE_BYTES", "104857600"))
    free_bytes = shutil.disk_usage(backup_dir).free
    if free_bytes < min_free_bytes:
        return {
            "status": "insufficient_disk",
            "path": str(DB_PATH),
            "backup_path": None,
            "free_bytes": free_bytes,
            "required_bytes": min_free_bytes,
        }

    stamp = app_now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"macro_data_{stamp}.db"
    source = sqlite3.connect(str(DB_PATH), timeout=60)
    target = sqlite3.connect(str(backup_path), timeout=60)
    try:
        source.backup(target, pages=256, sleep=0.1)
        target.commit()
    finally:
        target.close()
        source.close()

    integrity = verify_backup(backup_path)
    removed = _cleanup_backups(backup_dir, backup_path)
    return {
        "status": integrity["status"],
        "path": str(DB_PATH),
        "backup_path": str(backup_path),
        "size_bytes": backup_path.stat().st_size,
        "integrity": integrity["integrity"],
        "removed_backups": removed,
    }


def runtime_status():
    return {
        "database": {
            "path": str(DB_PATH),
            "exists": os.path.exists(DB_PATH),
            "size_bytes": os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
        },
        "env": {
            "api_host": os.getenv("API_HOST", "127.0.0.1"),
            "api_port": int(os.getenv("API_PORT", "8080")),
            "streamlit_port": int(os.getenv("STREAMLIT_PORT", "8501")),
            "scheduler_timezone": os.getenv("SCHEDULER_TIMEZONE", "Asia/Shanghai"),
            "log_dir": os.getenv("LOG_DIR", "logs"),
            "backup_dir": os.getenv("BACKUP_DIR", "backups"),
            "backup_retention_count": int(os.getenv("BACKUP_RETENTION_COUNT", "14")),
            "backup_retention_days": int(os.getenv("BACKUP_RETENTION_DAYS", "30")),
            "backup_min_free_bytes": int(os.getenv("BACKUP_MIN_FREE_BYTES", "104857600")),
        },
        "tasks": read_task_status(),
    }
