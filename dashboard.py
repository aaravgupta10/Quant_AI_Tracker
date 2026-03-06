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

# ==========================================
# INSTITUTIONAL FIREWALL
# ==========================================
def check_password():
    def password_entered():
        if st.session_state["password"] == st.secrets.get("APP_PASSWORD", "admin"): # Fallback for local testing
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.markdown("### 🔒 Vault Locked")
    st.text_input("Enter Institutional Master Key", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state:
        st.error("Access Denied. Incorrect Key.")
    return False

if "APP_PASSWORD" in st.secrets:
    if not check_password():
        st.stop()

# ==========================================
# VAULT UNLOCKED - LOAD DATA
# ==========================================
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

try:
    if "SUPABASE_URL" in st.secrets:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
except Exception:
    pass

supabase: Client = create_client(url, key)

st.title("🏛️ Institutional Quant OS")
st.markdown("Live Portfolio Analytics, Risk, & Forecasting")
st.divider()

@st.cache_data(ttl=60)
def pull_vault_data():
    # Notice: We order by date ASCENDING so we can draw the timeline chart
    metrics_res = supabase.table('portfolio_metrics').select('*').order('date', asc=True).execute()
    tx_res = supabase.table('transactions').select('*').execute()
    return metrics_res.data, tx_res.data

metrics_data, tx_data = pull_vault_data()

# Calculate active positions
cash_balance = 0.0
positions = {}
if tx_data:
    df_tx = pd.DataFrame(tx_data)
    for _, row in df_tx.iterrows():
        ticker = row['ticker']
        qty = float(row['quantity'])
        price = float(row['price'])
        action = row['action']
        total_val = qty * price
        
        if ticker == 'CASH':
            if action == 'DEPOSIT': cash_balance += total_val
            elif action == 'WITHDRAW': cash_balance -= total_val
        else:
            if action == 'BUY':
                cash_balance -= total_val
                positions[ticker] = positions.get(ticker, 0) + qty
            elif action == 'SELL':
                cash_balance += total_val
                positions[ticker] = positions.get(ticker, 0) - qty

active_positions = {t: q for t, q in positions.items() if q > 0}
current_tickers = list(active_positions.keys())

# --- CREATE 5 TABS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 Portfolio & Equity Curve", 
    "⚖️ Auto-Rebalancer", 
    "🎲 Monte Carlo Risk", 
    "🧪 Correlation Sandbox", 
    "📝 Trade Manager"
])

# ==========================================
# TAB 1: PORTFOLIO OVERVIEW & EQUITY CURVE
# ==========================================
with tab1:
    st.subheader("Macro Risk & Performance")
    if metrics_data:
        m = metrics_data[-1] # Get the absolute latest row for the HUD
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Current Total NAV", f"₹{m['total_nav']:,.2f}")
        col2.metric("Portfolio Alpha", f"{m.get('alpha', 0)*100:.2f}%") 
        col3.metric("Portfolio Beta", f"{m['portfolio_beta']:.2f}")
        col4.metric("Sharpe Ratio", f"{m['portfolio_sharpe']:.2f}")
        col5.metric("Max Drawdown", f"{m['max_drawdown']*100:.2f}%")
        
        st.divider()
        st.markdown("### Historical Equity Curve")
        # FEATURE 1: The Interactive Plotly Chart
        df_metrics = pd.DataFrame(metrics_data)
        fig_curve = px.line(df_metrics, x='date', y='total_nav', title='Net Asset Value Over Time')
        fig_curve.update_traces(line_color='#00FF00', line_width=3) # Institutional Green
        fig_curve.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig_curve, use_container_width=True)

    else:
        st.warning("⚠️ No metrics found. Run quant_engine.py in terminal to populate.")

    st.divider()
    col_table, col_chart = st.columns([1, 1])
    with col_table:
        st.markdown(f"**Available Cash:** ₹{cash_balance:,.2f}")
        if active_positions:
            holdings_df = pd.DataFrame(list(active_positions.items()), columns=['Ticker', 'Shares'])
            st.dataframe(holdings_df, hide_index=True, use_container_width=True)
    with col_chart:
        if active_positions:
            st.markdown("**Holdings Distribution (By Shares)**")
            fig = px.pie(values=list(active_positions.values()), names=list(active_positions.keys()), hole=0.4)
            st.plotly_chart(fig, use_container_width=True)

# ==========================================
# TAB 2: AUTOMATED REBALANCER
# ==========================================
with tab2:
    st.subheader("Automated Rebalancing Engine")
    st.write("Set your target weights. The engine will fetch live prices and generate your required trades.")
    
    if not active_positions:
        st.warning("You need active stock positions to use the rebalancer.")
    else:
        with st.spinner("Fetching live market prices..."):
            live_data = yf.download(current_tickers, period="1d")['Close']
            
            total_stock_value = 0
            live_prices = {}
            for ticker in current_tickers:
                # Handle single ticker edge case for yfinance
                price = float(live_data[ticker].iloc[-1]) if len(current_tickers) > 1 else float(live_data.iloc[-1])
                live_prices[ticker] = price
                total_stock_value += (price * active_positions[ticker])
                
            total_portfolio_value = total_stock_value + cash_balance
            
        st.info(f"**Live Portfolio Value (Including Cash):** ₹{total_portfolio_value:,.2f}")
        
        # User Inputs for target weights
        st.markdown("### Set Target Weights (%)")
        targets = {}
        target_sum = 0
        
        cols = st.columns(min(len(current_tickers), 4))
        for i, ticker in enumerate(current_tickers):
            with cols[i % 4]:
                current_weight = ((live_prices[ticker] * active_positions[ticker]) / total_portfolio_value) * 100
                targets[ticker] = st.number_input(f"{ticker} (Current: {current_weight:.1f}%)", min_value=0.0, max_value=100.0, value=float(round(current_weight, 1)), step=1.0)
                target_sum += targets[ticker]
                
        if target_sum > 100:
            st.error(f"Total target weight is {target_sum}%. It must not exceed 100%.")
        else:
            if st.button("Generate Trade List"):
                st.markdown("### Execution Plan")
                trade_plan = []
                for ticker in current_tickers:
                    target_val = total_portfolio_value * (targets[ticker] / 100)
                    current_val = live_prices[ticker] * active_positions[ticker]
                    delta = target_val - current_val
                    shares_to_trade = delta / live_prices[ticker]
                    
                    if shares_to_trade > 0.5:
                        trade_plan.append({"Action": "BUY", "Ticker": ticker, "Shares": round(shares_to_trade), "Est. Value": f"₹{delta:,.2f}"})
                    elif shares_to_trade < -0.5:
                        trade_plan.append({"Action": "SELL", "Ticker": ticker, "Shares": abs(round(shares_to_trade)), "Est. Value": f"₹{abs(delta):,.2f}"})
                
                if trade_plan:
                    st.dataframe(pd.DataFrame(trade_plan).style.applymap(lambda x: 'color: green' if x == 'BUY' else ('color: red' if x == 'SELL' else ''), subset=['Action']), hide_index=True)
                else:
                    st.success("Your portfolio is perfectly balanced to your targets. No trades needed.")

# ==========================================
# TAB 3: MONTE CARLO STRESS TEST
# ==========================================
with tab3:
    st.subheader("Monte Carlo Path Simulation (1 Year)")
    st.write("Simulating 500 future market scenarios using Geometric Brownian Motion.")
    
    if st.button("Run Simulation"):
        if not current_tickers:
            st.warning("Add stocks to your portfolio first.")
        elif not metrics_data:
            st.warning("Run quant_engine.py first to establish baseline NAV history.")
        else:
            with st.spinner("Crunching historical volatility and projecting probability cones..."):
                # Get historical returns of the overall portfolio
                df_metrics = pd.DataFrame(metrics_data)
                df_metrics['returns'] = df_metrics['total_nav'].pct_change().dropna()
                
                mu = df_metrics['returns'].mean()
                sigma = df_metrics['returns'].std()
                current_nav = float(df_metrics['total_nav'].iloc[-1])
                
                days = 252
                simulations = 500
                
                # Math: Calculate daily price paths
                sim_returns = np.random.normal(mu, sigma, (days, simulations))
                price_paths = np.zeros_like(sim_returns)
                price_paths[0] = current_nav
                
                for t in range(1, days):
                    price_paths[t] = price_paths[t-1] * (1 + sim_returns[t])
                    
                # Visualization
                fig_mc = go.Figure()
                for i in range(simulations):
                    fig_mc.add_trace(go.Scatter(y=price_paths[:, i], mode='lines', line=dict(color='rgba(0,100,255,0.05)'), showlegend=False))
                
                fig_mc.update_layout(title="500 Simulated Portfolio Paths", xaxis_title="Trading Days", yaxis_title="Projected NAV (₹)", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
                st.plotly_chart(fig_mc, use_container_width=True)
                
                # Statistical Output
                final_navs = price_paths[-1]
                worst_case = np.percentile(final_navs, 5)
                best_case = np.percentile(final_navs, 95)
                median_case = np.median(final_navs)
                
                st.markdown("### Probability Matrix (End of Year)")
                col_w, col_m, col_b = st.columns(3)
                col_w.error(f"**Bottom 5% (Crash):** ₹{worst_case:,.2f}")
                col_m.info(f"**Median Expectation:** ₹{median_case:,.2f}")
                col_b.success(f"**Top 5% (Bull Run):** ₹{best_case:,.2f}")

# ==========================================
# TAB 4: CORRELATION SANDBOX
# ==========================================
with tab4:
    st.subheader("Pre-Trade Risk & Diversification Analysis")
    target_ticker = st.text_input("Enter Target Ticker (e.g., ITC, HDFCBANK):").strip().upper()
    if target_ticker and not target_ticker.endswith('.NS') and target_ticker != "^NSEI":
        target_ticker = f"{target_ticker}.NS"
        
    if st.button("Run Matrix Analysis"):
        if not current_tickers: st.warning("Your portfolio is empty.")
        elif not target_ticker: st.warning("Please enter a ticker.")
        else:
            with st.spinner(f"Calculating matrix for {target_ticker}..."):
                all_tickers = current_tickers + [target_ticker, "^NSEI"]
                data = yf.download(all_tickers, period="1y", interval="1d")['Close']
                returns = data.pct_change().dropna()
                if len(current_tickers) > 1: returns['Current_Portfolio'] = returns[current_tickers].mean(axis=1)
                else: returns['Current_Portfolio'] = returns[current_tickers[0]]
                
                nifty_corr = returns['Current_Portfolio'].corr(returns["^NSEI"])
                port_corr = returns[target_ticker].corr(returns['Current_Portfolio'])
                
                stock_returns = returns[current_tickers + [target_ticker]]
                corr_matrix = stock_returns.corr()
                target_corrs = corr_matrix[target_ticker].drop(target_ticker)
                
                col_macro, col_target = st.columns(2)
                with col_macro: st.info(f"**Current Portfolio vs Nifty 50:** {nifty_corr:.2f}")
                with col_target: st.info(f"**{target_ticker} vs Current Portfolio:** {port_corr:.2f}")

                corr_df = pd.DataFrame(target_corrs).reset_index()
                corr_df.columns = ['Stock', 'Correlation']
                st.dataframe(corr_df.style.background_gradient(cmap='RdYlGn_r'), hide_index=True)

# ==========================================
# TAB 5: TRADE MANAGER
# ==========================================
with tab5:
    st.subheader("Log a New Trade")
    with st.form("trade_form", clear_on_submit=True):
        action = st.selectbox("Action", ["BUY", "SELL", "DEPOSIT", "WITHDRAW"])
        ticker_input = st.text_input("Ticker (e.g., RELIANCE, TCS, or CASH)")
        
        col_qty, col_price = st.columns(2)
        with col_qty: quantity = st.number_input("Quantity", min_value=0.01, value=1.0)
        with col_price: price = st.number_input("Price", min_value=0.01, value=100.0)
            
        trade_date = st.date_input("Date of Trade", value=datetime.today())
        submit_button = st.form_submit_button("Secure Trade in Vault")
        
        if submit_button:
            if not ticker_input: st.error("Please enter a valid ticker.")
            else:
                final_ticker = ticker_input.strip().upper()
                if action in ["DEPOSIT", "WITHDRAW"]: final_ticker = 'CASH'
                elif not final_ticker.endswith('.NS'): final_ticker = f"{final_ticker}.NS"
                
                try:
                    supabase.table('transactions').insert({
                        "date": trade_date.strftime('%Y-%m-%d'),
                        "ticker": final_ticker, "action": action,
                        "quantity": float(quantity), "price": float(price)
                    }).execute()
                    st.success(f"[✓] Successfully logged {action} for {final_ticker}!")
                    pull_vault_data.clear()
                except Exception as e:
                    st.error(f"Failed to log trade: {e}")