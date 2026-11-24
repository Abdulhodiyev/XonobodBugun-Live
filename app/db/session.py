from collections.abc import AsyncGenerator
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import settings


def create_engine_for_url(database_url: str) -> AsyncEngine:
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"timeout": 30} if is_sqlite else {}

    eng = create_async_engine(
        database_url,
        echo=settings.debug,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if is_sqlite:
        @event.listens_for(eng.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA busy_timeout=5000;")
            cursor.close()

    return eng


engine = create_engine_for_url(settings.database_url)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session