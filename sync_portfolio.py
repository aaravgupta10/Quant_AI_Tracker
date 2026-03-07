import os
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))

# 1. Read every ticker you actually own from the vault
res = supabase.table('transactions').select('ticker').execute()
owned_tickers = set([row['ticker'] for row in res.data if row['ticker'] != 'CASH'])

print(f"Forcing 5-year deep sync for your actual inventory: {owned_tickers}\n")

# 2. Download and force-inject the true history into the market_data table
for t in owned_tickers:
    print(f"Downloading history for {t}...")
    try:
        hist = yf.Ticker(t).history(period="5y")
        if hist.empty:
            print(f"  [!] Yahoo returned no data for {t}")
            continue
            
        records = []
        for date, row in hist.iterrows():
            records.append({
                "date": date.strftime('%Y-%m-%d'),
                "ticker": t,
                "close_price": round(float(row['Close']), 2),
                "volume": int(row['Volume'])
            })
            
        for i in range(0, len(records), 500):
            supabase.table('market_data').upsert(records[i:i+500], on_conflict="date,ticker").execute()
            
        print(f"  [✓] Database synced for {t}")
    except Exception as e:
        print(f"  [X] Failed on {t}: {e}")

print("\n🏁 SYNC COMPLETE. The Engine now has the correct historical prices.")