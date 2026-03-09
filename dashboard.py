import os
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from nav_utils import build_portfolio_state
import warnings

warnings.filterwarnings("ignore")
st.set_page_config(page_title="Quant OS", page_icon="🏛️", layout="wide")

def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets.get("APP_PASSWORD", "admin"):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False): return True
    st.markdown("### 🔒 Vault Locked")
    st.text_input("Enter Institutional Master Key", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state: st.error("Access Denied. Incorrect Key.")
    return False

if "APP_PASSWORD" in st.secrets:
    if not check_password(): st.stop()

load_dotenv()
supabase: Client = create_client(os.environ.get("SUPABASE_URL", st.secrets.get("SUPABASE_URL")), os.environ.get("SUPABASE_KEY", st.secrets.get("SUPABASE_KEY")))

st.title("🏛️ Institutional Quant OS")
st.markdown("Live Portfolio Analytics, Risk, & Forecasting")
st.divider()

@st.cache_data(ttl=60)
def pull_vault_data():
    metrics_res = supabase.table('portfolio_metrics').select('*').order('date', desc=False).execute()
    tx_res = supabase.table('transactions').select('*').execute()
    return metrics_res.data, tx_res.data

metrics_data, tx_data = pull_vault_data()

# Unified portfolio state; NAV configured to ignore cash balance
state = build_portfolio_state(pd.DataFrame(tx_data) if tx_data else pd.DataFrame())
active_positions = state['active_positions']
open_cost_basis = float(state.get('open_cost_basis_total', 0.0))

current_tickers = list(active_positions.keys())

@st.cache_data(ttl=60)
def get_market_data_prices(tickers):
    """Fetch the latest available close price from the Supabase market_data table."""
    prices = {t: 0.0 for t in tickers}
    if not tickers:
        return prices

    try:
        result = (supabase.table('market_data')
                  .select('date,ticker,close_price')
                  .in_('ticker', tickers)
                  .order('date', desc=True)
                  .limit(5000)
                  .execute())
        rows = result.data or []
        seen = set()
        for row in rows:
            ticker = row.get('ticker')
            if not ticker or ticker in seen:
                continue
            prices[ticker] = float(row.get('close_price') or 0.0)
            seen.add(ticker)
            if len(seen) == len(tickers):
                break
    except Exception:
        pass
    return prices


@st.cache_data(ttl=60)
def get_live_prices_safely(tickers):
    """Fallback to Yahoo Finance if Supabase market data is unavailable."""
    prices = {t: 0.0 for t in tickers}
    if not tickers: return prices
    try:
        data = yf.download(tickers, period="5d")
        close_data = data['Close'] if 'Close' in data else data
        for t in tickers:
            try:
                series = close_data[t].dropna() if isinstance(close_data, pd.DataFrame) and t in close_data.columns else close_data.dropna()
                if not series.empty:
                    prices[t] = float(series.iloc[-1])
            except:
                continue
    except:
        pass
    return prices


@st.cache_data(ttl=60)
def refresh_market_data(tickers):
    """Update Supabase market_data for the given tickers using the latest Yahoo close price."""
    if not tickers:
        return "No tickers to refresh."

    records = []
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period="5d")
            if hist.empty:
                continue
            latest = hist['Close'].dropna().iloc[-1]
            latest_date = hist.index[-1].strftime('%Y-%m-%d')
            records.append({
                "date": latest_date,
                "ticker": t,
                "close_price": round(float(latest), 2),
            })
        except Exception:
            continue

    if not records:
        return "No market data could be refreshed."

    try:
        for i in range(0, len(records), 500):
            supabase.table('market_data').upsert(records[i:i+500], on_conflict="date,ticker").execute()
        return f"Refreshed market_data for {len(records)} tickers."
    except Exception as e:
        return f"Failed refreshing market_data: {e}"


# LIVE P&L MATH
market_prices = get_market_data_prices(current_tickers)
missing_tickers = [t for t, p in market_prices.items() if p == 0.0]
if missing_tickers:
    market_prices.update(get_live_prices_safely(missing_tickers))

live_prices = market_prices
live_equity = sum([live_prices.get(t, 0.0) * active_positions[t] for t in current_tickers])
live_total_nav = live_equity

# Track which tickers required a Yahoo Finance fallback
fallback_tickers = [t for t in missing_tickers if live_prices.get(t, 0.0) > 0]

engine_total_nav = None
if metrics_data:
    try:
        engine_total_nav = float(metrics_data[-1].get('total_nav') or 0.0)
    except Exception:
        engine_total_nav = None

pnl_nav_base = engine_total_nav if engine_total_nav is not None and engine_total_nav > 0 else live_total_nav
all_time_pnl = pnl_nav_base - open_cost_basis
pnl_pct = (all_time_pnl / abs(open_cost_basis) * 100) if open_cost_basis != 0 else 0.0

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Portfolio Curve", "⚖️ Auto-Rebalancer", "🎲 Monte Carlo Risk", 
    "🧪 Correlation Sandbox", "📝 Trade Manager", "🔮 DCF Valuation"
])

with tab1:
    st.subheader("Macro Risk & Performance")
    col1, col2, col3, col4, col5 = st.columns(5)

    latest_unit_nav = None
    if metrics_data:
        try:
            latest_unit_nav = float(metrics_data[-1].get('unit_nav') or 0.0)
        except Exception:
            latest_unit_nav = None

    if latest_unit_nav and latest_unit_nav > 0:
        unit_nav_delta = ((latest_unit_nav / 100.0) - 1.0) * 100.0
        col1.metric("Unit NAV (Base 100)", f"₹{latest_unit_nav:,.4f}", f"{unit_nav_delta:.2f}%")
    else:
        col1.metric("Total Asset Value", f"₹{live_total_nav:,.2f}")

    col2.metric("All-Time Unrealised P/L", f"₹{all_time_pnl:,.2f}", f"{pnl_pct:.2f}%")

    if fallback_tickers:
        st.caption(f"Prices for {', '.join(fallback_tickers)} fetched via Yahoo Finance (fallback from Supabase market_data).")
    else:
        st.caption("Prices sourced from Supabase market_data.")
    st.caption("Cash balance is ignored in NAV by configuration.")
    if engine_total_nav is not None:
        st.caption("Unrealised P/L uses latest quant_engine NAV snapshot from portfolio_metrics.")

    if st.button("Refresh Supabase market_data now"):
        with st.spinner("Updating market_data from Yahoo Finance..."):
            refresh_result = refresh_market_data(current_tickers)
            st.success(refresh_result)
            # Clear caches so the updated market_data is used immediately
            pull_vault_data.clear()
            get_market_data_prices.clear()
            get_live_prices_safely.clear()

    if metrics_data:
        m = metrics_data[-1] 
        col3.metric("Portfolio Alpha", f"{m.get('alpha', 0)*100:.2f}%") 
        col4.metric("Portfolio Beta", f"{m.get('portfolio_beta', 0):.2f}")
        col5.metric("Max Drawdown", f"{m.get('max_drawdown', 0)*100:.2f}%")
        
        st.divider()
        st.markdown("### Historical True Growth (Unit NAV vs Nifty 50)")
        df_metrics = pd.DataFrame(metrics_data)
        df_metrics['date'] = pd.to_datetime(df_metrics['date'])
        
        if 'unit_nav' in df_metrics.columns:
            with st.spinner("Fetching Benchmark Data..."):
                start_date = df_metrics['date'].min()
                end_date = df_metrics['date'].max()
                
                bench_hist = yf.Ticker('^NSEI').history(start=start_date, end=end_date + pd.Timedelta(days=1))
                bench_df = bench_hist[['Close']].reset_index()
                bench_df['Date'] = bench_df['Date'].dt.tz_localize(None)
                bench_df.rename(columns={'Date': 'date', 'Close': 'nifty'}, inplace=True)
                
                chart_df = pd.merge(df_metrics[['date', 'unit_nav']], bench_df, on='date', how='left').ffill()
                first_nifty = chart_df['nifty'].iloc[0] if not chart_df.empty else 1
                chart_df['Nifty 50'] = (chart_df['nifty'] / first_nifty) * 100
                chart_df.rename(columns={'unit_nav': 'Portfolio (Unit NAV)'}, inplace=True)
                
                fig_curve = px.line(chart_df, x='date', y=['Portfolio (Unit NAV)', 'Nifty 50'], title='Fund Performance (Base ₹100)')
                fig_curve.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', yaxis_title="Growth (₹)", legend_title_text="")
                st.plotly_chart(fig_curve, use_container_width=True)
        else:
            st.warning("⚠️ Unit NAV not found. Run quant_engine.py in terminal.")

    st.divider()
    col_table, col_chart = st.columns([1, 1])
    with col_table:
        if active_positions:
            holdings_data = []
            for ticker in current_tickers:
                price = live_prices.get(ticker, 0.0)
                val = price * active_positions[ticker]
                holdings_data.append({"Ticker": ticker, "Shares": active_positions[ticker], "Value": val})

            for row in holdings_data:
                weight = (row["Value"] / live_equity * 100) if live_equity > 0 else 0
                row["% Weight"] = weight
                row["Display Weight"] = f"{weight:.2f}%"

            display_df = pd.DataFrame(holdings_data)[["Ticker", "Shares", "Display Weight"]]
            display_df.rename(columns={"Display Weight": "% Weight"}, inplace=True)
            st.dataframe(display_df, hide_index=True, use_container_width=True)
        else:
            st.write("No active stock positions.")
            
    with col_chart:
        if active_positions and 'holdings_data' in locals():
            st.markdown("**Holdings Distribution**")
            valid_holdings = [row for row in holdings_data if row["Value"] > 0]
            if valid_holdings:
                pie_labels = [row["Ticker"] for row in valid_holdings]
                pie_values = [row["% Weight"] for row in valid_holdings]
                fig = px.pie(values=pie_values, names=pie_labels, hole=0.4)
                st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.subheader("Automated Rebalancing Engine")
    if not active_positions: st.warning("No active positions.")
    else:
        if live_equity <= 0: st.error("Cannot calculate rebalance: Market data unavailable.")
        else:
            st.info(f"**Live Total NAV:** ₹{live_total_nav:,.2f}")
            targets = {}
            target_sum = 0
            cols = st.columns(min(len(current_tickers), 4))
            for i, ticker in enumerate(current_tickers):
                with cols[i % 4]:
                    current_weight = ((live_prices.get(ticker, 0.0) * active_positions[ticker]) / live_equity) * 100
                    safe_default = max(0.0, min(float(round(current_weight, 1)), 100.0))
                    targets[ticker] = st.number_input(f"{ticker} (Current: {current_weight:.1f}%)", min_value=0.0, max_value=100.0, value=safe_default, step=1.0)
                    target_sum += targets[ticker]
                    
            if target_sum > 100: st.error(f"Total target weight is {target_sum}%. Must not exceed 100%.")
            else:
                if st.button("Generate Trade List"):
                    trade_plan = []
                    for ticker in current_tickers:
                        target_val = live_equity * (targets[ticker] / 100)
                        current_val = live_prices.get(ticker, 0.0) * active_positions[ticker]
                        delta = target_val - current_val
                        if live_prices.get(ticker, 0.0) > 0:
                            shares_to_trade = delta / live_prices[ticker]
                            if shares_to_trade > 0.5: trade_plan.append({"Action": "BUY", "Ticker": ticker, "Shares": round(shares_to_trade), "Est. Value": f"₹{delta:,.2f}"})
                            elif shares_to_trade < -0.5: trade_plan.append({"Action": "SELL", "Ticker": ticker, "Shares": abs(round(shares_to_trade)), "Est. Value": f"₹{abs(delta):,.2f}"})
                    if trade_plan: st.dataframe(pd.DataFrame(trade_plan).style.applymap(lambda x: 'color: green' if x == 'BUY' else ('color: red' if x == 'SELL' else ''), subset=['Action']), hide_index=True)
                    else: st.success("Your portfolio is perfectly balanced.")

with tab3:
    st.subheader("Monte Carlo Path Simulation (1 Year)")
    if st.button("Run Simulation"):
        if not current_tickers: st.warning("Ensure you have active stocks.")
        else:
            with st.spinner("Crunching synthetic volatility..."):
                try:
                    data = yf.download(current_tickers, period="1y", interval="1d")['Close']
                    if len(current_tickers) == 1: data = data.to_frame(name=current_tickers[0])
                    valid_data = data.dropna(axis=1, how='all')
                    valid_tickers = valid_data.columns.tolist()
                    if not valid_tickers: st.error("No historical data available.")
                    else:
                        returns = valid_data.pct_change().dropna()
                        if live_equity > 0:
                            weights = {t: (live_prices.get(t, 0.0) * active_positions[t]) / live_equity for t in valid_tickers}
                            port_returns = sum([returns[t] * weights[t] for t in valid_tickers])
                            mu, sigma = port_returns.mean(), port_returns.std()
                            days, simulations = 252, 500
                            sim_returns = np.random.normal(mu, sigma, (days, simulations))
                            price_paths = np.zeros_like(sim_returns)
                            price_paths[0] = live_equity
                            for t in range(1, days): price_paths[t] = price_paths[t-1] * (1 + sim_returns[t])
                            fig_mc = go.Figure()
                            for i in range(simulations): fig_mc.add_trace(go.Scatter(y=price_paths[:, i], mode='lines', line=dict(color='rgba(0,100,255,0.05)'), showlegend=False))
                            fig_mc.update_layout(title="500 Simulated Portfolio Paths", xaxis_title="Trading Days", yaxis_title="Projected Equity (₹)", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
                            st.plotly_chart(fig_mc, use_container_width=True)
                            col_w, col_m, col_b = st.columns(3)
                            col_w.error(f"**Bottom 5% (Crash):** ₹{np.percentile(price_paths[-1], 5):,.2f}")
                            col_m.info(f"**Median Expectation:** ₹{np.median(price_paths[-1]):,.2f}")
                            col_b.success(f"**Top 5% (Bull Run):** ₹{np.percentile(price_paths[-1], 95):,.2f}")
                        else: st.error("Portfolio equity is zero.")
                except Exception as e: st.error(f"Failed: {e}")

with tab4:
    st.subheader("Pre-Trade Risk Analysis")
    target_ticker = st.text_input("Enter Target Ticker (e.g., ITC):", key="sandbox_tick").strip().upper()
    if target_ticker and not target_ticker.endswith('.NS') and target_ticker != "^NSEI": target_ticker = f"{target_ticker}.NS"
    if st.button("Run Matrix Analysis"):
        if not current_tickers or not target_ticker: st.warning("Please enter a ticker.")
        else:
            with st.spinner(f"Calculating matrix for {target_ticker}..."):
                try:
                    all_tickers = list(dict.fromkeys(current_tickers + [target_ticker, "^NSEI"]))
                    data = yf.download(all_tickers, period="1y", interval="1d")['Close']
                    returns = data.dropna(axis=1, how='all').pct_change().dropna()
                    if target_ticker not in returns.columns or '^NSEI' not in returns.columns: st.error("Insufficient market data.")
                    else:
                        port_val = sum([live_prices.get(t, 0.0) * active_positions[t] for t in current_tickers if t in returns.columns])
                        if port_val > 0:
                            weights = {t: (live_prices.get(t, 0.0) * active_positions[t]) / port_val for t in current_tickers if t in returns.columns}
                            returns['Current_Portfolio'] = sum([returns[t] * weights[t] for t in weights.keys()])
                            col_macro, col_target = st.columns(2)
                            col_macro.info(f"**True Portfolio vs Nifty 50:** {returns['Current_Portfolio'].corr(returns['^NSEI']):.2f}")
                            col_target.info(f"**{target_ticker} vs True Portfolio:** {returns[target_ticker].corr(returns['Current_Portfolio']):.2f}")
                            analysis_tickers = list(dict.fromkeys([t for t in current_tickers if t in returns.columns] + [target_ticker]))
                            if len(analysis_tickers) > 1:
                                corr_df = pd.DataFrame(returns[analysis_tickers].corr()[target_ticker].drop(target_ticker)).reset_index()
                                corr_df.columns = ['Stock', 'Correlation']
                                st.dataframe(corr_df.style.background_gradient(cmap='RdYlGn_r'), hide_index=True)
                            else: st.success(f"You only own {target_ticker}.")
                        else: st.error("Portfolio valuation failed.")
                except Exception as e: st.error(f"Matrix failed: {e}")

with tab5:
    st.subheader("Log a New Stock Trade")
    with st.form("trade_form", clear_on_submit=True):
        action = st.selectbox("Action", ["BUY", "SELL"])
        ticker_input = st.text_input("Ticker (e.g., RELIANCE, TCS)")
        col_qty, col_price = st.columns(2)
        with col_qty: quantity = st.number_input("Quantity", min_value=0.01, value=1.0)
        with col_price: price = st.number_input("Price", min_value=0.01, value=100.0)
        trade_date = st.date_input("Date of Trade", value=datetime.today())
        
        if st.form_submit_button("Secure Trade in Vault"):
            if not ticker_input: st.error("Please enter a valid ticker.")
            else:
                final_ticker = ticker_input.strip().upper()
                if not final_ticker.endswith('.NS'): final_ticker = f"{final_ticker}.NS"
                try:
                    supabase.table('transactions').insert({
                        "date": trade_date.strftime('%Y-%m-%d'), "ticker": final_ticker, 
                        "action": action, "quantity": float(quantity), "price": float(price)
                    }).execute()
                    st.success(f"[✓] Successfully logged {action} for {final_ticker}!")
                    pull_vault_data.clear()
                except Exception as e: st.error(f"Failed to log trade: {e}")

with tab6:
    st.subheader("Discounted Cash Flow (DCF) Model")
    dcf_ticker = st.text_input("Enter Ticker for DCF (e.g., TCS, INFY):", key="dcf_tick").strip().upper()
    if dcf_ticker and not dcf_ticker.endswith('.NS'): dcf_ticker = f"{dcf_ticker}.NS"
    if dcf_ticker:
        with st.spinner(f"Pulling fundamental data for {dcf_ticker}..."):
            try:
                info = yf.Ticker(dcf_ticker).info
                current_price, shares_out = info.get('currentPrice', 0), info.get('sharesOutstanding', 0)
                total_cash, total_debt, fcf_api = info.get('totalCash', 0), info.get('totalDebt', 0), info.get('freeCashflow', 0)
                
                if current_price == 0 or shares_out == 0: st.error("Missing essential data from Yahoo Finance.")
                else:
                    col_fund, col_assump = st.columns(2)
                    with col_fund:
                        st.info(f"**Current Price:** ₹{current_price:,.2f}")
                        st.info(f"**Shares Outstanding:** {shares_out:,.0f}")
                        starting_fcf = st.number_input("Starting Free Cash Flow (₹)", value=float(fcf_api) if fcf_api else 10000000000.0)
                    with col_assump:
                        growth_rate = st.slider("Expected Growth Rate (Y1-5)", 1.0, 50.0, 15.0, 0.5) / 100
                        discount_rate = st.slider("Discount Rate / WACC", 5.0, 25.0, 12.0, 0.5) / 100
                        terminal_growth = st.slider("Terminal Growth Rate (Y6+)", 1.0, 10.0, 4.0, 0.5) / 100
                        margin_of_safety = st.slider("Margin of Safety", 0.0, 50.0, 20.0, 5.0) / 100
                        
                    if st.button("Calculate Intrinsic Value"):
                        projected_fcfs = [starting_fcf * ((1 + growth_rate) ** i) for i in range(1, 6)]
                        pv_fcfs = sum([fcf / ((1 + discount_rate) ** i) for i, fcf in enumerate(projected_fcfs, 1)])
                        terminal_value = (projected_fcfs[-1] * (1 + terminal_growth)) / (discount_rate - terminal_growth)
                        pv_terminal_value = terminal_value / ((1 + discount_rate) ** 5)
                        equity_value = pv_fcfs + pv_terminal_value + total_cash - total_debt
                        intrinsic_value = equity_value / shares_out
                        target_buy_price = intrinsic_value * (1 - margin_of_safety)
                        
                        st.divider()
                        res_col1, res_col2, res_col3 = st.columns(3)
                        res_col1.metric("Current Market Price", f"₹{current_price:,.2f}")
                        res_col2.metric("Calculated Intrinsic Value", f"₹{intrinsic_value:,.2f}")
                        res_col3.metric(f"Target Buy Price", f"₹{target_buy_price:,.2f}")
            except Exception as e: st.error(f"Failed to calculate DCF: {e}")

