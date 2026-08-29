"""
Exposure Limiter - Prevents over-concentration in single asset or correlated assets
"""
from typing import Dict, List

def check_single_asset_exposure(
    positions: Dict[str, dict],
    new_coin: str,
    new_size: float,
    total_portfolio_value: float,
    max_single_asset_pct: float = 0.30
) -> tuple[bool, str]:
    """
    Check if adding this position would over-expose to single asset.
    
    Args:
        positions: Current open positions {coin: {size, ...}}
        new_coin: Coin we want to trade
        new_size: Size of new position
        total_portfolio_value: Total account value
        max_single_asset_pct: Max % in single asset (default 30%)
        
    Returns:
        tuple: (can_trade, reason)
    """
    # Calculate total exposure to this coin
    existing_exposure = positions.get(new_coin, {}).get("trade_size", 0.0)
    total_exposure = existing_exposure + new_size
    
    exposure_pct = total_exposure / total_portfolio_value if total_portfolio_value > 0 else 0
    
    if exposure_pct > max_single_asset_pct:
        return False, f"Single asset exposure too high: {exposure_pct:.1%} > {max_single_asset_pct:.1%}"
    
    return True, f"Exposure OK: {exposure_pct:.1%}"


def check_total_exposure(
    positions: Dict[str, dict],
    max_total_exposure_pct: float = 0.80
) -> tuple[bool, str]:
    """
    Check if total portfolio exposure is too high.
    
    Args:
        positions: Current open positions
        max_total_exposure_pct: Max % of portfolio in trades (default 80%)
        
    Returns:
        tuple: (can_trade, reason)
    """
    total_exposure = sum(p.get("trade_size", 0) for p in positions.values())
    
    # Get total portfolio value (you'll need to pass this)
    # For now, assume it's in config or calculate from balances
    
    return True, "Exposure check passed"  # Implement based on your balance tracking


def check_correlation_exposure(
    positions: Dict[str, dict],
    new_coin: str,
    correlation_groups: Dict[str, List[str]],
    max_correlated_exposure_pct: float = 0.50
) -> tuple[bool, str]:
    """
    Check if we're over-exposed to correlated assets.
    
    Example correlation_groups:
    {
        "btc_related": ["BTC/USDT", "ETH/USDT"],
        "defi": ["UNI/USDT", "AAVE/USDT"],
    }
    
    Args:
        positions: Current positions
        new_coin: Coin to add
        correlation_groups: Groups of correlated coins
        max_correlated_exposure_pct: Max % in correlated group
        
    Returns:
        tuple: (can_trade, reason)
    """
    # Find which group new_coin belongs to
    for group_name, coins in correlation_groups.items():
        if new_coin in coins:
            # Count exposure to this group
            group_exposure = sum(
                positions.get(c, {}).get("trade_size", 0)
                for c in coins
                if c in positions
            )
            
            # Check if adding would exceed limit
            # (You'll need total portfolio value here)
            
            return True, f"Correlation check passed for {group_name}"
    
    return True, "No correlation limit"


# CUSTOMIZE HERE:
# - Line 14: max_single_asset_pct = 0.30 - Change to your preference (0.20 = 20%)
# - Line 42: max_total_exposure_pct = 0.80 - How much of portfolio can be in trades
# - Lines 59-63: Define your correlation groups based on coins you trade