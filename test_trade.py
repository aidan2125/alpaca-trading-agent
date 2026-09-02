from execution.alpaca_trader import _get_client
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce


client = _get_client()

if not client:
    print("Failed to connect — check your .env file")
    raise SystemExit


print("Connected to Alpaca!")

order_data = MarketOrderRequest(
    symbol="AAPL",
    qty=1,
    side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY
)

try:
    order = client.submit_order(order_data=order_data)

    print("Order submitted successfully!")
    print("Order ID:", order.id)
    print("Symbol:", order.symbol)
    print("Quantity:", order.qty)
    print("Side:", order.side)
    print("Status:", order.status)

except Exception as e:
    print("Order failed:")
    print(e)