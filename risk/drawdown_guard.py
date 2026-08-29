"""
Drawdown Guard - Prevents trading when losses exceed threshold
"""
import datetime
import json
from pathlib import Path

DRAWDOWN_FILE = Path("data") / "drawdown_state.json"

def get_current_drawdown(initial_balance: float, current_balance: float) -> float:
    """
    Calculate current drawdown percentage.
    
    Args:
        initial_balance: Starting balance
        current_balance: Current balance
        
    Returns:
        float: Drawdown as percentage (e.g., 0.15 = 15% drawdown)
    """
    if initial_balance == 0:
        return 0.0
    
    drawdown = (initial_balance - current_balance) / initial_balance
    return max(0.0, drawdown)  # Only positive drawdowns


def check_drawdown_limit(initial_balance: float, current_balance: float, max_drawdown: float = 0.20) -> tuple[bool, str]:
    """
    Check if current drawdown exceeds maximum allowed.
    
    Args:
        initial_balance: Starting balance
        current_balance: Current balance
        max_drawdown: Maximum allowed drawdown (default 20%)
        
    Returns:
        tuple: (can_trade, reason)
    """
    current_dd = get_current_drawdown(initial_balance, current_balance)
    
    if current_dd >= max_drawdown:
        return False, f"Drawdown limit exceeded: {current_dd:.1%} >= {max_drawdown:.1%}"
    
    return True, f"Drawdown OK: {current_dd:.1%}"


def reset_drawdown_tracking(initial_balance: float):
    """Reset drawdown tracking to new starting point"""
    state = {
        "initial_balance": initial_balance,
        "peak_balance": initial_balance,
        "max_drawdown_seen": 0.0,
        "reset_timestamp": str(datetime.now())
    }
    
    DRAWDOWN_FILE.parent.mkdir(exist_ok=True)
    with open(DRAWDOWN_FILE, 'w') as f:
        json.dump(state, f, indent=4)


# CUSTOMIZE HERE:
# - max_drawdown: Change 0.20 to your risk tolerance (0.10 = 10%, 0.30 = 30%)
# - Add daily/weekly drawdown limits if needed