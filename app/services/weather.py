import logging
import httpx
from app.core.cache import app_cache

logger = logging.getLogger(__name__)

WEATHER_CACHE_KEY = "weather:xonobod"
WEATHER_TTL_SECONDS = 1800  # 30 минут

WEATHER_ICONS = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌦️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "❄️",
    80: "🌦️", 81: "🌧️", 82: "🌧️",
    95: "⛈️",
}


async def get_xonobod_weather() -> dict:
    cached = app_cache.get(WEATHER_CACHE_KEY)
    if cached is not None:
        return cached

    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=40.8028&longitude=73.0009"
        "&current=temperature_2m,weather_code"
        "&timezone=Asia%2FTashkent"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            current = resp.json().get("current", {})
            result = {
                "temp": round(current.get("temperature_2m", 0)),
                "icon": WEATHER_ICONS.get(int(current.get("weather_code", 0)), "⛅"),
            }
            app_cache.set(WEATHER_CACHE_KEY, result, WEATHER_TTL_SECONDS)
            return result
    except Exception as exc:
        logger.warning("Ob-havo ma’lumotini olishda xato: %s", exc)
        return {"temp": None, "icon": "⛅"}