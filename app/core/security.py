import hashlib
import hmac
from app.core.config import settings


def _hmac_sha256(prefix: str, raw_value: str) -> str:
    key = settings.secret_salt.encode("utf-8")
    msg = f"{prefix}:{raw_value}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def hash_reporter(raw_id: str) -> str:
    """Анонимный хеш пользователя (Telegram ID или web client ID)."""
    return _hmac_sha256("rep", raw_id)


def hash_ip(ip: str) -> str:
    """Анонимный хеш IP для защиты от накрутки через Инкогнито."""
    cleaned = (ip or "").strip()
    if not cleaned:
        return ""
    return _hmac_sha256("ip", cleaned)