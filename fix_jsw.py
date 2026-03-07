import os
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from supabase import create_client, Client

# 1. Unlock Vault
load_dotenv()
supabase: Client = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))

# 2. Your Exact Trade Data
ticker = "JSWCEMENT.NS"
start_date = "2025-11-12"
end_date = "2026-02-18"

start_price = 122.19
# Calculate weighted average sell price: ((2 * 124.11) + (3 * 124.13)) / 5
end_price = 124.12 

print(f"Generating synthetic historical prices for {ticker}...")

# 3. Generate Trading Days (Skips Weekends automatically using 'B' frequency)
dates = pd.date_range(start=start_date, end=end_date, freq='B')

# 4. Mathematically interpolate a smooth price line between your Buy and Sell
prices = np.linspace(start_price, end_price, len(dates))

records_to_upsert = []
for d, p in zip(dates, prices):
    records_to_upsert.append({
        "date": d.strftime('%Y-%m-%d'),
        "ticker": ticker,
        "close_price": round(float(p), 2),
        "volume": 1000  # Dummy volume, engine doesn't use it for NAV
    })

# 5. Fire into the Vault
print(f"Injecting {len(records_to_upsert)} days of market data directly into Supabase...")
try:
    # Upload in batches of 500
    for i in range(0, len(records_to_upsert), 500):
        batch = records_to_upsert[i : i + 500]
        supabase.table('market_data').upsert(batch, on_conflict="date,ticker").execute()
    print("✅ Surgical Injection Complete. The missing timeline has been restored.")
except Exception as e:
    print(f"❌ Failed to inject data: {e}")