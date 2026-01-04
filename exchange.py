import os
import math
from datetime import datetime
from typing import Optional, Dict, Tuple, Callable, Any

from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from database_setup import (
    engine,
    UserDB,
    MarketDB,
    AMMDB,
    PositionsDB,
    LedgerDB,
    ClearingHouseDB,
)

from system_actors import get_system_actor_ids

# Ledger mode 

# off   = no ledger writes 
# light = user trade rows only
# full  = user + system AMM + system CH rows 
LEDGER_MODE = os.getenv("LEDGER_MODE", "off").strip().lower()
if LEDGER_MODE not in ("off", "light", "full"):
    LEDGER_MODE = "off"


def _ledger_off() -> bool:
    return LEDGER_MODE == "off"


def _ledger_light() -> bool:
    return LEDGER_MODE == "light"


def _ledger_full() -> bool:
    return LEDGER_MODE == "full"


_SYSTEM_IDS: Optional[Tuple[int, int]] = None


def _get_system_ids() -> Tuple[int, int]:
    global _SYSTEM_IDS
    if _SYSTEM_IDS is None:
        _SYSTEM_IDS = get_system_actor_ids()
    return _SYSTEM_IDS


def _stable_logsumexp(a: float, b_: float) -> float:
    """Numerically stable log(exp(a) + exp(b))."""

    m = max(a, b_)
    if not math.isfinite(m):
        return float("inf")
    return m + math.log(math.exp(a - m) + math.exp(b_ - m))


def lmsr_cost(b: float, q_yes: float, q_no: float, dq: float, side: str) -> float:
    c_old = b * _stable_logsumexp(q_yes / b, q_no / b)
    if side == "yes":
        c_new = b * _stable_logsumexp((q_yes + dq) / b, q_no / b)
    else:
        c_new = b * _stable_logsumexp(q_yes / b, (q_no + dq) / b)
    return c_new - c_old


def lmsr_yes_price(b: float, q_yes: float, q_no: float) -> float:
    """YES price = exp(q_yes/b) / (exp(q_yes/b) + exp(q_no/b))."""

    a = q_yes / b
    b_ = q_no / b
    m = max(a, b_)

    exp_yes = math.exp(a - m)
    exp_no = math.exp(b_ - m)
    return exp_yes / (exp_yes + exp_no)


def _max_int_quantity_for_budget(
    *,
    b: float,
    q_yes: float,
    q_no: float,
    side: str,
    budget: float,
    mode: str = "buy",
) -> int:
    """Compute the maximum whole contracts supported by a point budget."""

    if budget is None or budget <= 0:
        raise ValueError("budget must be > 0")

    def cost_for_qty(qty: float) -> float:
        if mode == "buy":
            return lmsr_cost(b, q_yes, q_no, qty, side)
        # payout for sell
        return -lmsr_cost(b, q_yes, q_no, -qty, side)

    low = 0
    high = 1

    # Expand upper bound until cost/payout exceeds budget or overflows
    while True:
        val = cost_for_qty(high)
        if not math.isfinite(val) or val > budget:
            break
        low = high
        high *= 2
        if high > 1_000_000_000:
            break

    if low == 0:
        raise ValueError("budget insufficient for 1 contract")

    while low < high:
        mid = (low + high + 1) // 2
        val = cost_for_qty(mid)
        if not math.isfinite(val) or val > budget:
            high = mid - 1
        else:
            low = mid

    return int(low)



def get_or_create_user(session: Session, username: str, initial_points: float = 1000) -> UserDB:

    user = session.exec(select(UserDB).where(UserDB.username == username)).first()
    if user:
        return user

    user = UserDB(username=username, points=float(initial_points))
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        user = session.exec(select(UserDB).where(UserDB.username == username)).first()
        if not user:
            raise
    return user


def get_market_bundle_for_update(session: Session, market_id: int) -> Tuple[MarketDB, AMMDB, ClearingHouseDB]:

    stmt = (
        select(MarketDB, AMMDB, ClearingHouseDB)
        .join(AMMDB, AMMDB.market_id == MarketDB.id) 
        .join(ClearingHouseDB, ClearingHouseDB.market_id == MarketDB.id)  
        .where(MarketDB.id == market_id)
        .with_for_update()
    )
    
    row = session.exec(stmt).first()
    if not row:
        raise ValueError("Market not found or missing AMM/CH rows")
    
    return row  


def get_or_create_position_for_update(session: Session, user_id: int, market_id: int) -> PositionsDB:
    stmt = (
        select(PositionsDB)
        .where(PositionsDB.user_id == user_id)
        .where(PositionsDB.market_id == market_id)
        .with_for_update()
    )
    pos = session.exec(stmt).first()
    if pos:
        return pos

    pos = PositionsDB(user_id=user_id, market_id=market_id, qYes=0, qNo=0)
    session.add(pos)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        pos = session.exec(stmt).first()
        if not pos:
            raise
    return pos


def trade_buy(
    session: Session,
    *,
    username: str,
    market_id: int,
    quantity: float,
    side: str,
    budget_points: Optional[float] = None,
    is_visible: Optional[Callable[[str, str], bool]] = None,
) -> Dict[str, Any]:
    if side not in ("yes", "no"):
        raise ValueError("side must be 'yes' or 'no'")
    qty = float(quantity)
    if qty <= 0 and (budget_points is None or budget_points <= 0):
        raise ValueError("quantity must be > 0")

    # Lock user row
    db_user = session.exec(
        select(UserDB).where(UserDB.username == username).with_for_update()
    ).first()
    if not db_user:
        db_user = get_or_create_user(session, username)

  
    db_market, amm, ch = get_market_bundle_for_update(session, market_id)

    if db_market.resolved:
        raise ValueError("Market is settled")

    if is_visible is not None and not is_visible(db_market.name, username):
        raise ValueError("You cannot trade this market")

    # Lock/create position
    pos = get_or_create_position_for_update(session, db_user.id, market_id)

    b = float(db_market.b)
    q_yes_total = -float(amm.qYes or 0)
    q_no_total = -float(amm.qNo or 0)

    if budget_points is not None and budget_points > 0:
        qty = _max_int_quantity_for_budget(
            b=b,
            q_yes=q_yes_total,
            q_no=q_no_total,
            side=side,
            budget=float(budget_points),
            mode="buy",
        )

    cost = lmsr_cost(b, q_yes_total, q_no_total, qty, side)
    if not math.isfinite(cost):
        raise ValueError("pricing overflow")
    if float(db_user.points or 0) < cost:
        raise ValueError("not enough points for order")


    db_user.points = float(db_user.points) - cost
    amm.points = float(amm.points or 0) + cost
    db_market.amm_points = float(amm.points)

    # inventory / positions
    if side == "yes":
        pos.qYes = float(pos.qYes or 0) + qty
        amm.qYes = float(amm.qYes or 0) - qty
    else:
        pos.qNo = float(pos.qNo or 0) + qty
        amm.qNo = float(amm.qNo or 0) - qty

    # collateral
    total_yes = max(0.0, -float(amm.qYes or 0))
    total_no = max(0.0, -float(amm.qNo or 0))
    required = max(total_yes, total_no)

    current = float(ch.points or 0)
    delta = required - current
    if delta > 0:
        if float(amm.points or 0) < delta:
            raise ValueError("AMM lack points for required collateral")
        amm.points = float(amm.points) - delta
        db_market.amm_points = float(amm.points)
        ch.points = float(ch.points or 0) + delta
    elif delta < -1e-9:
        raise ValueError("clearing house has more points than required after minting")

    if not _ledger_off():
        now = datetime.utcnow()

        session.add(
            LedgerDB(
                market_id=market_id,
                user_id=db_user.id,
                timestamp=now,
                reason="trade buy",
                delta=-cost,
                side=side,
                amount=qty,
            )
        )

        if _ledger_full():
            SYSTEM_AMM_USER_ID, SYSTEM_CH_USER_ID = _get_system_ids()

            session.add(
                LedgerDB(
                    market_id=market_id,
                    user_id=SYSTEM_AMM_USER_ID,
                    timestamp=now,
                    reason="trade sell",
                    delta=cost,
                    side=side,
                    amount=qty,
                )
            )

            if delta > 0:
                session.add(
                    LedgerDB(
                        market_id=market_id,
                        user_id=SYSTEM_AMM_USER_ID,
                        timestamp=now,
                        reason="clearing house",
                        delta=-delta,
                        side="N/A",
                        amount=None,
                    )
                )
                session.add(
                    LedgerDB(
                        market_id=market_id,
                        user_id=SYSTEM_CH_USER_ID,
                        timestamp=now,
                        reason="clearing house",
                        delta=delta,
                        side="N/A",
                        amount=None,
                    )
                )


    q_yes_total2 = -float(amm.qYes or 0)
    q_no_total2 = -float(amm.qNo or 0)
    yes_price2 = lmsr_yes_price(b, q_yes_total2, q_no_total2)
    new_price = yes_price2 if side == "yes" else (1 - yes_price2)

    return {
        "new_balance": float(db_user.points),
        "new_price": float(new_price),
        "quantity": float(qty),
        "order_cost": float(cost),
    }


def trade_sell(
    session: Session,
    *,
    username: str,
    market_id: int,
    quantity: float,
    side: str,
    budget_points: Optional[float] = None,
    is_visible: Optional[Callable[[str, str], bool]] = None,
) -> Dict[str, Any]:
    if side not in ("yes", "no"):
        raise ValueError("side must be 'yes' or 'no'")
    qty = float(quantity)
    if qty <= 0 and (budget_points is None or budget_points <= 0):
        raise ValueError("quantity must be > 0")

    db_user = session.exec(
        select(UserDB).where(UserDB.username == username).with_for_update()
    ).first()
    if not db_user:
        raise ValueError("user not found")


    db_market, amm, ch = get_market_bundle_for_update(session, market_id)

    if db_market.resolved:
        raise ValueError("Market is settled")

    if is_visible is not None and not is_visible(db_market.name, username):
        raise ValueError("You cannot trade this market")

    pos = session.exec(
        select(PositionsDB)
        .where(PositionsDB.user_id == db_user.id)
        .where(PositionsDB.market_id == market_id)
        .with_for_update()
    ).first()
    if not pos:
        raise ValueError("no position for this market")

    if budget_points is not None and budget_points > 0:
        b = float(db_market.b)
        q_yes_total = -float(amm.qYes or 0)
        q_no_total = -float(amm.qNo or 0)

        qty = _max_int_quantity_for_budget(
            b=b,
            q_yes=q_yes_total,
            q_no=q_no_total,
            side=side,
            budget=float(budget_points),
            mode="sell",
        )

        available = float(pos.qYes or 0) if side == "yes" else float(pos.qNo or 0)
        qty = min(qty, math.floor(available))

    if side == "yes" and float(pos.qYes or 0) < qty:
        raise ValueError("not enough YES contracts to sell")
    if side == "no" and float(pos.qNo or 0) < qty:
        raise ValueError("not enough NO contracts to sell")

    b = float(db_market.b)
    q_yes_total = -float(amm.qYes or 0)
    q_no_total = -float(amm.qNo or 0)


    if qty <= 0:
        raise ValueError("quantity must be > 0")

    payout = -lmsr_cost(b, q_yes_total, q_no_total, -qty, side)
    if not math.isfinite(payout):
        raise ValueError("pricing overflow")

    if float(amm.points or 0) < payout:
        raise ValueError("AMM does not have enough points to pay this sell")

    db_user.points = float(db_user.points) + payout
    amm.points = float(amm.points) - payout
    db_market.amm_points = float(amm.points)

    if side == "yes":
        pos.qYes = float(pos.qYes or 0) - qty
        amm.qYes = float(amm.qYes or 0) + qty
    else:
        pos.qNo = float(pos.qNo or 0) - qty
        amm.qNo = float(amm.qNo or 0) + qty

    total_yes = max(0.0, -float(amm.qYes or 0))
    total_no = max(0.0, -float(amm.qNo or 0))
    required = max(total_yes, total_no)

    current = float(ch.points or 0)
    delta = current - required
    if delta > 0:
        ch.points = float(ch.points) - delta
        amm.points = float(amm.points) + delta
        db_market.amm_points = float(amm.points)
    elif delta < -1e-9:
        raise ValueError("collateral increased after sell; state inconsistent")

    if not _ledger_off():
        now = datetime.utcnow()

        session.add(
            LedgerDB(
                market_id=market_id,
                user_id=db_user.id,
                timestamp=now,
                reason="trade sell",
                delta=payout,
                side=side,
                amount=qty,
            )
        )

        if _ledger_full():
            SYSTEM_AMM_USER_ID, SYSTEM_CH_USER_ID = _get_system_ids()

            session.add(
                LedgerDB(
                    market_id=market_id,
                    user_id=SYSTEM_AMM_USER_ID,
                    timestamp=now,
                    reason="trade buy",
                    delta=-payout,
                    side=side,
                    amount=qty,
                )
            )

            if delta > 0:
                session.add(
                    LedgerDB(
                        market_id=market_id,
                        user_id=SYSTEM_CH_USER_ID,
                        timestamp=now,
                        reason="clearing house",
                        delta=-delta,
                        side="N/A",
                        amount=None,
                    )
                )
                session.add(
                    LedgerDB(
                        market_id=market_id,
                        user_id=SYSTEM_AMM_USER_ID,
                        timestamp=now,
                        reason="clearing house",
                        delta=delta,
                        side="N/A",
                        amount=None,
                    )
                )

    q_yes_total2 = -float(amm.qYes or 0)
    q_no_total2 = -float(amm.qNo or 0)
    yes_price2 = lmsr_yes_price(b, q_yes_total2, q_no_total2)
    if not math.isfinite(yes_price2):
        raise ValueError("pricing overflow")
    new_price = yes_price2 if side == "yes" else (1 - yes_price2)

    return {
        "new_balance": float(db_user.points),
        "new_price": float(new_price),
        "quantity": float(qty),
        "order_cost": float(payout),
    }



# Backwards-compatible classes 
class User:
    def __init__(self, username: str, points: float = 1000, load_positions: bool = False):
        self.username = username
        self.points = float(points)
        self.userId: Optional[int] = None
        self.positions: Dict[int, Dict[str, float]] = {}

        with Session(engine) as session:
            row = session.exec(select(UserDB).where(UserDB.username == username)).first()
            if row:
                self.userId = row.id
                self.points = float(row.points)
                if load_positions:
                    rows = session.exec(select(PositionsDB).where(PositionsDB.user_id == row.id)).all()
                    self.positions = {
                        p.market_id: {"yes": float(p.qYes or 0), "no": float(p.qNo or 0)}
                        for p in rows
                    }
            else:
                new_user = UserDB(username=username, points=self.points)
                session.add(new_user)
                session.commit()
                session.refresh(new_user)
                self.userId = new_user.id

    def addPoints(self, amount: float):
        with Session(engine) as session:
            u = session.exec(select(UserDB).where(UserDB.username == self.username).with_for_update()).first()
            if not u:
                raise ValueError("User not found")
            u.points = float(u.points) + float(amount)
            session.commit()
            self.points = float(u.points)

    def subtractPoints(self, amount: float):
        with Session(engine) as session:
            u = session.exec(select(UserDB).where(UserDB.username == self.username).with_for_update()).first()
            if not u:
                raise ValueError("User not found")
            amt = float(amount)
            if float(u.points) < amt:
                raise ValueError("not enough points")
            u.points = float(u.points) - amt
            session.commit()
            self.points = float(u.points)

    def __repr__(self):
        return f"User(username={self.username}, id={self.userId}, points={self.points})"


class Market:
    def __init__(self, name: str, b: float = 20, market_id: Optional[int] = None):
        self.name = name
        self.b = float(b)
        self.market_id = market_id

        if self.market_id is None:
            with Session(engine) as session:
                m = session.exec(select(MarketDB).where(MarketDB.name == name)).first()
                if not m:
                    m = MarketDB(name=name, b=self.b)
                    session.add(m)
                    session.flush() 
                    
                    # Create AMM + CH immediately for fast trades
                    session.add(AMMDB(market_id=m.id, points=float(m.amm_points or 10000), qYes=0, qNo=0))
                    session.add(ClearingHouseDB(market_id=m.id, points=0))
                    
                    session.commit()
                    session.refresh(m)
                
                self.market_id = int(m.id)
                self.b = float(m.b)

                # ensure AMM/CH exist for old markets
                amm = session.exec(select(AMMDB).where(AMMDB.market_id == self.market_id)).first()
                if not amm:
                    session.add(AMMDB(market_id=self.market_id, points=float(m.amm_points or 10000), qYes=0, qNo=0))
                ch = session.exec(select(ClearingHouseDB).where(ClearingHouseDB.market_id == self.market_id)).first()
                if not ch:
                    session.add(ClearingHouseDB(market_id=self.market_id, points=0))
                session.commit()

    def priceUpdate(self, side: str) -> float:
        if side not in ("yes", "no"):
            raise ValueError("side must be 'yes' or 'no'")
        with Session(engine) as session:
            m, amm, _ = get_market_bundle_for_update(session, int(self.market_id))

            b = float(m.b)
            q_yes_total = -float(amm.qYes or 0)
            q_no_total = -float(amm.qNo or 0)
            yes_p = lmsr_yes_price(b, q_yes_total, q_no_total)
            session.rollback()
        return yes_p if side == "yes" else (1 - yes_p)

    def buy(self, user: User, quantity: float, side: str) -> float:
        with Session(engine) as session:
            out = trade_buy(session, username=user.username, market_id=int(self.market_id), quantity=quantity, side=side)
            session.commit()
        user.points = float(out["new_balance"])
        return float(out["new_price"])

    def sell(self, user: User, quantity: float, side: str) -> float:
        with Session(engine) as session:
            out = trade_sell(session, username=user.username, market_id=int(self.market_id), quantity=quantity, side=side)
            session.commit()
        user.points = float(out["new_balance"])
        return float(out["new_price"])

    def settlement(self, outcome: str):
        """Settlement logic - kept for compatibility"""
        if outcome not in ("yes", "no"):
            raise ValueError("outcome must be 'yes' or 'no'")
        
        with Session(engine) as session:
            # Mark market as resolved
            m = session.exec(
                select(MarketDB).where(MarketDB.id == self.market_id).with_for_update()
            ).first()
            if not m:
                raise ValueError("Market not found")
            if m.resolved:
                raise ValueError("Market already settled")
            
            m.resolved = True
            m.outcome = (outcome == "yes")
            m.settled_at = datetime.utcnow()
            
            # Get all positions
            positions = session.exec(
                select(PositionsDB).where(PositionsDB.market_id == self.market_id)
            ).all()
            
            # Pay out winners
            for pos in positions:
                user = session.exec(
                    select(UserDB).where(UserDB.id == pos.user_id).with_for_update()
                ).first()
                if not user:
                    continue
                
                if outcome == "yes":
                    payout = float(pos.qYes or 0)
                else:
                    payout = float(pos.qNo or 0)
                
                if payout > 0:
                    user.points = float(user.points) + payout
            
            session.commit()


class clearingHouse:
    """Legacy class - kept for compatibility"""
    def __init__(self):
        pass
