import os
import pandas as pd
import yfinance as yf
import warnings
from dotenv import load_dotenv
from supabase import create_client, Client

# Suppress yfinance warnings for a clean terminal output
warnings.filterwarnings("ignore")

# Unlock the vault
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def get_current_holdings():
    # Fetch exactly what you own right now
    response = supabase.table('transactions').select("*").execute()
    if not response.data: return {}
    df = pd.DataFrame(response.data)
    
    positions = {}
    for _, row in df.iterrows():
        ticker = row['ticker']
        if ticker == 'CASH': continue
        qty = float(row['quantity'])
        action = row['action']
        
        if ticker not in positions: positions[ticker] = 0.0
        if action == 'BUY': positions[ticker] += qty
        elif action == 'SELL': positions[ticker] -= qty
            
    # Filter out stocks we've completely sold
    return {t: q for t, q in positions.items() if q > 0}

def run_sandbox(target_ticker):
    print(f"\n--- INITIATING CORRELATION SANDBOX ---")
    
    holdings = get_current_holdings()
    if not holdings:
        print("Your portfolio is currently empty.")
        return
        
    current_tickers = list(holdings.keys())
    print(f"Current Portfolio: {current_tickers}")
    
    # We add the Nifty 50 benchmark to the list of things to download
    benchmark_ticker = "^NSEI"
    all_tickers = current_tickers + [target_ticker, benchmark_ticker]
    
    print("\nDownloading 1-year historical data for matrix analysis...")
    data = yf.download(all_tickers, period="1y", interval="1d")['Close']
    
    # Calculate daily percentage returns
    returns = data.pct_change().dropna()
    
    # Calculate overall Portfolio Return (Simplified Equal Weight for Baseline)
    if len(current_tickers) > 1:
        returns['Current_Portfolio'] = returns[current_tickers].mean(axis=1)
    else:
        returns['Current_Portfolio'] = returns[current_tickers[0]]
        
    # 1. MACRO MARKET EXPOSURE (Your Portfolio vs Nifty 50)
    nifty_correlation = returns['Current_Portfolio'].corr(returns[benchmark_ticker])
    print(f"\n=== MACRO MARKET EXPOSURE ===")
    print(f"Portfolio Correlation with Nifty 50: {nifty_correlation:.2f}")
    
    if nifty_correlation > 0.8:
        print("-> [INDEX HUGGER]: You are highly correlated. You essentially move exactly with the broader market.")
    elif nifty_correlation < 0.3:
        print("-> [DECOUPLED]: You are highly decoupled from the market. Your performance path is unique.")
    else:
        print("-> [MODERATE]: You have moderate correlation with the broader market.")
    
    # 2. PRE-TRADE ANALYSIS (Target Stock vs Your Portfolio)
    print(f"\n=== TARGET ANALYSIS: {target_ticker} ===")
    print(f"How {target_ticker} moves compared to your specific stocks:")
    
    # Calculate matrix strictly for the stocks
    stock_returns = returns[current_tickers + [target_ticker]]
    correlation_matrix = stock_returns.corr()
    
    target_correlations = correlation_matrix[target_ticker].drop(target_ticker)
    for ticker, corr in target_correlations.items():
        print(f"  -> vs {ticker}: {corr:.2f}")
    
    port_correlation = returns[target_ticker].corr(returns['Current_Portfolio'])
    
    print(f"\n=== MACRO PORTFOLIO IMPACT ===")
    print(f"Adding {target_ticker} Correlation with Overall Portfolio: {port_correlation:.2f}")
    
    if port_correlation > 0.7:
        print(f"[!] DANGER: {target_ticker} moves almost exactly like your current portfolio. Minimal diversification added.")
    elif port_correlation > 0.4:
        print(f"[-] MODERATE: {target_ticker} has some overlap. Acceptable, but monitor your sector exposure.")
    elif port_correlation > -0.2:
        print(f"[✓] EXCELLENT: {target_ticker} is largely uncorrelated. True institutional diversification.")
    else:
        print(f"[🛡️] HEDGE: {target_ticker} moves opposite to your portfolio. It acts as a defensive shield.")
        
    print("---------------------------------------")

if __name__ == "__main__":
    # Test ITC against your portfolio AND the Nifty 50
    run_sandbox("ITC.NS")