from execution.alpaca_trader import _get_client

client = _get_client()
if client:
    account = client.get_account()
    print("Connected! Account status:", account.status)
    print("Cash:", account.cash)
    print("Buying power:", account.buying_power)
else:
    print("Failed to connect — check your .env file")