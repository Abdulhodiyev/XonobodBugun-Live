import gzip
import logging
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import BASE_DIR, settings
from app.core.logging import setup_logging

setup_logging()
logger = logging.getLogger("backup")

BACKUP_DIR = BASE_DIR / "backups"
KEEP_DAYS = 14


def resolve_sqlite_path(db_url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not db_url.startswith(prefix):
        raise RuntimeError("Bu skript faqat SQLite bazasi uchun mo‘ljallangan.")
    raw = db_url[len(prefix):]
    return (BASE_DIR / raw).resolve()


def run_backup() -> Path:
    src_path = resolve_sqlite_path(settings.database_url)
    if not src_path.exists():
        raise FileNotFoundError(f"Baza topilmadi: {src_path}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tmp_db = BACKUP_DIR / f"xonobod_{stamp}.db"
    gz_path = BACKUP_DIR / f"xonobod_{stamp}.db.gz"

    # Атомарный онлайн-бэкап (безопасен при активном WAL)
    src_conn = sqlite3.connect(str(src_path), timeout=30)
    dst_conn = sqlite3.connect(str(tmp_db))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    with open(tmp_db, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp_db.unlink(missing_ok=True)

    # Ротация: удаляем архивы старше KEEP_DAYS дней
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    removed = 0
    for old in BACKUP_DIR.glob("xonobod_*.db.gz"):
        mtime = datetime.fromtimestamp(old.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            old.unlink(missing_ok=True)
            removed += 1

    size_kb = gz_path.stat().st_size / 1024
    logger.info("Zaxira nusxa yaratildi: %s (%.1f KB), eskilar o‘chirildi: %d", gz_path.name, size_kb, removed)
    return gz_path


if __name__ == "__main__":
    run_backup()