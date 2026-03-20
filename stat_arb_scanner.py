import os
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from statsmodels.tsa.stattools import coint
import argparse
from datetime import datetime
import time

def ensure_quiet():
    import warnings
    warnings.filterwarnings('ignore')

def get_top_nifty_symbols(limit: int = 50) -> list[str]:
    print(f"[*] Fetching Nifty 500 components (Taking Top {limit} for Cointegration speed)...")
    try:
        from daily_market_intelligence import get_nifty500_constituents, load_metadata_cache
        constituents = get_nifty500_constituents()
        meta = load_metadata_cache()
        if meta.empty:
            return constituents["ticker"].head(limit).tolist()
        df = constituents.merge(meta, on="ticker", how="inner")
        if "market_cap" in df.columns:
            df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
            df = df.sort_values(by="market_cap", ascending=False)
        return df["ticker"].head(limit).tolist()
    except Exception as e:
        print(f"[!] Warning: Could not pull metadata from market intelligence script: {e}")
        # Fallback to major names if the import fails
        return ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "ITC.NS", "SBI.NS", "LT.NS"]

def find_cointegrated_pairs(prices: pd.DataFrame, p_value_threshold: float = 0.05):
    """
    Search massive combinations for statistically significant mean-reverting pairs 
    using the Augmented Dickey-Fuller (ADF) tests. O(n^2) complexity.
    """
    n = prices.shape[1]
    keys = prices.keys()
    pairs = []
    
    print(f"[*] Crunching Augmented Dickey-Fuller math for {int(n*(n-1)/2)} pairs...")
    
    for i in range(n):
        for j in range(i+1, n):
            S1 = prices[keys[i]]
            S2 = prices[keys[j]]
            
            # Drop anywhere one stock is NaN
            valid = pd.concat([S1, S2], axis=1).dropna()
            if len(valid) < 100:
                continue
                
            S1_valid = valid.iloc[:, 0]
            S2_valid = valid.iloc[:, 1]
            
            # Test S1 vs S2
            try:
                score, pvalue, _ = coint(S1_valid, S2_valid)
                if pvalue < p_value_threshold:
                    pairs.append((keys[i], keys[j], pvalue))
            except Exception:
                pass
                
    return pairs

def analyze_pair(S1: pd.Series, S2: pd.Series) -> dict:
    valid = pd.concat([S1, S2], axis=1).dropna()
    series1 = valid.iloc[:, 0]
    series2 = valid.iloc[:, 1]
    
    # Calculate Spread (Normalized)
    # Typically: Spread = log(S1) - n * log(S2)
    # Using simple returns spread for high-level scanning
    S1_norm = series1 / series1.iloc[0]
    S2_norm = series2 / series2.iloc[0]
    spread = S1_norm - S2_norm
    
    mean_spread = spread.mean()
    std_spread = spread.std()
    
    zscore = (spread - mean_spread) / std_spread
    current_zscore = zscore.iloc[-1]
    
    return {
        "current_zscore": float(current_zscore),
        "mean_spread": float(mean_spread),
        "std_spread": float(std_spread)
    }

def run_scanner(limit: int = 50, z_threshold: float = 2.0):
    ensure_quiet()
    print("="*60)
    print("      STATISTICAL ARBITRAGE (PAIRS TRADING) SCANNER      ")
    print("="*60)
    
    tickers = get_top_nifty_symbols(limit)
    print(f"[*] Downloading 1-year Daily Closing data for {len(tickers)} tickers...")
    
    data = yf.download(tickers, period="1y", interval="1d", progress=False)
    close_prices = data['Close'] if 'Close' in data else data
    if isinstance(close_prices, pd.Series):
        close_prices = close_prices.to_frame()
        
    prices = close_prices.dropna(axis=1, how='all')
    
    start_time = time.time()
    coint_pairs = find_cointegrated_pairs(prices)
    print(f"[*] Found {len(coint_pairs)} broadly cointegrated pairs in {time.time()-start_time:.1f} seconds.\n")
    
    opportunities = []
    
    for (t1, t2, pval) in coint_pairs:
        stats = analyze_pair(prices[t1], prices[t2])
        z = stats["current_zscore"]
        
        if abs(z) >= z_threshold:
            action = f"BUY {t2} / SHORT {t1}" if z > 0 else f"BUY {t1} / SHORT {t2}"
            opportunities.append({
                "Leg 1": t1,
                "Leg 2": t2,
                "ADF p-value": pval,
                "Spread Z-Score": z,
                "Action": action
            })
            
    if not opportunities:
        print("[!] No severe statistical divergences found today.")
    else:
        df_opps = pd.DataFrame(opportunities)
        df_opps = df_opps.sort_values(by="Spread Z-Score", key=abs, ascending=False)
        
        print(f"\n[$$$] FOUND {len(opportunities)} SEVERELY DIVERGED MEAN-REVERTING OPPORTUNITIES:\n")
        
        for idx, row in df_opps.iterrows():
            z = row['Spread Z-Score']
            color = "🟢" if abs(z) > 2.5 else ("🟡" if abs(z) > 2.0 else "⚪")
            print(f"{color} Pair: {row['Leg 1']} vs {row['Leg 2']}")
            print(f"    Z-Score    : {z:+.2f} (Standard Deviations from Mean)")
            print(f"    P-Value    : {row['ADF p-value']:.4f} (Extremely cointegrated)")
            print(f"    Algo Logic : {row['Action']}")
            print("-" * 50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50, help="Number of Top Nifty stocks to scan (n^2 complexity)")
    parser.add_argument("--zscore", type=float, default=2.0, help="Minimum absolute Z-score execution threshold")
    args = parser.parse_args()
    
    run_scanner(limit=args.limit, z_threshold=args.zscore)
