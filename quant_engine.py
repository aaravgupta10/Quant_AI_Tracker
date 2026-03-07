import os
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
import warnings
from nav_utils import normalize_ledger_dates

warnings.filterwarnings("ignore")
load_dotenv()
supabase: Client = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))


def fetch_ledger():
    response = supabase.table('transactions').select("*").execute()
    if not response.data:
        return None
    df = pd.DataFrame(response.data)
    return normalize_ledger_dates(df)


def build_time_machine(ledger_df):
    if ledger_df is None or ledger_df.empty:
        return pd.DataFrame()

    start_date = ledger_df['date'].min().strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')

    stock_ledger = ledger_df[(ledger_df['ticker'] != 'CASH') & (ledger_df['action'].isin(['BUY', 'SELL']))]
    tickers_traded = stock_ledger['ticker'].unique().tolist()

    calendar = pd.date_range(start=start_date, end=end_date, freq='D')

    price_matrix = pd.DataFrame(index=calendar)
    fallback_prices = {t: 0.0 for t in tickers_traded}
    if tickers_traded:
        market_response = (
            supabase.table('market_data')
            .select('date, ticker, close_price')
            .gte('date', start_date)
            .in_('ticker', tickers_traded)
            .execute()
        )

        prices_df = pd.DataFrame(market_response.data)
        if not prices_df.empty:
            prices_df['date'] = pd.to_datetime(prices_df['date']).dt.tz_localize(None).dt.normalize()
            price_matrix = prices_df.pivot(index='date', columns='ticker', values='close_price')
            # Do not backfill from future values; only carry known historical closes forward.
            price_matrix = price_matrix.reindex(calendar).ffill()

    daily_nav = []
    current_positions = {t: 0.0 for t in tickers_traded}

    # MF-style unitization based only on external cash flows (CASH deposit/withdraw).
    unit_nav = 100.0
    total_units = 0.0
    cash_balance = 0.0
    net_external_invested = 0.0

    for current_date in calendar:
        if not price_matrix.empty:
            for ticker in tickers_traded:
                try:
                    p = float(price_matrix.at[current_date, ticker])
                    if pd.notna(p) and p > 0:
                        fallback_prices[ticker] = p
                except Exception:
                    pass

        trades_today = ledger_df[ledger_df['date'] == current_date]
        external_cash_flow = 0.0

        for _, trade in trades_today.iterrows():
            ticker = trade['ticker']
            action = str(trade['action']).upper()
            qty = float(trade['quantity'])
            price = float(trade['price'])
            trade_val = qty * price

            if ticker == 'CASH':
                if action in ('DEPOSIT', 'BUY'):
                    cash_balance += trade_val
                    external_cash_flow += trade_val
                    net_external_invested += trade_val
                elif action in ('WITHDRAW', 'SELL'):
                    cash_balance -= trade_val
                    external_cash_flow -= trade_val
                    net_external_invested -= trade_val
                continue

            # Use trade price as same-day fallback if market close is missing.
            fallback_prices[ticker] = price

            if action == 'BUY':
                current_positions[ticker] = current_positions.get(ticker, 0.0) + qty
                cash_balance -= trade_val
            elif action == 'SELL':
                current_positions[ticker] = current_positions.get(ticker, 0.0) - qty
                cash_balance += trade_val

        equity_value = sum(qty * fallback_prices.get(t, 0.0) for t, qty in current_positions.items())
        eod_asset_value = equity_value + cash_balance

        # Units only change for external capital flows.
        if external_cash_flow != 0:
            if total_units > 0:
                pre_flow_nav = (eod_asset_value - external_cash_flow) / total_units
                if pre_flow_nav <= 0:
                    pre_flow_nav = 100.0
            else:
                pre_flow_nav = 100.0
            total_units += (external_cash_flow / pre_flow_nav)

        if total_units > 0:
            unit_nav = eod_asset_value / total_units
        else:
            unit_nav = 100.0

        daily_nav.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'total_nav': round(eod_asset_value, 2),
            'unit_nav': round(unit_nav, 4),
            'net_invested': round(net_external_invested, 2)
        })

    return pd.DataFrame(daily_nav)


def calculate_risk_metrics(nav_df, risk_free_rate=0.07):
    print("Fetching Nifty 50 Benchmark for Risk Math...")

    active_nav_df = nav_df[nav_df['unit_nav'] > 0].copy()
    if active_nav_df.empty or len(active_nav_df) < 2:
        return {"Max Drawdown": 0.0, "Beta": 0.0, "Sharpe Ratio": 0.0, "Alpha": 0.0}

    start_date = active_nav_df['date'].min()
    end_date = active_nav_df['date'].max()

    bench_hist = yf.Ticker('^NSEI').history(start=start_date, end=pd.to_datetime(end_date) + pd.Timedelta(days=1))
    bench_df = bench_hist[['Close']].reset_index()
    bench_df['Date'] = bench_df['Date'].dt.tz_localize(None).dt.strftime('%Y-%m-%d')
    bench_df.rename(columns={'Date': 'date', 'Close': 'nifty_close'}, inplace=True)

    df = pd.merge(active_nav_df, bench_df, on='date', how='inner')
    df['nifty_close'] = df['nifty_close'].ffill().bfill()

    # Returns are based on unit NAV, independent of cash contributions/withdrawals.
    df['port_return'] = df['unit_nav'].pct_change()
    df['bench_return'] = df['nifty_close'].pct_change()
    df = df.dropna()

    if df.empty:
        return {"Max Drawdown": 0.0, "Beta": 0.0, "Sharpe Ratio": 0.0, "Alpha": 0.0}

    cumulative_returns = (1 + df['port_return']).cumprod()
    peak = cumulative_returns.cummax()
    drawdown = (cumulative_returns - peak) / peak

    cov_matrix = np.cov(df['port_return'], df['bench_return'])
    beta = cov_matrix[0, 1] / cov_matrix[1, 1] if cov_matrix[1, 1] != 0 else 0

    trading_days = 252
    ann_port_return = df['port_return'].mean() * trading_days
    ann_bench_return = df['bench_return'].mean() * trading_days
    ann_port_volatility = df['port_return'].std() * np.sqrt(trading_days)

    sharpe_ratio = (ann_port_return - risk_free_rate) / ann_port_volatility if ann_port_volatility != 0 else 0
    alpha = ann_port_return - (risk_free_rate + beta * (ann_bench_return - risk_free_rate))

    return {
        "Max Drawdown": float(drawdown.min()) if pd.notna(drawdown.min()) else 0.0,
        "Beta": float(beta) if pd.notna(beta) else 0.0,
        "Sharpe Ratio": float(sharpe_ratio) if pd.notna(sharpe_ratio) else 0.0,
        "Alpha": float(alpha) if pd.notna(alpha) else 0.0
    }


if __name__ == "__main__":
    print("--- INITIATING QUANT ENGINE (PHASE 4 FINAL) ---")
    ledger_df = fetch_ledger()

    if ledger_df is not None and not ledger_df.empty:
        nav_history = build_time_machine(ledger_df)
        if not nav_history.empty:
            metrics = calculate_risk_metrics(nav_history)

            current_nav = nav_history.iloc[-1]['total_nav']
            unit_nav = nav_history.iloc[-1]['unit_nav']
            net_invested = nav_history.iloc[-1]['net_invested']
            all_time_pnl = current_nav - net_invested
            pnl_percentage = (all_time_pnl / abs(net_invested)) * 100 if net_invested != 0 else 0.0

            print("\n=== ABSOLUTE PERFORMANCE ===")
            print(f"Total Net Capital Invested : Rs{net_invested:,.2f}")
            print(f"Total Asset Value          : Rs{current_nav:,.2f}")
            print(f"Mutual Fund Unit NAV       : Rs{unit_nav:,.4f}")
            print(f"All-Time Profit/Loss       : Rs{all_time_pnl:,.2f} ({pnl_percentage:.2f}%)")

            print("\n=== RISK & ALPHA METRICS ===")
            print(f"Portfolio Alpha            : {metrics['Alpha'] * 100:.2f}%")
            print(f"Portfolio Beta             : {metrics['Beta']:.2f}")

            records_to_upsert = []
            for _, row in nav_history.iterrows():
                records_to_upsert.append({
                    "date": row['date'],
                    "total_nav": row['total_nav'],
                    "unit_nav": row.get('unit_nav', 100.0),
                    "portfolio_beta": round(metrics['Beta'], 4),
                    "portfolio_sharpe": round(metrics['Sharpe Ratio'], 4),
                    "max_drawdown": round(metrics['Max Drawdown'], 4),
                    "alpha": round(metrics['Alpha'], 4)
                })

            for i in range(0, len(records_to_upsert), 500):
                supabase.table('portfolio_metrics').upsert(records_to_upsert[i:i+500], on_conflict="date").execute()

            print("\n--- ENGINE EXECUTION COMPLETE ---")
