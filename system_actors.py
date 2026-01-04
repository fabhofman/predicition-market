from __future__ import annotations
from typing import Optional, Tuple
from sqlmodel import Session, select
from database_setup import engine, UserDB

SYSTEM_AMM_USERNAME = "__system_amm__"
SYSTEM_CH_USERNAME  = "__system_clearing_house__"


def get_or_create_system_user_id(username: str, *, points: float = 0) -> int:
    with Session(engine) as session:
        row = session.exec(select(UserDB).where(UserDB.username == username)).first()
        if row:
            return row.id

        row = UserDB(username=username, points=points)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def get_system_actor_ids() -> Tuple[int, int]:
    amm_id = get_or_create_system_user_id(SYSTEM_AMM_USERNAME, points=0)
    ch_id  = get_or_create_system_user_id(SYSTEM_CH_USERNAME, points=0)
    return amm_id, ch_id
