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
        if st.session_state["password"] == st.secrets.get("APP_PASSWORD", "admin"):
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
    metrics_res = supabase.table('portfolio_metrics').select('*').order('date', desc=False).execute()
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

# ==========================================
# INDESTRUCTIBLE BATCH FETCHER
# ==========================================
@st.cache_data(ttl=60)
def get_live_prices_safely(tickers):
    prices = {t: 0.0 for t in tickers}
    if not tickers:
        return prices
        
    try:
        data = yf.download(tickers, period="5d")
        if 'Close' in data:
            close_data = data['Close']
        else:
            close_data = data
            
        for t in tickers:
            try:
                if isinstance(close_data, pd.DataFrame):
                    if t in close_data.columns:
                        series = close_data[t].dropna()
                    else:
                        series = pd.Series(dtype=float)
                else:
                    series = close_data.dropna()
                    
                if not series.empty:
                    prices[t] = float(series.iloc[-1])
            except:
                continue
    except Exception:
        pass
        
    return prices

# --- CREATE 6 TABS ---
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Portfolio Curve", 
    "⚖️ Auto-Rebalancer", 
    "🎲 Monte Carlo Risk", 
    "🧪 Correlation Sandbox", 
    "📝 Trade Manager",
    "🔮 DCF Valuation"
])

# ==========================================
# TAB 1: PORTFOLIO OVERVIEW & EQUITY CURVE
# ==========================================
with tab1:
    st.subheader("Macro Risk & Performance")
    if metrics_data:
        m = metrics_data[-1] 
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Current Total NAV", f"₹{m['total_nav']:,.2f}")
        col2.metric("Portfolio Alpha", f"{m.get('alpha', 0)*100:.2f}%") 
        col3.metric("Portfolio Beta", f"{m['portfolio_beta']:.2f}")
        col4.metric("Sharpe Ratio", f"{m['portfolio_sharpe']:.2f}")
        col5.metric("Max Drawdown", f"{m['max_drawdown']*100:.2f}%")
        
        st.divider()
        st.markdown("### Historical Equity Curve")
        df_metrics = pd.DataFrame(metrics_data)
        fig_curve = px.line(df_metrics, x='date', y='total_nav', title='Net Asset Value Over Time')
        fig_curve.update_traces(line_color='#00FF00', line_width=3) 
        fig_curve.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig_curve, use_container_width=True)
    else:
        st.warning("⚠️ No metrics found. Run quant_engine.py in terminal to populate.")

    st.divider()
    col_table, col_chart = st.columns([1, 1])
    with col_table:
        st.markdown(f"**Available Cash:** ₹{cash_balance:,.2f}")
        if active_positions:
            with st.spinner("Calculating live capital weights..."):
                holdings_data = []
                total_equity = 0.0
                live_prices = get_live_prices_safely(current_tickers)

                for ticker in current_tickers:
                    price = live_prices.get(ticker, 0.0)
                    val = price * active_positions[ticker]
                    total_equity += val
                    holdings_data.append({"Ticker": ticker, "Shares": active_positions[ticker], "Value": val})

                total_port_val = total_equity  # Cash completely excluded

                for row in holdings_data:
                    weight = (row["Value"] / total_port_val * 100) if total_port_val > 0 else 0
                    row["% Weight"] = weight
                    row["Display Weight"] = f"{weight:.2f}%"

                display_df = pd.DataFrame(holdings_data)[["Ticker", "Shares", "Display Weight"]]
                display_df.rename(columns={"Display Weight": "% Weight"}, inplace=True)
                st.dataframe(display_df, hide_index=True, use_container_width=True)
        else:
            st.write("No active stock positions.")
            
    with col_chart:
        if active_positions and 'holdings_data' in locals():
            st.markdown("**Holdings Distribution (By Capital)**")
            valid_holdings = [row for row in holdings_data if row["Value"] > 0]
            if valid_holdings:
                pie_labels = [row["Ticker"] for row in valid_holdings]
                pie_values = [row["% Weight"] for row in valid_holdings]
                fig = px.pie(values=pie_values, names=pie_labels, hole=0.4)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Portfolio value is currently zero (market data unavailable).")

# ==========================================
# TAB 2: AUTOMATED REBALANCER
# ==========================================
with tab2:
    st.subheader("Automated Rebalancing Engine")
    if not active_positions:
        st.warning("You need active stock positions to use the rebalancer.")
    else:
        with st.spinner("Fetching live market prices..."):
            live_prices = get_live_prices_safely(current_tickers)
            total_stock_value = sum(live_prices.get(t, 0.0) * active_positions[t] for t in current_tickers)

            if total_stock_value <= 0:
                st.error("Cannot calculate rebalance: Live market data currently unavailable.")
            else:
                total_portfolio_value = total_stock_value  # Cash completely excluded
                st.info(f"**Live Invested Equity:** ₹{total_portfolio_value:,.2f}")
                
                st.markdown("### Set Target Weights (%)")
                targets = {}
                target_sum = 0
                cols = st.columns(min(len(current_tickers), 4) if len(current_tickers) > 0 else 1)
                
                for i, ticker in enumerate(current_tickers):
                    with cols[i % 4]:
                        current_weight = ((live_prices.get(ticker, 0.0) * active_positions[ticker]) / total_portfolio_value) * 100
                        # UI Armor to prevent StreamlitValueAboveMaxError
                        safe_default = max(0.0, min(float(round(current_weight, 1)), 100.0))
                        targets[ticker] = st.number_input(f"{ticker} (Current: {current_weight:.1f}%)", min_value=0.0, max_value=100.0, value=safe_default, step=1.0)
                        target_sum += targets[ticker]
                        
                if target_sum > 100:
                    st.error(f"Total target weight is {target_sum}%. It must not exceed 100%.")
                else:
                    if st.button("Generate Trade List"):
                        st.markdown("### Execution Plan")
                        trade_plan = []
                        for ticker in current_tickers:
                            target_val = total_portfolio_value * (targets[ticker] / 100)
                            current_val = live_prices.get(ticker, 0.0) * active_positions[ticker]
                            delta = target_val - current_val
                            
                            if live_prices.get(ticker, 0.0) > 0:
                                shares_to_trade = delta / live_prices[ticker]
                                
                                if shares_to_trade > 0.5:
                                    trade_plan.append({"Action": "BUY", "Ticker": ticker, "Shares": round(shares_to_trade), "Est. Value": f"₹{delta:,.2f}"})
                                elif shares_to_trade < -0.5:
                                    trade_plan.append({"Action": "SELL", "Ticker": ticker, "Shares": abs(round(shares_to_trade)), "Est. Value": f"₹{abs(delta):,.2f}"})
                        
                        if trade_plan:
                            st.dataframe(pd.DataFrame(trade_plan).style.applymap(lambda x: 'color: green' if x == 'BUY' else ('color: red' if x == 'SELL' else ''), subset=['Action']), hide_index=True)
                        else:
                            st.success("Your portfolio is perfectly balanced.")

# ==========================================
# TAB 3: MONTE CARLO STRESS TEST
# ==========================================
with tab3:
    st.subheader("Monte Carlo Path Simulation (1 Year)")
    if st.button("Run Simulation"):
        if not current_tickers:
            st.warning("Ensure you have active stocks to run the simulation.")
        else:
            with st.spinner("Crunching synthetic historical volatility..."):
                try:
                    data = yf.download(current_tickers, period="1y", interval="1d")['Close']
                    if len(current_tickers) == 1:
                        data = data.to_frame(name=current_tickers[0])

                    valid_data = data.dropna(axis=1, how='all')
                    valid_tickers = valid_data.columns.tolist()

                    if not valid_tickers:
                        st.error("No historical data available for any portfolio stocks.")
                    else:
                        returns = valid_data.pct_change().dropna()
                        live_prices = get_live_prices_safely(valid_tickers)
                        total_equity = sum([live_prices.get(t, 0.0) * active_positions[t] for t in valid_tickers])

                        if total_equity > 0:
                            weights = {t: (live_prices.get(t, 0.0) * active_positions[t]) / total_equity for t in valid_tickers}
                            port_returns = sum([returns[t] * weights[t] for t in valid_tickers])

                            mu = port_returns.mean()
                            sigma = port_returns.std()
                            current_nav = total_equity  # Cash completely excluded

                            days, simulations = 252, 500
                            sim_returns = np.random.normal(mu, sigma, (days, simulations))
                            price_paths = np.zeros_like(sim_returns)
                            price_paths[0] = current_nav

                            for t in range(1, days):
                                price_paths[t] = price_paths[t-1] * (1 + sim_returns[t])

                            fig_mc = go.Figure()
                            for i in range(simulations):
                                fig_mc.add_trace(go.Scatter(y=price_paths[:, i], mode='lines', line=dict(color='rgba(0,100,255,0.05)'), showlegend=False))

                            fig_mc.update_layout(title="500 Simulated Portfolio Paths", xaxis_title="Trading Days", yaxis_title="Projected Equity (₹)", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
                            st.plotly_chart(fig_mc, use_container_width=True)

                            final_navs = price_paths[-1]
                            st.markdown("### Probability Matrix (End of Year)")
                            col_w, col_m, col_b = st.columns(3)
                            col_w.error(f"**Bottom 5% (Crash):** ₹{np.percentile(final_navs, 5):,.2f}")
                            col_m.info(f"**Median Expectation:** ₹{np.median(final_navs):,.2f}")
                            col_b.success(f"**Top 5% (Bull Run):** ₹{np.percentile(final_navs, 95):,.2f}")
                        else:
                            st.error("Portfolio equity is zero, cannot run simulation.")
                except Exception as e:
                    st.error(f"Simulation failed to calculate: {e}")

# ==========================================
# TAB 4: CORRELATION SANDBOX
# ==========================================
with tab4:
    st.subheader("Pre-Trade Risk Analysis")
    st.info("💡 **Note:** If you just want to check the stats for your current portfolio, enter a ticker of any stock already in your portfolio.")
    
    target_ticker = st.text_input("Enter Target Ticker (e.g., ITC):", key="sandbox_tick").strip().upper()
    if target_ticker and not target_ticker.endswith('.NS') and target_ticker != "^NSEI":
        target_ticker = f"{target_ticker}.NS"
        
    if st.button("Run Matrix Analysis"):
        if not current_tickers or not target_ticker:
            st.warning("Please enter a ticker and ensure portfolio has stocks.")
        else:
            with st.spinner(f"Calculating capital-weighted matrix for {target_ticker}..."):
                try:
                    all_tickers = list(dict.fromkeys(current_tickers + [target_ticker, "^NSEI"]))
                    data = yf.download(all_tickers, period="1y", interval="1d")['Close']
                    
                    valid_data = data.dropna(axis=1, how='all')
                    returns = valid_data.pct_change().dropna()
                    
                    if target_ticker not in returns.columns or '^NSEI' not in returns.columns:
                        st.error(f"Insufficient market data for {target_ticker} or Nifty 50 to run correlation.")
                    else:
                        live_prices = get_live_prices_safely(current_tickers)
                        portfolio_value = sum([live_prices.get(t, 0.0) * active_positions[t] for t in current_tickers if t in returns.columns])
                        
                        if portfolio_value > 0:
                            weights = {t: (live_prices.get(t, 0.0) * active_positions[t]) / portfolio_value for t in current_tickers if t in returns.columns}
                            
                            returns['Current_Portfolio'] = 0.0
                            for t in weights.keys():
                                returns['Current_Portfolio'] += returns[t] * weights[t]
                                
                            col_macro, col_target = st.columns(2)
                            with col_macro: 
                                st.info(f"**True Portfolio vs Nifty 50:** {returns['Current_Portfolio'].corr(returns['^NSEI']):.2f}")
                            with col_target: 
                                st.info(f"**{target_ticker} vs True Portfolio:** {returns[target_ticker].corr(returns['Current_Portfolio']):.2f}")

                            analysis_tickers = list(dict.fromkeys([t for t in current_tickers if t in returns.columns] + [target_ticker]))
                            
                            if len(analysis_tickers) > 1:
                                corr_matrix = returns[analysis_tickers].corr()
                                target_corrs = corr_matrix[target_ticker].drop(target_ticker)
                                
                                corr_df = pd.DataFrame(target_corrs).reset_index()
                                corr_df.columns = ['Stock', 'Correlation']
                                st.markdown(f"**How {target_ticker} correlates with your individual holdings:**")
                                st.dataframe(corr_df.style.background_gradient(cmap='RdYlGn_r'), hide_index=True)
                            else:
                                st.success(f"You currently only own {target_ticker}. Log more trades to build a correlation matrix!")
                        else:
                            st.error("Portfolio valuation failed due to missing market data.")
                except Exception as e:
                    st.error(f"Matrix calculation failed: {e}")

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
        
        if st.form_submit_button("Secure Trade in Vault"):
            if not ticker_input: st.error("Please enter a valid ticker.")
            else:
                final_ticker = ticker_input.strip().upper()
                if action in ["DEPOSIT", "WITHDRAW"]: final_ticker = 'CASH'
                elif not final_ticker.endswith('.NS'): final_ticker = f"{final_ticker}.NS"
                
                try:
                    supabase.table('transactions').insert({
                        "date": trade_date.strftime('%Y-%m-%d'), "ticker": final_ticker, 
                        "action": action, "quantity": float(quantity), "price": float(price)
                    }).execute()
                    st.success(f"[✓] Successfully logged {action} for {final_ticker}!")
                    pull_vault_data.clear()
                except Exception as e:
                    st.error(f"Failed to log trade: {e}")

# ==========================================
# TAB 6: DCF VALUATION ENGINE
# ==========================================
with tab6:
    st.subheader("Discounted Cash Flow (DCF) Model")
    st.write("Determine the intrinsic value of a company based on projected future cash flows.")
    
    dcf_ticker = st.text_input("Enter Ticker for DCF (e.g., TCS, INFY):", key="dcf_tick").strip().upper()
    if dcf_ticker and not dcf_ticker.endswith('.NS'):
        dcf_ticker = f"{dcf_ticker}.NS"
        
    if dcf_ticker:
        with st.spinner(f"Pulling fundamental data for {dcf_ticker}..."):
            try:
                stock = yf.Ticker(dcf_ticker)
                info = stock.info
                
                current_price = info.get('currentPrice', 0)
                shares_out = info.get('sharesOutstanding', 0)
                total_cash = info.get('totalCash', 0)
                total_debt = info.get('totalDebt', 0)
                fcf_api = info.get('freeCashflow', 0)
                
                if current_price == 0 or shares_out == 0:
                    st.error("Missing essential pricing or share data from Yahoo Finance. Try another ticker.")
                else:
                    col_fund, col_assump = st.columns(2)
                    
                    with col_fund:
                        st.markdown("### 🏛️ Base Fundamentals")
                        st.info(f"**Current Price:** ₹{current_price:,.2f}")
                        st.info(f"**Shares Outstanding:** {shares_out:,.0f}")
                        st.info(f"**Total Cash:** ₹{total_cash:,.0f}")
                        st.info(f"**Total Debt:** ₹{total_debt:,.0f}")
                        
                        starting_fcf = st.number_input(
                            "Starting Free Cash Flow (₹)", 
                            value=float(fcf_api) if fcf_api else 10000000000.0,
                            help="Override this manually if Yahoo Finance data is inaccurate."
                        )
                        
                    with col_assump:
                        st.markdown("### 🎚️ Your Assumptions")
                        growth_rate = st.slider("Expected Growth Rate (Years 1-5)", 1.0, 50.0, 15.0, 0.5) / 100
                        discount_rate = st.slider("Discount Rate / WACC", 5.0, 25.0, 12.0, 0.5) / 100
                        terminal_growth = st.slider("Terminal Growth Rate (Year 6+)", 1.0, 10.0, 4.0, 0.5) / 100
                        margin_of_safety = st.slider("Margin of Safety", 0.0, 50.0, 20.0, 5.0) / 100
                        
                    if st.button("Calculate Intrinsic Value"):
                        projected_fcfs = []
                        current_fcf = starting_fcf
                        for i in range(1, 6):
                            current_fcf *= (1 + growth_rate)
                            projected_fcfs.append(current_fcf)
                            
                        pv_fcfs = sum([fcf / ((1 + discount_rate) ** i) for i, fcf in enumerate(projected_fcfs, 1)])
                        
                        terminal_value = (projected_fcfs[-1] * (1 + terminal_growth)) / (discount_rate - terminal_growth)
                        pv_terminal_value = terminal_value / ((1 + discount_rate) ** 5)
                        
                        enterprise_value = pv_fcfs + pv_terminal_value
                        equity_value = enterprise_value + total_cash - total_debt
                        
                        intrinsic_value = equity_value / shares_out
                        target_buy_price = intrinsic_value * (1 - margin_of_safety)
                        
                        st.divider()
                        st.markdown("### 📊 Valuation Output")
                        
                        res_col1, res_col2, res_col3 = st.columns(3)
                        res_col1.metric("Current Market Price", f"₹{current_price:,.2f}")
                        res_col2.metric("Calculated Intrinsic Value", f"₹{intrinsic_value:,.2f}", 
                                        delta=f"{((intrinsic_value - current_price) / current_price) * 100:.2f}% (Upside)", 
                                        delta_color="normal" if intrinsic_value > current_price else "inverse")
                        res_col3.metric(f"Target Buy Price ({int(margin_of_safety*100)}% MoS)", f"₹{target_buy_price:,.2f}")
                        
                        if current_price < target_buy_price:
                            st.success(f"🔥 UNDERVALUED: The stock is currently trading below your required Target Buy Price of ₹{target_buy_price:,.2f}.")
                        else:
                            st.error(f"🛑 OVERVALUED: The stock is currently too expensive compared to your Target Buy Price.")

            except Exception as e:
                st.error(f"Failed to fetch data or calculate DCF: {e}. Try adjusting the ticker.")