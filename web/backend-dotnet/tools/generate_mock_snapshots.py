import json
import random
from datetime import datetime, timedelta
import uuid

def generate_mock_portfolio():
    symbols = ["110011", "000300", "510300", "005827", "161725"]
    positions = []
    total_market_value = 0.0
    for sym in symbols:
        qty = random.randint(100, 5000) * 10
        cost = random.uniform(1.0, 3.5)
        nav = cost * random.uniform(0.8, 1.5)
        market_value = qty * nav
        total_market_value += market_value
        positions.append({
            "symbol": sym,
            "quantity": qty,
            "average_cost": round(cost, 4),
            "latest_nav": round(nav, 4),
            "market_value": round(market_value, 2)
        })
    
    cash = round(random.uniform(10000, 50000), 2)
    return {
        "positions": positions,
        "cash": cash,
        "total_asset": round(total_market_value + cash, 2),
        "snapshot_time": datetime.now().isoformat()
    }

if __name__ == "__main__":
    snapshots = [generate_mock_portfolio() for _ in range(5)]
    with open("C:/Users/admin/Desktop/量化/量化/data/mock_snapshots.json", "w", encoding="utf-8") as f:
        json.dump(snapshots, f, ensure_ascii=False, indent=2)
    print(f"Successfully generated {len(snapshots)} mock snapshots at data/mock_snapshots.json")
