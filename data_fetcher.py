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
    nifty_url = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(nifty_url, headers=headers)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        return [f"{str(sym).strip()}.NS" for sym in df['Symbol'].tolist()]
    except:
        return []

if __name__ == "__main__":
    nifty_tickers = get_live_nifty_500()
    print(f"Initiating Daily Pulse for {len(nifty_tickers)} stocks...")

    for ticker in nifty_tickers:
        try:
            stock = yf.Ticker(ticker)
            # Only pulling the latest 1 day to append to your massive history
            hist = stock.history(period="1d") 
            
            if hist.empty:
                continue

            latest_date = hist.index[0].strftime('%Y-%m-%d')
            close_price = round(float(hist['Close'].iloc[0]), 2)
            volume = int(hist['Volume'].iloc[0])
            
            supabase.table('market_data').upsert({
                "date": latest_date,
                "ticker": ticker,
                "close_price": close_price,
                "volume": volume
            }, on_conflict="date,ticker").execute()
            
            print(f"  [✓] Updated {ticker}: ₹{close_price}")
            
        except Exception as e:
            pass
            
        time.sleep(0.2) 
        
    print("\n🏁 Daily Market Update Complete.")