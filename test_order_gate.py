from config import settings
from risk.agent_safety_gate import check_agent_order_safety


def main():
    order = {
        "symbol": "AAPL",
        "side": "buy",
        "qty": "1",
        "type": "market",
        "time_in_force": "day",
    }

    allowed, reason = check_agent_order_safety(
        tool_name="place_stock_order",
        arguments=order,
        current_positions=1,
        settings=settings,
    )

    print("\n" + "=" * 70)
    print("ORDER SAFETY GATE TEST")
    print("=" * 70)

    print(f"Order: {order}")
    print(f"Allowed: {allowed}")
    print(f"Reason: {reason}")

    print("=" * 70)

    if allowed:
        print("❌ TEST FAILED: Order was allowed!")
    else:
        print("✅ TEST PASSED: Order was blocked!")


if __name__ == "__main__":
    main()