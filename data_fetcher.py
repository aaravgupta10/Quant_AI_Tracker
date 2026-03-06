import os
import time
import requests
import pandas as pd
import io
import yfinance as yf
from dotenv import load_dotenv
from supabase import create_client, Client

# 1. Load security keys
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def get_live_nifty_500():
    print("Fetching the LIVE Nifty 500 list directly from the NSE server...")
    # The official live CSV URL
    nifty_url = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    
    # We use a User-Agent so the server thinks we are a normal Chrome browser, not a bot
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        response = requests.get(nifty_url, headers=headers)
        response.raise_for_status() # Check if the download was successful
        
        # Read the downloaded text directly into pandas (no file saving needed!)
        df = pd.read_csv(io.StringIO(response.text))
        return df['Symbol'].tolist()
    
    except Exception as e:
        print(f"Critical Error: Could not fetch live Nifty 500 list: {e}")
        exit()

def fetch_and_save_daily_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1d")
        
        if hist.empty:
            print(f"  [!] Skipped: No data for {ticker}")
            return

        latest_date = hist.index[0].strftime('%Y-%m-%d')
        close_price = round(float(hist['Close'].iloc[0]), 2)
        volume = int(hist['Volume'].iloc[0])
        
        # Upsert: Silently update if the row already exists
        supabase.table('market_data').upsert({
            "date": latest_date,
            "ticker": ticker,
            "close_price": close_price,
            "volume": volume
        }, on_conflict="date,ticker").execute()
        
        print(f"  [✓] Saved {ticker}: ₹{close_price}")
        
    except Exception as e:
        print(f"  [X] Error processing {ticker}: {e}")

if __name__ == "__main__":
    
    # 2. Get the live list instead of a static file
    symbols = get_live_nifty_500()
    
    print(f"Successfully loaded {len(symbols)} live stocks. Starting mass data fetch...")
    print("Grab a coffee. This will take about 10-15 minutes to run safely.")
    
    # 3. The Mass Automation Loop
    for symbol in symbols:
        yahoo_ticker = f"{symbol}.NS"
        fetch_and_save_daily_data(yahoo_ticker)
        time.sleep(1.5) 
        
    print("Nifty 500 Daily Fetch Complete! Your vault is fully updated.")