import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.cache import app_cache
from app.db.models import Base, Mahalla, Settlement, Station


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    app_cache.invalidate()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            Settlement(
                id="xonobod",
                name_uz="Xonobod",
                name_ru="Ханабад",
                center_lat=40.8028,
                center_lon=73.0009,
                default_zoom=13,
            )
        )
        session.add(
            Mahalla(
                id="xon_fozilmon",
                settlement_id="xonobod",
                name_uz="Fozilmon",
                lat=40.815288,
                lon=72.9812,
            )
        )
        session.add(
            Station(
                id="st_metan_1",
                settlement_id="xonobod",
                name_uz="Metan 1",
                fuel_type="metan",
                lat=40.796,
                lon=72.968,
            )
        )
        await session.commit()
        yield session

    await engine.dispose()