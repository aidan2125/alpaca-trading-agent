cd alpaca-trading-agent
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
python -m pip install --upgrade pip

pip install -r requirements.txt

verify
pip list | grep -E "alpaca|anthropic|mcp|pandas"