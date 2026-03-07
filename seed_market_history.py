import os
import time
import requests
import pandas as pd
import io
import yfinance as yf
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
supabase: Client = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))

def get_live_nifty_500():
    print("🌐 Fetching the LIVE Nifty 500 list from NSE...")
    nifty_url = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(nifty_url, headers=headers)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        return [f"{str(sym).strip()}.NS" for sym in df['Symbol'].tolist()]
    except Exception as e:
        print(f"Error fetching list: {e}")
        return []

if __name__ == "__main__":
    nifty_tickers = get_live_nifty_500()
    print(f"Loaded {len(nifty_tickers)} stocks. Initiating 5-Year Deep Fetch...")
    print("⚠️ WARNING: This will upload ~600,000 rows to Supabase. It will take ~30 minutes.")
    print("Do not close this terminal.")

    for ticker in nifty_tickers:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5y")
            
            if hist.empty:
                print(f"  [!] Skipped: {ticker}")
                continue

            records_to_upsert = []
            for date_index, row in hist.iterrows():
                records_to_upsert.append({
                    "date": date_index.strftime('%Y-%m-%d'),
                    "ticker": ticker,
                    "close_price": round(float(row['Close']), 2),
                    "volume": int(row['Volume'])
                })
            
            # Batch upload in chunks of 500
            for i in range(0, len(records_to_upsert), 500):
                batch = records_to_upsert[i : i + 500]
                supabase.table('market_data').upsert(batch, on_conflict="date,ticker").execute()
            
            print(f"  [✓] Deep Fetched {ticker}")
            
        except Exception as e:
            print(f"  [X] Error on {ticker}: {e}")
            
        time.sleep(1) # Crucial: Prevents Yahoo Finance from IP banning you
        
    print("\n🏁 THE BIG BANG COMPLETE. Your vault now holds the entire Indian market history.")