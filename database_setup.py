from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, create_engine

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is not set")


class UserDB(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("username"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str
    points: float = Field(default=1000)


class MarketDB(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("name"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    b: float = Field(default=20)
    amm_points: float = Field(default=10000)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved: bool = Field(default=False)
    outcome: Optional[bool] = Field(default=None)
    settled_at: Optional[datetime] = Field(default=None)


class AMMDB(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("market_id"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    market_id: int = Field(foreign_key="marketdb.id")
    points: float = Field(default=10000)
    qYes: float = Field(default=0)
    qNo: float = Field(default=0)


class PositionsDB(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("market_id", "user_id"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    market_id: int = Field(foreign_key="marketdb.id")
    user_id: int = Field(foreign_key="userdb.id")
    qYes: float = Field(default=0)
    qNo: float = Field(default=0)


class LedgerDB(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    market_id: int = Field(foreign_key="marketdb.id")
    user_id: int = Field(foreign_key="userdb.id")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    reason: str
    delta: float
    side: str
    amount: Optional[float] = Field(default=None)


class ClearingHouseDB(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("market_id"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    market_id: int = Field(foreign_key="marketdb.id")
    points: float = Field(default=0)


# ------------------------------------------------------------
# IMPORTANT: Connection pooling (biggest Supabase latency win)
# ------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    pool_pre_ping=True,
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
    pool_use_lifo=True,
)


def create_database_and_table():
    SQLModel.metadata.create_all(engine)
