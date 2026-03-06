import os
import time
import requests
import pandas as pd
import io
import yfinance as yf
from dotenv import load_dotenv
from supabase import create_client, Client

# Load security keys
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def get_live_nifty_500():
    print("Fetching the LIVE Nifty 500 list from NSE...")
    nifty_url = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(nifty_url, headers=headers)
        response.raise_for_status() 
        df = pd.read_csv(io.StringIO(response.text))
        return df['Symbol'].tolist()
    except Exception as e:
        print(f"Critical Error: {e}")
        exit()

def backfill_historical_data(ticker):
    print(f"Pulling 3 years of history for {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        # yfinance automatically pulls whatever exists up to 3 years (handling recent IPOs seamlessly)
        hist = stock.history(period="3y")
        
        if hist.empty:
            print(f"  [!] Skipped: No data for {ticker}")
            return
        
        records = []
        for date, row in hist.iterrows():
            records.append({
                "date": date.strftime('%Y-%m-%d'),
                "ticker": ticker,
                "close_price": round(float(row['Close']), 2),
                "volume": int(row['Volume'])
            })
        
        # BULK UPSERT: Send all rows to the vault in one shot
        supabase.table('market_data').upsert(records, on_conflict="date,ticker").execute()
        print(f"  [✓] Successfully vaulted {len(records)} days of data for {ticker}")
        
    except Exception as e:
        print(f"  [X] Error processing {ticker}: {e}")

if __name__ == "__main__":
    print("--- INITIATING FULL NIFTY 500 BACKFILL ---")
    
    symbols = get_live_nifty_500()
    
    # Unleashing the script on all 500 live stocks
    for symbol in symbols:
        yahoo_ticker = f"{symbol}.NS" # Ensuring Yahoo Finance formatting
        backfill_historical_data(yahoo_ticker)
        time.sleep(1.5) # Rate-limit pause to prevent IP ban
        
    print("--- FULL BACKFILL COMPLETE ---")