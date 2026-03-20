import os
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from supabase import Client, create_client

from nav_utils import build_portfolio_state, normalize_ledger_dates

load_dotenv()
supabase: Client = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))


def fetch_ledger() -> pd.DataFrame | None:
    try:
        response = supabase.table("transactions").select("*").execute()
        if not response.data:
            return None
        df = pd.DataFrame(response.data)
        return normalize_ledger_dates(df)
    except Exception as e:
        print(f"Error fetching ledger: {e}")
        return None


def _get_price_matrix(start_date: str, end_date: str, tickers: list[str]) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame(index=pd.date_range(start=start_date, end=end_date, freq="D"))

    try:
        market_response = (
            supabase.table("market_data")
            .select("date, ticker, close_price")
            .gte("date", start_date)
            .in_("ticker", tickers)
            .execute()
        )
        data = market_response.data
    except Exception as e:
        print(f"Error fetching market data: {e}")
        data = []
    prices_df = pd.DataFrame(data)
    calendar = pd.date_range(start=start_date, end=end_date, freq="D")

    if prices_df.empty:
        return pd.DataFrame(index=calendar)

    prices_df["date"] = pd.to_datetime(prices_df["date"]).dt.tz_localize(None).dt.normalize()
    price_matrix = prices_df.pivot(index="date", columns="ticker", values="close_price")
    # Forward fill to avoid lookahead from future dates.
    return price_matrix.reindex(calendar).ffill()


def build_time_machine(ledger_df: pd.DataFrame) -> pd.DataFrame:
    if ledger_df is None or ledger_df.empty:
        return pd.DataFrame()

    start_date = ledger_df["date"].min().strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    calendar = pd.date_range(start=start_date, end=end_date, freq="D")

    stock_ledger = ledger_df[
        (ledger_df["ticker"].astype(str).str.upper() != "CASH")
        & (ledger_df["action"].astype(str).str.upper().isin(["BUY", "SELL"]))
    ].copy()
    tickers_traded = stock_ledger["ticker"].astype(str).str.upper().str.strip().unique().tolist()

    price_matrix = _get_price_matrix(start_date, end_date, tickers_traded)
    fallback_prices = {t: 0.0 for t in tickers_traded}
    current_positions = {t: 0.0 for t in tickers_traded}

    # NAV is strictly invested equity value (cash excluded by design).
    unit_nav = 100.0
    prev_equity_value = None
    equity_net_invested = 0.0
    records: list[dict] = []

    for current_date in calendar:
        if not price_matrix.empty:
            for ticker in tickers_traded:
                try:
                    p = float(price_matrix.at[current_date, ticker])
                    if pd.notna(p) and p > 0:
                        fallback_prices[ticker] = p
                except Exception:
                    pass

        trades_today = ledger_df[ledger_df["date"] == current_date]
        net_external_flow = 0.0

        for _, trade in trades_today.iterrows():
            ticker = str(trade.get("ticker", "")).upper().strip()
            action = str(trade.get("action", "")).upper().strip()
            qty = abs(float(trade.get("quantity") or 0.0))
            price = float(trade.get("price") or 0.0)
            trade_val = qty * price

            if ticker == "CASH":
                # Cash must not participate in performance math.
                continue

            if action not in ("BUY", "SELL"):
                continue

            fallback_prices[ticker] = price
            current_positions[ticker] = current_positions.get(ticker, 0.0)

            if action == "BUY":
                current_positions[ticker] += qty
                net_external_flow += trade_val
                equity_net_invested += trade_val
            else:
                current_positions[ticker] -= qty
                net_external_flow -= trade_val
                equity_net_invested -= trade_val

        equity_value = sum(q * fallback_prices.get(t, 0.0) for t, q in current_positions.items())

        # Daily TWR-style return on invested assets only.
        if prev_equity_value is not None and prev_equity_value > 0:
            daily_return = (equity_value - prev_equity_value - net_external_flow) / prev_equity_value
            if pd.notna(daily_return):
                unit_nav *= (1.0 + daily_return)
        elif equity_value > 0:
            unit_nav = 100.0

        records.append(
            {
                "date": current_date.strftime("%Y-%m-%d"),
                "total_nav": round(float(equity_value), 2),
                "unit_nav": round(float(unit_nav), 4),
                "net_invested": round(float(equity_net_invested), 2),
            }
        )

        prev_equity_value = equity_value

    return pd.DataFrame(records)


def calculate_risk_metrics(nav_df: pd.DataFrame, risk_free_rate: float = 0.07) -> dict:
    active_nav_df = nav_df[nav_df["unit_nav"] > 0].copy()
    if active_nav_df.empty or len(active_nav_df) < 2:
        return {"Max Drawdown": 0.0, "Beta": 0.0, "Sharpe Ratio": 0.0, "Alpha": 0.0}

    start_date = active_nav_df["date"].min()
    end_date = active_nav_df["date"].max()

    bench_hist = yf.Ticker("^NSEI").history(start=start_date, end=pd.to_datetime(end_date) + pd.Timedelta(days=1))
    bench_df = bench_hist[["Close"]].reset_index()
    bench_df["Date"] = bench_df["Date"].dt.tz_localize(None).dt.strftime("%Y-%m-%d")
    bench_df.rename(columns={"Date": "date", "Close": "nifty_close"}, inplace=True)

    df = pd.merge(active_nav_df, bench_df, on="date", how="inner")
    df["nifty_close"] = df["nifty_close"].ffill().bfill()
    df["port_return"] = df["unit_nav"].pct_change()
    df["bench_return"] = df["nifty_close"].pct_change()
    df = df.dropna()

    if df.empty:
        return {"Max Drawdown": 0.0, "Beta": 0.0, "Sharpe Ratio": 0.0, "Alpha": 0.0}

    cumulative_returns = (1 + df["port_return"]).cumprod()
    peak = cumulative_returns.cummax()
    drawdown = (cumulative_returns - peak) / peak

    cov_matrix = np.cov(df["port_return"], df["bench_return"])
    beta = cov_matrix[0, 1] / cov_matrix[1, 1] if cov_matrix[1, 1] != 0 else 0.0

    trading_days = 252
    ann_port_return = df["port_return"].mean() * trading_days
    ann_bench_return = df["bench_return"].mean() * trading_days
    ann_port_volatility = df["port_return"].std() * np.sqrt(trading_days)

    sharpe_ratio = (ann_port_return - risk_free_rate) / ann_port_volatility if ann_port_volatility != 0 else 0.0
    alpha = ann_port_return - (risk_free_rate + beta * (ann_bench_return - risk_free_rate))

    return {
        "Max Drawdown": float(drawdown.min()) if pd.notna(drawdown.min()) else 0.0,
        "Beta": float(beta) if pd.notna(beta) else 0.0,
        "Sharpe Ratio": float(sharpe_ratio) if pd.notna(sharpe_ratio) else 0.0,
        "Alpha": float(alpha) if pd.notna(alpha) else 0.0,
    }


if __name__ == "__main__":
    print("--- INITIATING QUANT ENGINE ---")
    ledger_df = fetch_ledger()

    if ledger_df is not None and not ledger_df.empty:
        nav_history = build_time_machine(ledger_df)
        if not nav_history.empty:
            metrics = calculate_risk_metrics(nav_history)

            current_nav = float(nav_history.iloc[-1]["total_nav"])
            unit_nav = float(nav_history.iloc[-1]["unit_nav"])
            state = build_portfolio_state(ledger_df)
            open_cost_basis = float(state.get("open_cost_basis_total", 0.0))
            all_time_pnl = current_nav - open_cost_basis
            pnl_percentage = (all_time_pnl / abs(open_cost_basis)) * 100 if open_cost_basis != 0 else 0.0

            print("\n=== ABSOLUTE PERFORMANCE ===")
            print(f"Open Position Cost Basis : Rs{open_cost_basis:,.2f}")
            print(f"Total Equity Value       : Rs{current_nav:,.2f}")
            print(f"Unit NAV (Base 100)      : Rs{unit_nav:,.4f}")
            print(f"All-Time Unrealised P/L  : Rs{all_time_pnl:,.2f} ({pnl_percentage:.2f}%)")

            records_to_upsert = []
            for _, row in nav_history.iterrows():
                records_to_upsert.append(
                    {
                        "date": row["date"],
                        "total_nav": row["total_nav"],
                        "unit_nav": row.get("unit_nav", 100.0),
                        "portfolio_beta": round(metrics["Beta"], 4),
                        "portfolio_sharpe": round(metrics["Sharpe Ratio"], 4),
                        "max_drawdown": round(metrics["Max Drawdown"], 4),
                        "alpha": round(metrics["Alpha"], 4),
                    }
                )

            try:
                for i in range(0, len(records_to_upsert), 500):
                    supabase.table("portfolio_metrics").upsert(records_to_upsert[i : i + 500], on_conflict="date").execute()
            except Exception as e:
                print(f"Error upserting metrics to Supabase: {e}")

            print("\n--- ENGINE EXECUTION COMPLETE ---")
