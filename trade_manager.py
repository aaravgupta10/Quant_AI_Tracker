import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

# Unlock the vault
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def log_trade(ticker, action, quantity, price, trade_date, conviction=3, strategy="Core"):
    action = action.upper()
    ticker = ticker.upper()
    
    # Ensure Yahoo Finance formatting for Indian stocks if not cash
    if ticker != 'CASH' and not ticker.endswith('.NS'):
        ticker = f"{ticker}.NS"
        
    print(f"\nSending to vault: {action} {quantity} of {ticker} at ₹{price} on {trade_date} (Strategy: {strategy}, Conviction: {conviction})...")
    
    try:
        payload = {
            "date": trade_date,
            "ticker": ticker,
            "action": action,
            "quantity": float(quantity),
            "price": float(price)
        }
        if ticker != 'CASH' and action in ('BUY', 'SELL'):
            payload["conviction_score"] = int(conviction)
            payload["strategy_tag"] = str(strategy)
            
        response = supabase.table('transactions').insert(payload).execute()
        print("[✓] Trade successfully secured in the ledger!")
    except Exception as e:
        print(f"[X] Failed to log trade: {e}")

if __name__ == "__main__":
    print("\n=== PORTFOLIO TRADE MANAGER ===")
    print("Type 'exit' at any prompt to cancel.\n")
    
    while True:
        action = input("Action (BUY / SELL / DEPOSIT / WITHDRAW): ").strip().upper()
        if action == 'EXIT': break
        if action not in ['BUY', 'SELL', 'DEPOSIT', 'WITHDRAW']:
            print("Invalid action. Try again.")
            continue
            
        ticker = input("Ticker (e.g., ZOMATO or CASH): ").strip().upper()
        if ticker == 'EXIT': break
        
        try:
            quantity = input("Quantity (Number of shares or '1' for cash): ").strip()
            if quantity.upper() == 'EXIT': break
            quantity = float(quantity)
            
            price = input("Price (Per share, or total cash amount): ₹").strip()
            if price.upper() == 'EXIT': break
            price = float(price)
            
        except ValueError:
            print("Quantity and Price must be numbers. Let's start over.\n")
            continue
            
        trade_date = input("Date (YYYY-MM-DD) [Press Enter for Today]: ").strip()
        if trade_date.upper() == 'EXIT': break
        if not trade_date:
            trade_date = datetime.now().strftime('%Y-%m-%d')
        else:
            try:
                datetime.strptime(trade_date, '%Y-%m-%d')
            except ValueError:
                print("Invalid date format. Must be YYYY-MM-DD. Let's start over.\n")
                continue
        
        # Thesis Tracking
        conv_val = 3
        strat_val = "Core"
        if ticker != 'CASH':
            c_input = input("Conviction Score (1-5) [Press Enter for 3]: ").strip()
            try:
                conv_val = int(c_input) if c_input else 3
            except ValueError:
                conv_val = 3
            
            s_input = input("Strategy Tag (e.g., Value, Momentum, Hedge) [Press Enter for Core]: ").strip()
            strat_val = s_input if s_input else "Core"
            
        # Confirm and log
        log_trade(ticker, action, quantity, price, trade_date, conv_val, strat_val)
        
        # Ask if there are more trades
        another = input("\nLog another trade? (y/n): ").strip().lower()
        if another != 'y':
            print("Closing Trade Manager. Run quant_engine.py to update your metrics!")
            break