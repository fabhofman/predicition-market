import random

class RandomBot:
    def __init__(self, username):
        self.username = username

    def order(self, current_price: float, market_name: str | None = None):
        """
        current_price is the YES price
        """
        side = random.choice(["buy", "sell"])
        yes_or_no = random.choice(["yes", "no"])
        qty = random.randint(1, 3)  # Smaller trades for more frequent activity
        return side, yes_or_no, qty

class BeliefBot:
    def __init__(
        self,
        username: str,
        default_belief: float = 0.5,
        aggressiveness: float = 12.0,
        dead_zone: float = 0.02,
        max_qty: int = 10,
    ):
        """
        default_belief: fallback belief if market not in MARKET_BELIEFS.
        aggressiveness: larger means bigger trades for same mispricing.
        dead_zone: if |belief - price| < dead_zone, don't trade.
        max_qty: hard cap on trade size.
        """
        self.username = username
        self.default_belief = default_belief
        self.aggressiveness = aggressiveness
        self.dead_zone = dead_zone
        self.max_qty = max_qty

    def order(self, current_price: float, market_name: str):
        belief = MARKET_BELIEFS.get(market_name, self.default_belief)

        diff = belief - current_price  
      
        if abs(diff) < self.dead_zone:
            return "buy", "yes", 0   

        if diff > 0:
            side = "buy"
            yes_or_no = "yes"
        else:
            side = "buy"
            yes_or_no = "no"

        base_qty = abs(diff) * self.aggressiveness
        qty = max(1, min(self.max_qty, int(round(base_qty))))

        return side, yes_or_no, qty


class BiasedBot:
    def __init__(self, username, default_bias="yes", default_intensity: float = 0.5):
        """
        default_bias/intensity are used if a market has no explicit config.
        """
        self.username = username
        self.default_bias = default_bias
        self.default_intensity = default_intensity

    def order(self, current_price: float, market_name: str):
        cfg = MARKET_BIAS_CONFIG.get(market_name, {})
        bias = cfg.get("bias", self.default_bias) 
        intensity = cfg.get("intensity", self.default_intensity)  

        yes_or_no = bias

        if random.random() < 0.2 * (1.0 - intensity):
            side = "sell"
        else:
            side = "buy"

        max_qty = max(1, int(5 * (0.5 + 0.5 * intensity)))
        qty = random.randint(1, max_qty)

        return side, yes_or_no, qty


class HyperActiveBot:
    """Trades VERY frequently with small amounts to create visible market movement"""
    def __init__(self, username, volatility: float = 0.3):
        self.username = username
        self.volatility = volatility 

    def order(self, current_price: float, market_name: str):
        if current_price > 0.7:
            if random.random() < 0.6:
                side = "buy"
                yes_or_no = "no"
            else:
                side = "sell"
                yes_or_no = "yes"
        elif current_price < 0.3:
            if random.random() < 0.6:
                side = "buy"
                yes_or_no = "yes"
            else:
                side = "sell"
                yes_or_no = "no"
        else:
            side = random.choice(["buy", "sell"])
            yes_or_no = random.choice(["yes", "no"])
        
        qty = random.randint(1, 2)
        return side, yes_or_no, qty


# Market beliefs for BeliefBot
MARKET_BELIEFS = {
    "TestMarketOne": 0.7,
    "TestMarketTwo": 0.3,
    "TestMarketThree": 0.5,
}

# Market bias config for BiasedBot
MARKET_BIAS_CONFIG = {
    "TestMarketOne": {"bias": "yes", "intensity": 0.9},
    "TestMarketTwo": {"bias": "no", "intensity": 0.7},
    "TestMarketThree": {"bias": "yes", "intensity": 0.6},
}

BOTS = [
    HyperActiveBot("botHyper1"), 
    HyperActiveBot("botHyper2"),  
    RandomBot("botR"),
    BiasedBot("botB", default_bias="yes", default_intensity=0.7),
    BiasedBot("botN", default_bias="no", default_intensity=0.7),
    BeliefBot("botBull", default_belief=0.6, aggressiveness=15.0),
    BeliefBot("botBear", default_belief=0.4, aggressiveness=15.0),
]
