import os
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client
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
    if "password_correct" in st.session_state: st.error("Access Denied.")
    return False

if "APP_PASSWORD" in st.secrets:
    if not check_password(): st.stop()

load_dotenv()
url = os.environ.get("SUPABASE_URL", st.secrets.get("SUPABASE_URL"))
key = os.environ.get("SUPABASE_KEY", st.secrets.get("SUPABASE_KEY"))
supabase: Client = create_client(url, key)

st.title("🏛️ Institutional Quant OS")
st.markdown("Live Portfolio Analytics, Risk, & Forecasting")
st.divider()

@st.cache_data(ttl=60)
def pull_vault_data():
    metrics_res = supabase.table('portfolio_metrics').select('*').order('date', desc=False).execute()
    tx_res = supabase.table('transactions').select('*').execute()
    return metrics_res.data, tx_res.data

metrics_data, tx_data = pull_vault_data()

# --- THE BUG FIX: STRICT EQUITY CAPITAL TRACKING ---
positions = {}
equity_net_invested = 0.0

if tx_data:
    df_tx = pd.DataFrame(tx_data)
    for _, row in df_tx.iterrows():
        ticker = row['ticker']
        if ticker == 'CASH': continue # CASH IS DEAD
        
        qty = float(row['quantity'])
        price = float(row['price'])
        action = row['action']
        val = qty * price
        
        if action == 'BUY':
            positions[ticker] = positions.get(ticker, 0) + qty
            equity_net_invested += val
        elif action == 'SELL':
            positions[ticker] = positions.get(ticker, 0) - qty
            equity_net_invested -= val

active_positions = {t: q for t, q in positions.items() if q > 0}
current_tickers = list(active_positions.keys())

@st.cache_data(ttl=60)
def get_live_prices_safely(tickers):
    prices = {t: 0.0 for t in tickers}
    if not tickers: return prices
    try:
        data = yf.download(tickers, period="5d")
        close_data = data['Close'] if 'Close' in data else data
        for t in tickers:
            try:
                series = close_data[t].dropna() if isinstance(close_data, pd.DataFrame) and t in close_data.columns else close_data.dropna()
                if not series.empty: prices[t] = float(series.iloc[-1])
            except: continue
    except Exception: pass
    return prices

# LIVE P&L MATH
live_prices = get_live_prices_safely(current_tickers)
live_equity = sum([live_prices.get(t, 0.0) * active_positions[t] for t in current_tickers])

# Now equity_net_invested is accurate, so P&L will finally display properly
all_time_pnl = live_equity - equity_net_invested
pnl_pct = (all_time_pnl / equity_net_invested * 100) if equity_net_invested > 0 else 0.0

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Portfolio Curve", "⚖️ Auto-Rebalancer", "🎲 Monte Carlo Risk", 
    "🧪 Correlation Sandbox", "📝 Trade Manager", "🔮 DCF Valuation"
])

with tab1:
    st.subheader("Macro Risk & Performance")
    col1, col2, col3, col4, col5 = st.columns(5)
    
    col1.metric("Total Asset Value", f"₹{live_equity:,.2f}")
    col2.metric("All-Time Profit/Loss", f"₹{all_time_pnl:,.2f}", f"{pnl_pct:.2f}%")
    
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
            st.info(f"**Live Invested Equity:** ₹{live_equity:,.2f}")
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
                                st.markdown(f"**How {target_ticker} correlates with your individual holdings:**")
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
                        st.markdown("### 🏛️ Base Fundamentals")
                        st.info(f"**Current Price:** ₹{current_price:,.2f}")
                        st.info(f"**Shares Outstanding:** {shares_out:,.0f}")
                        starting_fcf = st.number_input("Starting Free Cash Flow (₹)", value=float(fcf_api) if fcf_api else 10000000000.0)
                    with col_assump:
                        st.markdown("### 🎚️ Your Assumptions")
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