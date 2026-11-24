from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Settlement(Base):
    """Населённый пункт: Xonobod, позже Sultonobod и др."""
    __tablename__ = "settlements"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 'xonobod', 'sultonobod'
    name_uz: Mapped[str] = mapped_column(String(100), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)
    default_zoom: Mapped[int] = mapped_column(Integer, default=13, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    mahallas: Mapped[list["Mahalla"]] = relationship(back_populates="settlement")
    stations: Mapped[list["Station"]] = relationship(back_populates="settlement")


class Mahalla(Base):
    """Махалля внутри населённого пункта."""
    __tablename__ = "mahallas"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # 'xon_fozilmon'
    settlement_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("settlements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name_uz: Mapped[str] = mapped_column(String(120), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    polygon_geojson: Mapped[str | None] = mapped_column(Text, nullable=True)  # задел под границы
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    settlement: Mapped["Settlement"] = relationship(back_populates="mahallas")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="mahalla")


class Station(Base):
    """Заправки: metan, propan, позже benzin."""
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # 'st_metan_1'
    settlement_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("settlements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name_uz: Mapped[str] = mapped_column(String(150), nullable=False)
    fuel_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'metan' | 'propan' | 'benzin'
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    settlement: Mapped["Settlement"] = relationship(back_populates="stations")
    votes: Mapped[list["StationVote"]] = relationship(back_populates="station")


class Incident(Base):
    """Отключение света, воды или газа (народное или плановое)."""
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_lookup", "mahalla_id", "utility_type", "status", "expires_at"),
        Index("ix_incidents_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mahalla_id: Mapped[str] = mapped_column(
        String(60), ForeignKey("mahallas.id", ondelete="CASCADE"), nullable=False
    )
    utility_type: Mapped[str] = mapped_column(String(20), nullable=False)  # power | water | gas
    issue_subtype: Mapped[str] = mapped_column(String(30), default="outage", nullable=False)  # outage | low_pressure | planned
    source: Mapped[str] = mapped_column(String(20), default="web", nullable=False)  # bot | web | admin
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # unconfirmed | confirmed | planned | closed
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)  # комментарий админа для planned
    street_hint: Mapped[str] = mapped_column(String(160), default="", nullable=False)  # улица / ориентир от жителей

    reports_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    resolve_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_report_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)  # timeout | user_restored | admin

    mahalla: Mapped["Mahalla"] = relationship(back_populates="incidents")
    reports: Mapped[list["Report"]] = relationship(back_populates="incident", cascade="all, delete-orphan")


class Report(Base):
    """Сырые сигналы пользователей (жалоба / подтверждение / отбой)."""
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_antifraud", "incident_id", "reporter_hash", "action"),
        Index("ix_reports_rate", "reporter_hash", "created_at"),
        Index("ix_reports_ip", "ip_hash", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    reporter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)  # защита от инкогнито на сайте
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # 'report' | 'resolve'
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # 'bot' | 'web' | 'admin'
    street_hint: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    incident: Mapped["Incident"] = relationship(back_populates="reports")


class StationVote(Base):
    """Голоса по статусу заправки (для защиты от одного фейкового клика)."""
    __tablename__ = "station_votes"
    __table_args__ = (
        Index("ix_station_votes_active", "station_id", "expires_at"),
        Index("ix_station_votes_rate", "station_id", "reporter_hash", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(
        String(60), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # 'open' | 'queue' | 'closed'
    reporter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    station: Mapped["Station"] = relationship(back_populates="votes")


class Subscriber(Base):
    """Подписки жителей в Telegram-боте на уведомления по своей махалле."""
    __tablename__ = "subscribers"
    __table_args__ = (
        UniqueConstraint("telegram_id", "mahalla_id", name="uq_subscriber_mahalla"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    mahalla_id: Mapped[str] = mapped_column(
        String(60), ForeignKey("mahallas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notify_power: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_water: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_gas: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EventLog(Base):
    """Лента событий (Таймлайн)."""
    __tablename__ = "event_logs"
    __table_args__ = (
        Index("ix_event_logs_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # power | water | gas | station
    target_id: Mapped[str] = mapped_column(String(60), nullable=False)
    target_name: Mapped[str] = mapped_column(String(150), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)  # reported | confirmed | restored | planned | station_update
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    details: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)