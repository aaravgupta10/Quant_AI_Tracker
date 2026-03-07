import os
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

# Unlock the vault
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def fetch_ledger():
    response = supabase.table('transactions').select("*").execute()
    if not response.data: return None
    df = pd.DataFrame(response.data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(by='date').reset_index(drop=True)
    return df

def build_time_machine(ledger_df):
    start_date = ledger_df['date'].min().strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    tickers_traded = ledger_df[ledger_df['ticker'] != 'CASH']['ticker'].unique().tolist()
    
    market_response = supabase.table('market_data') \
        .select('date, ticker, close_price') \
        .gte('date', start_date) \
        .in_('ticker', tickers_traded) \
        .execute()
        
    prices_df = pd.DataFrame(market_response.data)
    prices_df['date'] = pd.to_datetime(prices_df['date'])
    price_matrix = prices_df.pivot(index='date', columns='ticker', values='close_price')
    
    calendar = pd.date_range(start=start_date, end=end_date, freq='D')
    price_matrix = price_matrix.reindex(calendar).ffill()
    
    daily_nav = []
    current_cash = 0.0
    net_invested = 0.0 # Tracking actual cash injected vs withdrawn
    current_positions = {ticker: 0.0 for ticker in tickers_traded}
    
    for current_date in calendar:
        trades_today = ledger_df[ledger_df['date'] == current_date]
        
        for _, trade in trades_today.iterrows():
            action = trade['action']
            ticker = trade['ticker']
            total_value = float(trade['quantity']) * float(trade['price'])
            
            if ticker == 'CASH':
                if action == 'DEPOSIT': 
                    current_cash += total_value
                    net_invested += total_value
                elif action == 'WITHDRAW': 
                    current_cash -= total_value
                    net_invested -= total_value
            else:
                qty = float(trade['quantity'])
                if action == 'BUY':
                    current_cash -= total_value
                    current_positions[ticker] += qty
                elif action == 'SELL':
                    current_cash += total_value
                    current_positions[ticker] -= qty
        
        stock_value_today = 0.0
        for ticker, qty in current_positions.items():
            if qty > 0:
                # Institutional Armor: Safely handle non-index tickers or missing data
                try:
                    price_today = float(price_matrix.at[current_date, ticker])
                    if pd.isna(price_today):
                        price_today = 0.0
                except KeyError:
                    # If the ticker wasn't downloaded, default its value to 0 for this specific day
                    price_today = 0.0
                if pd.notna(price_today):
                    stock_value_today += (qty * price_today)
        
        total_nav = current_cash + stock_value_today
        daily_nav.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'total_nav': round(total_nav, 2),
            'net_invested': round(net_invested, 2) # Adding this to calculate P&L easily
        })
        
    return pd.DataFrame(daily_nav)

def calculate_risk_metrics(nav_df, risk_free_rate=0.07):
    print("Fetching Nifty 50 Benchmark for Risk Math...")
    start_date = nav_df['date'].min()
    end_date = nav_df['date'].max()
    
    benchmark = yf.Ticker('^NSEI')
    bench_hist = benchmark.history(start=start_date, end=pd.to_datetime(end_date) + pd.Timedelta(days=1))
    
    bench_df = bench_hist[['Close']].reset_index()
    bench_df['Date'] = bench_df['Date'].dt.tz_localize(None).dt.strftime('%Y-%m-%d')
    bench_df.rename(columns={'Date': 'date', 'Close': 'nifty_close'}, inplace=True)
    
    df = pd.merge(nav_df, bench_df, on='date', how='left')
    df['nifty_close'] = df['nifty_close'].ffill()
    
    df['port_return'] = df['total_nav'].pct_change()
    df['bench_return'] = df['nifty_close'].pct_change()
    df = df.dropna()
    
    cumulative_returns = (1 + df['port_return']).cumprod()
    peak = cumulative_returns.cummax()
    drawdown = (cumulative_returns - peak) / peak
    max_drawdown = drawdown.min()
    
    cov_matrix = np.cov(df['port_return'], df['bench_return'])
    beta = cov_matrix[0, 1] / cov_matrix[1, 1]
    
    trading_days = 252
    ann_port_return = df['port_return'].mean() * trading_days
    ann_bench_return = df['bench_return'].mean() * trading_days
    ann_port_volatility = df['port_return'].std() * np.sqrt(trading_days)
    
    sharpe_ratio = (ann_port_return - risk_free_rate) / ann_port_volatility
    alpha = ann_port_return - (risk_free_rate + beta * (ann_bench_return - risk_free_rate))
    
    return {
        "Max Drawdown": float(max_drawdown),
        "Beta": float(beta),
        "Sharpe Ratio": float(sharpe_ratio),
        "Alpha": float(alpha)
    }

if __name__ == "__main__":
    print("--- INITIATING QUANT ENGINE (PHASE 4 FINAL) ---")
    ledger_df = fetch_ledger()
    
    if ledger_df is not None:
        nav_history = build_time_machine(ledger_df)
        metrics = calculate_risk_metrics(nav_history)
        
        # Get the absolute latest numbers for P&L
        current_nav = nav_history.iloc[-1]['total_nav']
        net_invested = nav_history.iloc[-1]['net_invested']
        all_time_pnl = current_nav - net_invested
        pnl_percentage = (all_time_pnl / net_invested) * 100 if net_invested > 0 else 0

        print("\n=== ABSOLUTE PERFORMANCE ===")
        print(f"Total Net Cash Deposited   : ₹{net_invested:,.2f}")
        print(f"Current Portfolio Value    : ₹{current_nav:,.2f}")
        print(f"All-Time Profit/Loss       : ₹{all_time_pnl:,.2f} ({pnl_percentage:.2f}%)")
        
        print("\n=== RISK & ALPHA METRICS ===")
        print(f"Portfolio Alpha            : {metrics['Alpha'] * 100:.2f}%")
        print(f"Portfolio Beta             : {metrics['Beta']:.2f}")
        print(f"Sharpe Ratio               : {metrics['Sharpe Ratio']:.2f}")
        print(f"Maximum Drawdown           : {metrics['Max Drawdown'] * 100:.2f}%")
        
        print("\nSaving metrics to the Supabase Vault...")
        today_str = datetime.now().strftime('%Y-%m-%d')
        supabase.table('portfolio_metrics').upsert({
            "date": today_str,
            "total_nav": current_nav,
            "portfolio_beta": round(metrics['Beta'], 4),
            "portfolio_sharpe": round(metrics['Sharpe Ratio'], 4),
            "max_drawdown": round(metrics['Max Drawdown'], 4)
        }).execute()
        
    print("\n--- ENGINE EXECUTION COMPLETE ---")