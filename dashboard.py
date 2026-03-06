import os
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.express as px
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
import warnings

warnings.filterwarnings("ignore")
st.set_page_config(page_title="Quant OS", page_icon="🏛️", layout="wide")

# ==========================================
# INSTITUTIONAL FIREWALL (PASSWORD GATING)
# ==========================================
def check_password():
    """Returns `True` if the user had the correct password."""
    def password_entered():
        # Check if the entered password matches the hidden secret
        if st.session_state["password"] == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't keep password in memory
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    st.markdown("### 🔒 Vault Locked")
    st.text_input(
        "Enter Institutional Master Key", type="password", on_change=password_entered, key="password"
    )
    if "password_correct" in st.session_state:
        st.error("Access Denied. Incorrect Key.")
    return False

# If the password is wrong, stop the entire script right here.
if not check_password():
    st.stop()

# ==========================================
# VAULT UNLOCKED - LOAD THE DASHBOARD
# ==========================================
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

# When deployed on Streamlit, it uses st.secrets. When local, it uses os.environ.
try:
    if "SUPABASE_URL" in st.secrets:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
except Exception:
    pass

supabase: Client = create_client(url, key)

st.title("🏛️ Institutional Quant OS")
st.markdown("Live Portfolio Analytics & Risk Dashboard")
st.divider()

@st.cache_data(ttl=60)
def pull_vault_data():
    metrics_res = supabase.table('portfolio_metrics').select('*').order('date', desc=True).limit(1).execute()
    tx_res = supabase.table('transactions').select('*').execute()
    return metrics_res.data, tx_res.data

metrics_data, tx_data = pull_vault_data()

# Calculate active positions dynamically
cash_balance = 0.0
positions = {}
if tx_data:
    df = pd.DataFrame(tx_data)
    for _, row in df.iterrows():
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

# --- CREATE 3 TABS ---
tab1, tab2, tab3 = st.tabs(["📊 Portfolio Overview", "🧪 Correlation Sandbox", "📝 Trade Manager"])

# TAB 1: PORTFOLIO OVERVIEW
with tab1:
    st.subheader("Macro Risk & Performance")
    if metrics_data:
        m = metrics_data[0]
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total NAV", f"₹{m['total_nav']:,.2f}")
        col2.metric("Portfolio Alpha", f"{m.get('alpha', 0)*100:.2f}%") 
        col3.metric("Portfolio Beta", f"{m['portfolio_beta']:.2f}")
        col4.metric("Sharpe Ratio", f"{m['portfolio_sharpe']:.2f}")
        col5.metric("Max Drawdown", f"{m['max_drawdown']*100:.2f}%")
    else:
        st.warning("⚠️ No metrics found. Engine running in background.")

    st.divider()
    
    col_table, col_chart = st.columns([1, 1])
    with col_table:
        st.markdown(f"**Available Cash:** ₹{cash_balance:,.2f}")
        if active_positions:
            holdings_df = pd.DataFrame(list(active_positions.items()), columns=['Ticker', 'Shares'])
            st.dataframe(holdings_df, hide_index=True, use_container_width=True)
        else:
            st.write("No active stock positions.")
            
    with col_chart:
        if active_positions:
            st.markdown("**Holdings Distribution (By Shares)**")
            fig = px.pie(values=list(active_positions.values()), names=list(active_positions.keys()), hole=0.4)
            st.plotly_chart(fig, use_container_width=True)

# TAB 2: CORRELATION SANDBOX
with tab2:
    st.subheader("Pre-Trade Risk & Diversification Analysis")
    target_ticker = st.text_input("Enter Target Ticker (e.g., ITC, HDFCBANK):").strip().upper()
    
    if target_ticker and not target_ticker.endswith('.NS') and target_ticker != "^NSEI":
        target_ticker = f"{target_ticker}.NS"
        
    if st.button("Run Matrix Analysis"):
        if not current_tickers:
            st.warning("Your portfolio is empty.")
        elif not target_ticker:
            st.warning("Please enter a ticker.")
        else:
            with st.spinner(f"Calculating matrix for {target_ticker}..."):
                all_tickers = current_tickers + [target_ticker, "^NSEI"]
                data = yf.download(all_tickers, period="1y", interval="1d")['Close']
                returns = data.pct_change().dropna()
                
                if len(current_tickers) > 1:
                    returns['Current_Portfolio'] = returns[current_tickers].mean(axis=1)
                else:
                    returns['Current_Portfolio'] = returns[current_tickers[0]]
                
                nifty_corr = returns['Current_Portfolio'].corr(returns["^NSEI"])
                port_corr = returns[target_ticker].corr(returns['Current_Portfolio'])
                
                stock_returns = returns[current_tickers + [target_ticker]]
                corr_matrix = stock_returns.corr()
                target_corrs = corr_matrix[target_ticker].drop(target_ticker)
                
                st.divider()
                st.markdown("### 🔍 Analysis Results")
                
                col_macro, col_target = st.columns(2)
                with col_macro:
                    st.info(f"**Current Portfolio vs Nifty 50:** {nifty_corr:.2f}")
                with col_target:
                    st.info(f"**{target_ticker} vs Current Portfolio:** {port_corr:.2f}")

                st.markdown(f"**How {target_ticker} correlates with your individual stocks:**")
                corr_df = pd.DataFrame(target_corrs).reset_index()
                corr_df.columns = ['Stock', 'Correlation']
                st.dataframe(corr_df.style.background_gradient(cmap='RdYlGn_r'), hide_index=True)

# TAB 3: TRADE MANAGER
with tab3:
    st.subheader("Log a New Trade")
    with st.form("trade_form", clear_on_submit=True):
        action = st.selectbox("Action", ["BUY", "SELL", "DEPOSIT", "WITHDRAW"])
        ticker_input = st.text_input("Ticker (e.g., RELIANCE, TCS, or CASH)")
        
        col_qty, col_price = st.columns(2)
        with col_qty:
            quantity = st.number_input("Quantity", min_value=0.01, value=1.0)
        with col_price:
            price = st.number_input("Price", min_value=0.01, value=100.0)
            
        trade_date = st.date_input("Date of Trade", value=datetime.today())
        submit_button = st.form_submit_button("Secure Trade in Vault")
        
        if submit_button:
            if not ticker_input:
                st.error("Please enter a valid ticker.")
            else:
                final_ticker = ticker_input.strip().upper()
                if action in ["DEPOSIT", "WITHDRAW"]:
                    final_ticker = 'CASH'
                elif not final_ticker.endswith('.NS'):
                    final_ticker = f"{final_ticker}.NS"
                
                try:
                    supabase.table('transactions').insert({
                        "date": trade_date.strftime('%Y-%m-%d'),
                        "ticker": final_ticker,
                        "action": action,
                        "quantity": float(quantity),
                        "price": float(price)
                    }).execute()
                    
                    st.success(f"[✓] Successfully logged {action} for {final_ticker}!")
                    pull_vault_data.clear()
                except Exception as e:
                    st.error(f"Failed to log trade: {e}")