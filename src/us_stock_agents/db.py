from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from urllib.parse import unquote, urlparse

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from us_stock_agents.settings import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class StrategyRow(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(64), default="ma_crossover")
    fast_ma: Mapped[int] = mapped_column(Integer, default=10)
    slow_ma: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active|candidate|champion|retired
    backtest_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    paper_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    paper_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    is_champion: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PortfolioRow(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(Integer, index=True)
    cash: Mapped[float] = mapped_column(Float)
    positions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    equity: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TradeRow(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MistakeRow(Base):
    __tablename__ = "mistakes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    pnl_pct: Mapped[float] = mapped_column(Float)
    lesson: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunRow(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentiment_label: Mapped[str] = mapped_column(String(32), default="")
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)
    themes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    backtest_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    paper_equity: Mapped[float] = mapped_column(Float, default=0.0)
    paper_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    lessons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


_engine = None
_SessionLocal = None


def _normalize_database_url(url: str) -> str:
    # Railway often provides postgres:// — SQLAlchemy wants postgresql+psycopg://
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _ensure_sqlite_parent(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    # sqlite:///./state/local.db or sqlite:////absolute/path.db
    raw = url.split("sqlite:///", 1)[-1]
    if raw == ":memory:" or not raw:
        return
    path = Path(unquote(raw))
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        url = _normalize_database_url(settings.database_url)
        if "sqlite" in url and "postgres" not in url:
            log.warning(
                "Using SQLite (%s). On Railway you should attach PostgreSQL and set DATABASE_URL.",
                url,
            )
            _ensure_sqlite_parent(url)
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
        log.info("Database engine ready: %s", urlparse(url).scheme)
    return _engine


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_active_strategy(session: Session, fast: int, slow: int) -> StrategyRow:
    row = session.scalar(
        select(StrategyRow).where(StrategyRow.status == "active").order_by(StrategyRow.id.desc())
    )
    if row:
        return row
    now = utcnow()
    row = StrategyRow(
        version=1,
        name="ma_crossover",
        fast_ma=fast,
        slow_ma=slow,
        status="active",
        params={"fast_ma": fast, "slow_ma": slow},
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row
