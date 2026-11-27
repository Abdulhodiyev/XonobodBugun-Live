import time
from datetime import datetime, timezone
from pathlib import Path

from app.core.cache import app_cache
from app.core.config import BASE_DIR, settings

PHOTOS_DIR = BASE_DIR / "uploads" / "station_photos"


def _safe_photo_path(station_id: str) -> Path:
    clean_id = "".join(ch for ch in station_id if ch.isalnum() or ch == "_")
    return PHOTOS_DIR / f"{clean_id}.jpg"


def save_station_photo(station_id: str, data: bytes) -> Path:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    path = _safe_photo_path(station_id)
    path.write_bytes(data)
    app_cache.invalidate("state")
    return path


def get_fresh_photo_path(station_id: str) -> Path | None:
    path = _safe_photo_path(station_id)
    if not path.exists():
        return None
    ttl_sec = settings.station_photo_ttl_min * 60
    age_sec = time.time() - path.stat().st_mtime
    if age_sec > ttl_sec:
        path.unlink(missing_ok=True)
        return None
    return path


def get_fresh_photo_meta(station_id: str) -> dict | None:
    path = get_fresh_photo_path(station_id)
    if path is None:
        return None
    mtime = path.stat().st_mtime
    updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    return {
        "photo_url": f"/api/stations/{station_id}/photo?v={int(mtime)}",
        "photo_updated_at": updated_at,
    }


def cleanup_expired_station_photos() -> int:
    if not PHOTOS_DIR.exists():
        return 0
    ttl_sec = settings.station_photo_ttl_min * 60
    now = time.time()
    removed = 0
    for f in PHOTOS_DIR.glob("*.jpg"):
        if now - f.stat().st_mtime > ttl_sec:
            f.unlink(missing_ok=True)
            removed += 1
    return removed