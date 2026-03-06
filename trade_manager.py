import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

# Unlock the vault
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def log_trade(ticker, action, quantity, price, trade_date):
    action = action.upper()
    ticker = ticker.upper()
    
    # Ensure Yahoo Finance formatting for Indian stocks if not cash
    if ticker != 'CASH' and not ticker.endswith('.NS'):
        ticker = f"{ticker}.NS"
        
    print(f"\nSending to vault: {action} {quantity} of {ticker} at ₹{price} on {trade_date}...")
    
    try:
        response = supabase.table('transactions').insert({
            "date": trade_date,
            "ticker": ticker,
            "action": action,
            "quantity": float(quantity),
            "price": float(price)
        }).execute()
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
            
        # Confirm and log
        log_trade(ticker, action, quantity, price, trade_date)
        
        # Ask if there are more trades
        another = input("\nLog another trade? (y/n): ").strip().lower()
        if another != 'y':
            print("Closing Trade Manager. Run quant_engine.py to update your metrics!")
            break