"""
Dynamic Risk Management
Adjusts stop loss and take profit based on market volatility (ATR)
"""

import os
import json
from datetime import datetime
from pathlib import Path


RISK_CONFIG_FILE = Path("data") / "risk_config.json"

# Enhanced risk configuration
ENHANCED_RISK_CONFIG = {
    # Basic Settings
    "max_positions": 5,
    "position_size_pct": 0.10,
    "min_trade_size": 10,
    
    # Dynamic Stop Loss/Take Profit
    "use_dynamic_sl_tp": True,           # Use ATR-based SL/TP
    "atr_multiplier_sl": 2.0,            # Stop loss = 2x ATR
    "atr_multiplier_tp": 3.0,            # Take profit = 3x ATR
    "min_risk_reward_ratio": 1.5,       # Minimum 1.5:1 reward:risk
    
    # Fixed Fallbacks (if ATR unavailable)
    "stop_loss_pct": 0.02,               # 2% fixed SL
    "take_profit_pct": 0.05,             # 5% fixed TP
    "trailing_stop_pct": 0.03,           # 3% trailing stop
    
    # Fees and Slippage
    "trading_fee_pct": 0.001,
    "slippage_pct": 0.0005,
    
    # Portfolio Risk
    "max_portfolio_risk": 0.20,
    "max_loss_per_trade_pct": 0.02,     # Max 2% loss per trade
  

    # Daily Circuit Breaker
    "max_daily_loss_pct_account": 0.03,  # Halt ALL new entries if today's account-wide realized loss exceeds 3% of equity
    "max_daily_loss_pct_coin": 0.02,     # Halt entries for a SPECIFIC coin if today's realized loss on that coin exceeds 2% of equity
}


def check_max_positions(current_positions: int, config: dict = None) -> tuple[bool, int, int]:
    """
    Check if opening a new position would exceed the maximum allowed.
    
    Args:
        current_positions: Number of currently open positions
        config: Risk configuration dict
    
    Returns:
        tuple: (can_open, current_count, max_allowed)
    """
    if config is None:
        config = load_enhanced_risk_config()
    
    max_allowed = config.get("max_positions", 5)
    can_open = current_positions < max_allowed
    
    return can_open, current_positions, max_allowed

def calculate_dynamic_sl_tp(entry_price, atr, direction="BUY", config=None):
    """
    Calculate dynamic stop loss and take profit based on ATR.
    
    Args:
        entry_price: Entry price of the trade
        atr: Current ATR value
        direction: "BUY" or "SELL"
        config: Risk configuration dict
        
    Returns:
        tuple: (stop_loss, take_profit)
    """
    if config is None:
        config = load_enhanced_risk_config()

    # Normalize ATR to a numeric value if possible (defensive)
    try:
        atr = float(atr) if atr is not None else None
    except (TypeError, ValueError):
        atr = None

    # Use ATR-based if enabled and ATR is available
    if config.get("use_dynamic_sl_tp", False) and atr is not None and atr > 0:
        sl_distance = atr * config["atr_multiplier_sl"]
        tp_distance = atr * config["atr_multiplier_tp"]
        
        # Ensure minimum risk/reward ratio
        if tp_distance / sl_distance < config["min_risk_reward_ratio"]:
            tp_distance = sl_distance * config["min_risk_reward_ratio"]
        
        if direction == "BUY":
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:  # SELL
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance
    
    else:
        # Fallback to fixed percentage
        if direction == "BUY":
            stop_loss = entry_price * (1 - config["stop_loss_pct"])
            take_profit = entry_price * (1 + config["take_profit_pct"])
        else:  # SELL
            stop_loss = entry_price * (1 + config["stop_loss_pct"])
            take_profit = entry_price * (1 - config["take_profit_pct"])
    
    return stop_loss, take_profit

def calculate_position_size_with_risk(account_balance, entry_price, stop_loss, max_risk_pct=0.02):
    """
    Calculate position size based on risk per trade.
    
    Risk-based sizing: Never risk more than X% of account per trade
    
    Args:
        account_balance: Total account balance
        entry_price: Entry price
        stop_loss: Stop loss price
        max_risk_pct: Maximum % of account to risk (default 2%)
        
    Returns:
        float: Position size in currency units
    """
    # Calculate risk per unit
    risk_per_unit = abs(entry_price - stop_loss)
    
    if risk_per_unit == 0:
        return 0
    
    # Maximum dollar amount to risk
    max_risk_dollars = account_balance * max_risk_pct
    
    # Calculate position size
    position_size = max_risk_dollars / (risk_per_unit / entry_price)
    
    return position_size

def get_risk_reward_ratio(entry_price, stop_loss, take_profit):
    """
    Calculate risk/reward ratio.
    
    Args:
        entry_price: Entry price
        stop_loss: Stop loss price
        take_profit: Take profit price
        
    Returns:
        float: Risk/reward ratio
    """
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    
    if risk == 0:
        return 0
    
    return reward / risk

def load_enhanced_risk_config():
    """Load enhanced risk configuration."""
    if not RISK_CONFIG_FILE.exists():
        return ENHANCED_RISK_CONFIG.copy()
    
    try:
        with open(RISK_CONFIG_FILE, 'r') as f:
            config = json.load(f)
            # Ensure all enhanced keys exist
            for key, value in ENHANCED_RISK_CONFIG.items():
                config.setdefault(key, value)
            return config
    except json.JSONDecodeError:
        return ENHANCED_RISK_CONFIG.copy()

def save_enhanced_risk_config(config):
    """Save enhanced risk configuration."""
    RISK_CONFIG_FILE.parent.mkdir(exist_ok=True)
    with open(RISK_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

# Persist config (keep as backup). Writes a timestamped backup and optionally updates main file.
def save_risk_config_backup(config: dict, path: str = RISK_CONFIG_FILE, keep_main: bool = False):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    backup_dir = os.path.join(os.path.dirname(path) or ".", "backups")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = os.path.join(backup_dir, f"{os.path.basename(path)}.{ts}.json")
    with open(backup_path, "w") as f:
        json.dump(config, f, indent=4)
    if keep_main:
        with open(path, "w") as f:
            json.dump(config, f, indent=4)

def analyze_trade_risk(entry_price, stop_loss, take_profit, position_size, account_balance):
    """
    Analyze and display trade risk metrics.
    
    Args:
        entry_price: Entry price
        stop_loss: Stop loss price
        take_profit: Take profit price
        position_size: Position size in currency
        account_balance: Total account balance
        
    Returns:
        dict: Risk analysis
    """
    # Calculate risk and reward in dollars
    risk_dollars = abs(entry_price - stop_loss) * (position_size / entry_price)
    reward_dollars = abs(take_profit - entry_price) * (position_size / entry_price)
    
    # Calculate as percentage of account
    risk_pct = (risk_dollars / account_balance * 100) if account_balance > 0 else 0
    reward_pct = (reward_dollars / account_balance * 100) if account_balance > 0 else 0
    
    # Risk/reward ratio
    rr_ratio = get_risk_reward_ratio(entry_price, stop_loss, take_profit)
    
    return {
        'risk_dollars': risk_dollars,
        'reward_dollars': reward_dollars,
        'risk_pct': risk_pct,
        'reward_pct': reward_pct,
        'risk_reward_ratio': rr_ratio,
        'max_loss': risk_dollars,
        'max_profit': reward_dollars
    }

def validate_trade_risk(risk_analysis, config=None):
    """
    Validate if trade meets risk criteria.
    
    Args:
        risk_analysis: Dict from analyze_trade_risk()
        config: Risk configuration
        
    Returns:
        tuple: (is_valid, reason)
    """
    if config is None:
        config = load_enhanced_risk_config()
    
    # Check risk/reward ratio
    if risk_analysis['risk_reward_ratio'] < config.get('min_risk_reward_ratio', 1.5):
        return False, f"Risk/Reward too low ({risk_analysis['risk_reward_ratio']:.2f})"
    
    # Check max risk per trade
    if risk_analysis['risk_pct'] > config.get('max_loss_per_trade_pct', 0.02) * 100:
        return False, f"Risk too high ({risk_analysis['risk_pct']:.2f}%)"
    
    return True, "Trade meets risk criteria"

def calculate_fees_and_slippage(position_size: float, config: dict = None) -> dict:
    """
    Calculate trading fees and slippage costs.
    """
    if config is None:
        config = load_enhanced_risk_config()
    
    fee_pct = config.get("trading_fee_pct", 0.001)
    slippage_pct = config.get("slippage_pct", 0.0005)
    total_rate = fee_pct + slippage_pct
    
    return {
        "trading_fee": round(position_size * fee_pct, 2),
        "slippage": round(position_size * slippage_pct, 2),
        "total_cost": round(position_size * total_rate, 2)
    }

# Example usage
if __name__ == "__main__":
    print("Dynamic Risk Management Module")
    print("\nFeatures:")
    print("✓ ATR-based stop loss & take profit")
    print("✓ Risk-based position sizing")
    print("✓ Risk/reward ratio validation")
    print("✓ Trade risk analysis")
    
    # Example calculation
    entry = 30000
    atr = 500
    
    sl, tp = calculate_dynamic_sl_tp(entry, atr, "BUY")
    print(f"\nExample Trade:")
    print(f"  Entry: ${entry}")
    print(f"  ATR: ${atr}")
    print(f"  Stop Loss: ${sl:.2f}")
    print(f"  Take Profit: ${tp:.2f}")
    print(f"  Risk/Reward: {get_risk_reward_ratio(entry, sl, tp):.2f}:1")