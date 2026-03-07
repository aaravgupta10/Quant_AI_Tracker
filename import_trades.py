import os
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client

# 1. Unlock Vault
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# 2. Read the Raw Zerodha CSV
try:
    df = pd.read_csv('tradebook_7March2026.csv')
    # Standardize headers (make lowercase, remove spaces) just in case Zerodha changed them
    df.columns = df.columns.str.strip().str.lower()
except FileNotFoundError:
    print("❌ Error: 'tradebook_7March2026.csv' not found in the folder.")
    exit()

trades_to_insert = []
print("⏳ Parsing and cleaning Zerodha tradebook...")

# 3. Data Transformation Loop
for index, row in df.iterrows():
    try:
        # Extract and clean ticker (Zerodha sometimes appends '-EQ' or '-BE')
        raw_symbol = str(row['symbol']).strip()
        clean_symbol = raw_symbol.split('-')[0] + ".NS" 
        
        # Format Date to YYYY-MM-DD
        formatted_date = pd.to_datetime(row['trade_date']).strftime('%Y-%m-%d')
        
        # Ensure Action is uppercase BUY/SELL
        action = str(row['trade_type']).strip().upper()
        
        # Extract Math
        qty = float(row['quantity'])
        price = float(row['price'])
        
        # Build the exact dictionary required by your vault
        trade = {
            "date": formatted_date,
            "ticker": clean_symbol,
            "action": action,
            "quantity": qty,
            "price": price
        }
        trades_to_insert.append(trade)
        
    except Exception as e:
        print(f"⚠️ Skipping row {index} due to formatting error: {e}")

# 4. Bulk Upload Payload
print(f"🚀 Firing {len(trades_to_insert)} historical trades into the Vault...")

if trades_to_insert:
    # Fire in batches of 100 to avoid Supabase payload limits
    batch_size = 100
    for i in range(0, len(trades_to_insert), batch_size):
        batch = trades_to_insert[i : i + batch_size]
        try:
            supabase.table('transactions').insert(batch).execute()
            print(f"[✓] Successfully inserted trades {i+1} to {min(i+batch_size, len(trades_to_insert))}...")
        except Exception as e:
            print(f"❌ Failed to insert batch {i+1}: {e}")

print("🏦 Migration Complete. Your entire history is now locked in the cloud.")