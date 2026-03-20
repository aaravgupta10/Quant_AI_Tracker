import os
import time
import requests
import pandas as pd
import io
import yfinance as yf
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    except Exception as e:
        logger.error(f"Failed to fetch Nifty 500 list: {e}")
        return []

if __name__ == "__main__":
    nifty_tickers = get_live_nifty_500()
    if not nifty_tickers:
        logger.error("No tickers found. Exiting.")
        exit(1)
        
    logger.info(f"Initiating bulk Daily Pulse for {len(nifty_tickers)} stocks...")

    try:
        # Bulk download using yfinance for 1 day
        data = yf.download(
            tickers=nifty_tickers,
            period="1d",
            interval="1d",
            auto_adjust=True,
            threads=True,
            progress=False
        )
        
        records = []
        if 'Close' not in data:
            logger.error("No valid 'Close' data returned from yfinance.")
            exit(1)
            
        close_df = data['Close']
        vol_df = data['Volume'] if 'Volume' in data else pd.DataFrame()
        
        # Determine the latest date across all fetched data
        latest_date_stamp = data.index.max()
        if pd.isna(latest_date_stamp):
            logger.error("No valid datetimes in yfinance download.")
            exit(1)
        latest_date = latest_date_stamp.strftime('%Y-%m-%d')
        
        for ticker in nifty_tickers:
            if ticker in close_df.columns:
                series_close = close_df[ticker].dropna()
                if series_close.empty:
                    continue
                    
                close_price = round(float(series_close.iloc[-1]), 2)
                volume = 0
                if not vol_df.empty and ticker in vol_df.columns:
                    series_vol = vol_df[ticker].dropna()
                    if not series_vol.empty:
                        volume = int(series_vol.iloc[-1])
                        
                records.append({
                    "date": latest_date,
                    "ticker": ticker,
                    "close_price": close_price,
                    "volume": volume
                })
        
        logger.info(f"Successfully processed {len(records)} valid tickers. Upserting into Supabase...")
        
        # Bulk upsert in chunks of 500
        for i in range(0, len(records), 500):
            batch = records[i:i+500]
            try:
                supabase.table('market_data').upsert(batch, on_conflict="date,ticker").execute()
                logger.info(f"Upserted batch {i//500 + 1} ({len(batch)} records)")
            except Exception as e:
                logger.error(f"Failed to upsert batch {i//500 + 1}: {e}")
                
        logger.info("🏁 Daily Market Update Complete.")
        
    except Exception as e:
        logger.error(f"Fatal error during data fetch: {e}")