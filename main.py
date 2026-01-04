from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from database_setup import (
    engine,
    UserDB,
    MarketDB,
    AMMDB,
    PositionsDB,
    create_database_and_table,
)

from exchange import (
    lmsr_yes_price,
    lmsr_cost,
    trade_buy,
    trade_sell,
    _max_int_quantity_for_budget,
    Market,  
    User,   
)

from sqlmodel import Session, select
from datetime import datetime
from typing import Dict, Tuple, List, Optional
import asyncio
import random
import math
import os

from bots import BOTS

BOT_TARGET_BALANCE = 10_000
BOT_MIN_BALANCE = 500

ALLOWED_USERNAMES = {}

HIDDEN_PREFIXES_BY_USER: Dict[str, List[str]] = {}


def is_market_visible_to_user(market_name: str, username: str) -> bool:
    prefixes = HIDDEN_PREFIXES_BY_USER.get(username, [])
    return not any(market_name.startswith(p) for p in prefixes)


TRADE_COOLDOWN_SECONDS = 3
last_trade_times: Dict[Tuple[str, int], datetime] = {}


def check_cooldown(username: str, market_id: int) -> None:
    now = datetime.utcnow()
    key = (username, market_id)
    last = last_trade_times.get(key)
    if last is None:
        return

    elapsed = (now - last).total_seconds()
    if elapsed < TRADE_COOLDOWN_SECONDS:
        remaining = int(TRADE_COOLDOWN_SECONDS - elapsed) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Too many trades in this market. Please wait {remaining} seconds.",
        )


create_database_and_table()

app = FastAPI(title="exchange API", version="0.5-ultra-fast")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://prediction-markets-blush.vercel.app",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:3000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def bot_loop():
    await asyncio.sleep(3.0)
    
    print("[BOT LOOP] Starting high-frequency bot trading...")

    while True:
        try:

            with Session(engine) as session:
                markets = session.exec(select(MarketDB).where(MarketDB.resolved == False)).all()  # noqa: E712

            if not markets:
                await asyncio.sleep(30.0)
                continue

            for m in markets:

                with Session(engine) as session:
                    try:
                        mkt = session.get(MarketDB, m.id)
                        amm = session.exec(select(AMMDB).where(AMMDB.market_id == m.id)).first()
                        if not mkt or not amm:
                            continue
                        
                        b = float(mkt.b)
                        q_yes = -float(amm.qYes or 0)
                        q_no = -float(amm.qNo or 0)
                        current_price = lmsr_yes_price(b, q_yes, q_no)
                    except Exception as e:
                        print(f"[BOT] Failed to get price for {m.name}: {e}")
                        continue


                for bot in BOTS:

                    bot_name = bot.__class__.__name__
                    if bot_name == "HyperActiveBot":
                        trade_chance = 0.9  
                    elif bot_name == "RandomBot":
                        trade_chance = 0.4  
                    elif bot_name == "BiasedBot":
                        trade_chance = 0.5  
                    else:  # BeliefBot
                        trade_chance = 0.3  

                    if random.random() > trade_chance:
                        continue

                    # Ensure bot user has enough balance
                    with Session(engine) as session:
                        user = session.exec(
                            select(UserDB).where(UserDB.username == bot.username)
                        ).first()
                        
                        if not user:
                            # Create bot user
                            user = UserDB(username=bot.username, points=BOT_TARGET_BALANCE)
                            session.add(user)
                            session.commit()
                        elif user.points < BOT_MIN_BALANCE:
                            # Top up
                            user = session.exec(
                                select(UserDB)
                                .where(UserDB.username == bot.username)
                                .with_for_update()
                            ).first()
                            if user:
                                top_up = BOT_TARGET_BALANCE - user.points
                                user.points = float(user.points) + top_up
                                session.commit()
                                print(f"[BOT] Topped up {bot.username} with {top_up} points")

                    # Get bot's order
                    try:
                        side, yes_or_no, qty = bot.order(current_price, m.name)
                        if qty <= 0:
                            continue
                    except Exception as e:
                        print(f"[BOT] {bot.username} order generation failed: {e}")
                        continue

                    with Session(engine) as session:
                        try:
                            if side == "buy":
                                trade_buy(
                                    session,
                                    username=bot.username,
                                    market_id=m.id,
                                    quantity=qty,
                                    side=yes_or_no
                                )
                            else:
                                trade_sell(
                                    session,
                                    username=bot.username,
                                    market_id=m.id,
                                    quantity=qty,
                                    side=yes_or_no
                                )
                            session.commit()
  
                        except Exception as e:

                            pass

        except Exception as outer:
            print("[BOT LOOP] outer error:", outer)

        # FAST LOOP: 5 seconds between cycles for visible movement
        await asyncio.sleep(5.0)


@app.post("/users/create")
def create_user(username: str):
    if username not in ALLOWED_USERNAMES:
        raise HTTPException(status_code=403, detail="User not allowed")

    user = User(username, load_positions=False)
    return {"message": f"User {username} created", "points": float(user.points)}


@app.post("/users/login")
def login_user(username: str):
    if username not in ALLOWED_USERNAMES:
        raise HTTPException(status_code=403, detail="User not allowed")

    user = User(username, load_positions=False)
    return {"username": user.username, "points": float(user.points)}


@app.get("/users/{username}")
def get_user(username: str):
    if username not in ALLOWED_USERNAMES:
        raise HTTPException(status_code=403, detail="User not allowed")

    with Session(engine) as session:
        user = session.exec(select(UserDB).where(UserDB.username == username)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        rows = session.exec(
            select(PositionsDB, MarketDB)
            .join(MarketDB, MarketDB.id == PositionsDB.market_id)
            .where(PositionsDB.user_id == user.id)
        ).all()

        return {
            "username": user.username,
            "points": float(user.points),
            "positions": [{"market": m.name, "yes": float(p.qYes), "no": float(p.qNo)} for p, m in rows],
        }


@app.get("/users/{username}/portfolio")
def get_portfolio(username: str):
    if username not in ALLOWED_USERNAMES:
        raise HTTPException(status_code=403, detail="User not allowed")

    with Session(engine) as session:
        user = session.exec(select(UserDB).where(UserDB.username == username)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        rows = session.exec(
            select(PositionsDB, MarketDB, AMMDB)
            .join(MarketDB, MarketDB.id == PositionsDB.market_id)
            .join(AMMDB, AMMDB.market_id == MarketDB.id)
            .where(PositionsDB.user_id == user.id)
        ).all()

        portfolio = []
        for pos, mkt, amm in rows:
            if (pos.qYes or 0) <= 0 and (pos.qNo or 0) <= 0:
                continue

            q_yes_total = -float(amm.qYes or 0)
            q_no_total = -float(amm.qNo or 0)
            yes_price = lmsr_yes_price(float(mkt.b), q_yes_total, q_no_total)
            no_price = 1 - yes_price

            current_value = (float(pos.qYes or 0) * yes_price) + (float(pos.qNo or 0) * no_price)

            portfolio.append({
                "market_id": mkt.id,
                "market_name": mkt.name,
                "yes": float(pos.qYes or 0),
                "no": float(pos.qNo or 0),
                "current_yes_price": round(float(yes_price), 4),
                "current_no_price": round(float(no_price), 4),
                "current_value": round(float(current_value), 2),
            })

        return {"positions": portfolio}



@app.post("/markets/create/")
def create_market(name: str, b: float):
    market = Market(name, b)
    return {"message": f"Market '{name}' created successfully.", "b": float(b)}


@app.get("/markets")
def get_markets():
    with Session(engine) as session:
        markets = session.exec(select(MarketDB).where(MarketDB.resolved == False)).all()  # noqa: E712
        return [
            {
                "id": m.id,
                "name": m.name,
                "b": float(m.b),
                "amm_points": float(m.amm_points or 0),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in markets
        ]


@app.get("/markets/for_user")
def get_markets_for_user(username: str):
    if username not in ALLOWED_USERNAMES:
        raise HTTPException(status_code=403, detail="User not allowed")

    with Session(engine) as session:
        markets = session.exec(select(MarketDB).where(MarketDB.resolved == False)).all()  # noqa: E712

    result = []
    for m in markets:
        if not is_market_visible_to_user(m.name, username):
            continue
        result.append({
            "id": m.id,
            "name": m.name,
            "b": float(m.b),
            "amm_points": float(m.amm_points or 0),
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    return result


@app.get("/markets/preview")
def preview(
    market_id: int,
    username: str,
    quantity: Optional[float] = None,
    yesOrNo: str = "yes",
    points: Optional[float] = None,
):
    if username not in ALLOWED_USERNAMES:
        raise HTTPException(status_code=403, detail="User not allowed")
    if yesOrNo not in ("yes", "no"):
        raise HTTPException(status_code=400, detail="yesOrNo must be 'yes' or 'no'")

    qty: Optional[float] = None
    if quantity is not None:
        try:
            qty = float(quantity)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="quantity must be a number")

    if qty is None or qty <= 0:
        if points is None:
            raise HTTPException(status_code=400, detail="quantity or points is required")
        try:
            points_val = float(points)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="points must be a number")
        if points_val <= 0:
            raise HTTPException(status_code=400, detail="points must be > 0")
        points = points_val

    with Session(engine) as session:
        mkt = session.get(MarketDB, market_id)
        if not mkt:
            raise HTTPException(status_code=404, detail="Market not found")
        if mkt.resolved:
            raise HTTPException(status_code=400, detail="Market is settled")
        if not is_market_visible_to_user(mkt.name, username):
            raise HTTPException(status_code=403, detail="You cannot preview this market")

        amm = session.exec(select(AMMDB).where(AMMDB.market_id == market_id)).first()
        if not amm:
            raise HTTPException(status_code=500, detail="AMM row missing")

        b = float(mkt.b)
        q_yes_total = -float(amm.qYes or 0)
        q_no_total = -float(amm.qNo or 0)

        if qty is None or qty <= 0:
            try:
                qty = _max_int_quantity_for_budget(
                    b=b,
                    q_yes=q_yes_total,
                    q_no=q_no_total,
                    side=yesOrNo,
                    budget=float(points),
                    mode="buy",
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

        current_yes = lmsr_yes_price(b, q_yes_total, q_no_total)
        cost = lmsr_cost(b, q_yes_total, q_no_total, float(qty), yesOrNo)

        if yesOrNo == "yes":
            new_yes = lmsr_yes_price(b, q_yes_total + float(qty), q_no_total)
        else:
            new_yes = lmsr_yes_price(b, q_yes_total, q_no_total + float(qty))

        if not all(math.isfinite(x) for x in (cost, new_yes, current_yes)):
            raise HTTPException(status_code=400, detail="Pricing error: non-finite result")

        return {
            "order_cost": round(float(cost), 2),
            "payout": float(qty),
            "quantity": float(qty),
            "new_price": round(float(new_yes), 4),
            "current_price": round(float(current_yes), 4),
        }


@app.get("/markets/{market_id}")
def get_market(market_id: int):
    with Session(engine) as session:
        market = session.get(MarketDB, market_id)
        if not market:
            raise HTTPException(status_code=404, detail="Market not found")
        return {
            "id": market.id,
            "name": market.name,
            "b": float(market.b),
            "amm_points": float(market.amm_points or 0),
            "resolved": bool(market.resolved),
            "outcome": market.outcome,
            "settled_at": market.settled_at.isoformat() if market.settled_at else None,
            "created_at": market.created_at.isoformat() if market.created_at else None,
        }


@app.post("/markets/buy")
def buy(
    market_id: int,
    username: str,
    quantity: Optional[float] = None,
    yesOrNo: str = "yes",
    points: Optional[float] = None,
):
    if username not in ALLOWED_USERNAMES:
        raise HTTPException(status_code=403, detail="User not allowed")
    if yesOrNo not in ("yes", "no"):
        raise HTTPException(status_code=400, detail="yesOrNo must be 'yes' or 'no'")

    qty: Optional[float] = None
    if quantity is not None:
        try:
            qty = float(quantity)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="quantity must be a number")

    if (qty is None or qty <= 0) and (points is None or points <= 0):
        raise HTTPException(status_code=400, detail="quantity or points must be > 0")

    check_cooldown(username, market_id)

    budget = float(points) if points is not None else None

    with Session(engine) as session:
        try:
            out = trade_buy(
                session,
                username=username,
                market_id=market_id,
                quantity=float(qty or 1),
                side=yesOrNo,
                budget_points=budget,
                is_visible=is_market_visible_to_user,
            )
            session.commit()
        except ValueError as e:
            session.rollback()
            return JSONResponse(content={"message": str(e)}, status_code=400)
        except Exception as e:
            session.rollback()
            return JSONResponse(content={"message": f"Trade failed: {e}"}, status_code=500)

    last_trade_times[(username, market_id)] = datetime.utcnow()

    used_qty = out.get("quantity", qty)
    return {
        "message": f"{username} bought {used_qty} {yesOrNo} contracts in market {market_id}",
        "new_balance": float(out["new_balance"]),
        "new_price": float(out["new_price"]),
        "order_cost": float(out.get("order_cost", 0)),
        "quantity": float(used_qty or 0),
        "status": "success",
    }


@app.post("/markets/sell")
def sell(
    market_id: int,
    username: str,
    quantity: Optional[float] = None,
    yesOrNo: str = "yes",
    points: Optional[float] = None,
):
    if username not in ALLOWED_USERNAMES:
        raise HTTPException(status_code=403, detail="User not allowed")
    if yesOrNo not in ("yes", "no"):
        raise HTTPException(status_code=400, detail="yesOrNo must be 'yes' or 'no'")

    qty: Optional[float] = None
    if quantity is not None:
        try:
            qty = float(quantity)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="quantity must be a number")

    if (qty is None or qty <= 0) and (points is None or points <= 0):
        raise HTTPException(status_code=400, detail="quantity or points must be > 0")

    check_cooldown(username, market_id)

    budget = float(points) if points is not None else None

    with Session(engine) as session:
        try:
            out = trade_sell(
                session,
                username=username,
                market_id=market_id,
                quantity=float(qty or 1),
                side=yesOrNo,
                budget_points=budget,
                is_visible=is_market_visible_to_user,
            )
            session.commit()
        except ValueError as e:
            session.rollback()
            return JSONResponse(content={"message": str(e)}, status_code=400)
        except Exception as e:
            session.rollback()
            return JSONResponse(content={"message": f"Trade failed: {e}"}, status_code=500)

    last_trade_times[(username, market_id)] = datetime.utcnow()

    used_qty = out.get("quantity", qty)
    return {
        "message": f"{username} sold {used_qty} {yesOrNo} contracts in market {market_id}",
        "new_balance": float(out["new_balance"]),
        "new_price": float(out["new_price"]),
        "order_cost": float(out.get("order_cost", 0)),
        "quantity": float(used_qty or 0),
        "status": "success",
    }


@app.post("/markets/settle")
def settle(market_id: int, outcome: str):
    if outcome not in ("yes", "no"):
        raise HTTPException(status_code=400, detail="Outcome must be 'yes' or 'no'")

    with Session(engine) as session:
        db_market = session.get(MarketDB, market_id)
        if not db_market:
            raise HTTPException(status_code=404, detail="Market not found")
        if db_market.resolved:
            raise HTTPException(status_code=400, detail="Market already settled")
        market_name = db_market.name

    market = Market(market_name)
    market.settlement(outcome)

    return {"message": f"{market_name} has settled at {outcome}"}


ENABLE_BOTS = os.getenv("ENABLE_BOTS", "true").lower() == "true"  # Changed default to true

@app.on_event("startup")
async def start_bot_loop():
    if ENABLE_BOTS:
        print("[STARTUP] Launching bot trading loop...")
        asyncio.create_task(bot_loop())
    else:
        print("[STARTUP] Bots disabled (set ENABLE_BOTS=true to enable)")


@app.get("/")
def root():
    return {"ok": True, "bots_enabled": ENABLE_BOTS}


@app.get("/health")
def health():
    return {"status": "healthy", "bots_active": ENABLE_BOTS}
