from pathlib import Path
from pydantic import Field

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Xonobod Bugun"
    debug: bool = False

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    webapp_url: str = Field(default="", alias="WEBAPP_URL")
    run_bot_in_app: bool = Field(default=True, alias="RUN_BOT_IN_APP")
    admin_ids_raw: str = Field(default="", alias="ADMIN_IDS")
    secret_salt: str = Field(default="xonobod-default-salt", alias="SECRET_SALT")
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{(BASE_DIR / 'xonobod.db').as_posix()}",
        alias="DATABASE_URL",
    )

    # Бизнес-константы (TTL и антиспам)
    ttl_power_hours: int = 3
    ttl_water_hours: int = 6
    ttl_gas_hours: int = 6
    station_ttl_minutes: int = 60
    rate_limit_minutes: int = 15
    station_rate_limit_minutes: int = 10
    state_cache_seconds: int = 5

    @property
    def admin_ids(self) -> set[int]:
        return {
            int(x.strip())
            for x in self.admin_ids_raw.split(",")
            if x.strip().isdigit()
        }

    @property
    def data_file(self) -> Path:
        return BASE_DIR / "data" / "mahallas.json"

    @property
    def static_dir(self) -> Path:
        return BASE_DIR / "static"

    def ttl_hours_for(self, utility_type: str) -> int:
        return {
            "power": self.ttl_power_hours,
            "water": self.ttl_water_hours,
            "gas": self.ttl_gas_hours,
        }[utility_type]


settings = Settings()

UTILITY_LABELS_UZ = {
    "power": "⚡ Chiroq",
    "water": "💧 Suv",
    "gas": "🔥 Gaz",
}

FUEL_LABELS_UZ = {
    "metan": "🔵 Metan",
    "propan": "🟡 Propan",
    "benzin": "⛽ Benzin",
}

FUEL_SHORT_UZ = {
    "metan": "Metan",
    "propan": "Propan",
    "benzin": "Benzin",
}