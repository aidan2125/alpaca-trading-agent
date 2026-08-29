"""
Kill Switch - Emergency stop mechanisms
"""
import json
from pathlib import Path
from datetime import datetime, timedelta

KILL_SWITCH_FILE = Path("data") / "kill_switch_state.json"

def check_kill_switch() -> tuple[bool, str]:
    """
    Check if kill switch is activated.
    
    Returns:
        tuple: (can_trade, reason)
    """
    if not KILL_SWITCH_FILE.exists():
        return True, "Kill switch not active"
    
    try:
        with open(KILL_SWITCH_FILE) as f:
            state = json.load(f)
        
        if state.get("active", False):
            reason = state.get("reason", "Emergency stop")
            activated_at = state.get("activated_at", "Unknown")
            return False, f"KILL SWITCH ACTIVE: {reason} (since {activated_at})"
    
    except:
        pass
    
    return True, "Kill switch check passed"


def activate_kill_switch(reason: str):
    """
    Activate kill switch - stops all trading.
    
    Args:
        reason: Why kill switch was activated
    """
    state = {
        "active": True,
        "reason": reason,
        "activated_at": datetime.now().isoformat()
    }
    
    KILL_SWITCH_FILE.parent.mkdir(exist_ok=True)
    with open(KILL_SWITCH_FILE, 'w') as f:
        json.dump(state, f, indent=4)
    
    print(f"\n{'='*60}")
    print("KILL SWITCH ACTIVATED".center(60))
    print(f"{'='*60}")
    print(f"Reason: {reason}")
    print(f"All trading stopped until manually reset")
    print(f"{'='*60}\n")


def deactivate_kill_switch():
    """Deactivate kill switch - resume trading"""
    if KILL_SWITCH_FILE.exists():
        KILL_SWITCH_FILE.unlink()
    
    print("\nKill switch deactivated - trading resumed\n")


def check_max_consecutive_losses(
    trade_history: list,
    max_consecutive: int = 5
) -> tuple[bool, str]:
    """
    Activate kill switch if too many consecutive losses.
    
    Args:
        trade_history: List of recent trades with 'pnl' field
        max_consecutive: Max consecutive losses before kill switch
        
    Returns:
        tuple: (can_continue, reason)
    """
    if len(trade_history) < max_consecutive:
        return True, "Not enough trades to check"
    
    # Check last N trades
    recent = trade_history[-max_consecutive:]
    consecutive_losses = all(t.get('pnl', 0) < 0 for t in recent)
    
    if consecutive_losses:
        activate_kill_switch(f"{max_consecutive} consecutive losses detected")
        return False, f"Kill switch: {max_consecutive} losses in a row"
    
    return True, "No consecutive loss pattern"


def check_daily_loss_limit(
    trades_today: list,
    daily_limit: float = 100.0
) -> tuple[bool, str]:
    """
    Activate kill switch if daily losses exceed limit.
    
    Args:
        trades_today: Trades executed today
        daily_limit: Max loss allowed per day
        
    Returns:
        tuple: (can_continue, reason)
    """
    total_loss = sum(t.get('pnl', 0) for t in trades_today if t.get('pnl', 0) < 0)
    
    if abs(total_loss) >= daily_limit:
        activate_kill_switch(f"Daily loss limit exceeded: ${abs(total_loss):.2f}")
        return False, f"Daily loss: ${abs(total_loss):.2f} >= ${daily_limit:.2f}"
    
    return True, f"Daily loss OK: ${abs(total_loss):.2f}"


# CUSTOMIZE HERE:
# - Line 79: max_consecutive = 5 - Number of losses before stop (3, 4, 5?)
# - Line 107: daily_limit = 100.0 - Max $ loss per day before stop
# - Add hourly limits, max trades per day, etc.