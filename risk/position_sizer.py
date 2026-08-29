"""
Position Sizer - Calculates safe position sizes
"""

def calculate_kelly_criterion(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Kelly Criterion for optimal position sizing.
    
    Args:
        win_rate: Win rate as decimal (0.60 = 60%)
        avg_win: Average win amount
        avg_loss: Average loss amount (positive)
        
    Returns:
        float: Optimal position size as fraction (0.10 = 10%)
    """
    if avg_loss == 0 or win_rate == 0:
        return 0.0
    
    win_loss_ratio = avg_win / avg_loss
    kelly = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
    
    # Conservative: use half-Kelly
    return max(0.0, min(kelly * 0.5, 0.25))  # Cap at 25%


def calculate_position_size_volatility_adjusted(
    balance: float,
    base_risk_pct: float,
    current_volatility: float,
    avg_volatility: float
) -> float:
    """
    Adjust position size based on market volatility.
    
    Args:
        balance: Account balance
        base_risk_pct: Base risk percentage (e.g., 0.02 = 2%)
        current_volatility: Current ATR or volatility measure
        avg_volatility: Average ATR over period
        
    Returns:
        float: Position size in dollars
    """
    if avg_volatility == 0:
        return balance * base_risk_pct
    
    # Reduce size when volatility is high
    volatility_ratio = current_volatility / avg_volatility
    
    if volatility_ratio > 1.5:  # High volatility
        adjusted_risk = base_risk_pct * 0.5  # Cut risk in half
    elif volatility_ratio > 1.2:  # Moderate volatility
        adjusted_risk = base_risk_pct * 0.75
    else:  # Normal/low volatility
        adjusted_risk = base_risk_pct
    
    return balance * adjusted_risk


def get_position_size(
    balance: float,
    signal_quality: float,
    base_size_pct: float = 0.10
) -> float:
    """
    Scale position size based on signal quality.
    
    Args:
        balance: Account balance
        signal_quality: Quality score 0-100
        base_size_pct: Base position size (default 10%)
        
    Returns:
        float: Position size in dollars
    """
    if signal_quality >= 80:
        multiplier = 1.5  # Increase size for excellent signals
    elif signal_quality >= 60:
        multiplier = 1.0  # Normal size
    else:
        multiplier = 0.5  # Reduce size for weak signals
    
    return balance * base_size_pct * multiplier


# CUSTOMIZE HERE:
# - Line 25: kelly * 0.5 - Change to kelly * 0.33 for more conservative
# - Line 48-54: Volatility thresholds (1.5, 1.2) - adjust based on your market
# - Line 70-76: Signal quality scaling - match your strategy's scoring