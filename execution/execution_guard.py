"""
Execution Guard - Real-time execution safety
"""
from datetime import datetime, timedelta
from typing import Optional

def check_execution_timing(
    signal_generated_at: datetime,
    max_age_seconds: int = 300
) -> tuple[bool, str]:
    """
    Check if signal is too old to execute.
    
    Args:
        signal_generated_at: When signal was generated
        max_age_seconds: Max signal age (default 5 min)
        
    Returns:
        tuple: (is_valid, reason)
    """
    age = (datetime.now() - signal_generated_at).total_seconds()
    
    if age > max_age_seconds:
        return False, f"Signal too old: {age:.0f}s (max {max_age_seconds}s)"
    
    return True, f"Signal age OK: {age:.0f}s"


def check_slippage_acceptable(
    expected_price: float,
    actual_price: float,
    max_slippage: float = 0.005
) -> tuple[bool, str]:
    """
    Check if slippage is within acceptable range.
    
    Args:
        expected_price: Price when signal generated
        actual_price: Current market price
        max_slippage: Max acceptable slippage (default 0.5%)
        
    Returns:
        tuple: (is_acceptable, reason)
    """
    slippage = abs(actual_price - expected_price) / expected_price
    
    if slippage > max_slippage:
        return False, f"Slippage too high: {slippage:.2%} > {max_slippage:.2%}"
    
    return True, f"Slippage OK: {slippage:.2%}"


def check_order_book_depth(
    bid_volume: Optional[float] = None,
    ask_volume: Optional[float] = None,
    min_volume: float = 1000.0
) -> tuple[bool, str]:
    """
    Check if order book has sufficient liquidity.
    
    Args:
        bid_volume: Volume on bid side
        ask_volume: Volume on ask side
        min_volume: Minimum required volume
        
    Returns:
        tuple: (is_sufficient, reason)
    """
    if bid_volume is None or ask_volume is None:
        return True, "No order book data (skipped)"
    
    if bid_volume < min_volume or ask_volume < min_volume:
        return False, f"Low liquidity: bid ${bid_volume:.0f}, ask ${ask_volume:.0f}"
    
    return True, f"Liquidity OK: bid ${bid_volume:.0f}, ask ${ask_volume:.0f}"


def check_execution_rate_limit(
    trades_last_minute: int,
    max_per_minute: int = 5
) -> tuple[bool, str]:
    """
    Check if we're executing too fast.
    
    Args:
        trades_last_minute: Recent trade count
        max_per_minute: Max trades per minute
        
    Returns:
        tuple: (within_limit, reason)
    """
    if trades_last_minute >= max_per_minute:
        return False, f"Rate limit: {trades_last_minute} trades in last minute"
    
    return True, f"Rate OK: {trades_last_minute}/{max_per_minute} per minute"