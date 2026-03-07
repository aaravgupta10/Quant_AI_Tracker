import pandas as pd


def normalize_ledger_dates(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    out = out.dropna(subset=["date"])

    sort_cols = ["date"]
    for c in ("created_at", "id"):
        if c in out.columns:
            sort_cols.append(c)
    return out.sort_values(sort_cols).reset_index(drop=True)


def build_portfolio_state(df_tx: pd.DataFrame) -> dict:
    """
    Build a consistent portfolio state from transactions.
    - Stock BUY/SELL moves between cash and equity.
    - CASH DEPOSIT/WITHDRAW (or BUY/SELL on CASH) are external flows.
    """
    if df_tx is None or df_tx.empty:
        return {
            "positions": {},
            "active_positions": {},
            "cash_balance": 0.0,
            "net_external_invested": 0.0,
            "legacy_equity_net_invested": 0.0,
        }

    df = normalize_ledger_dates(df_tx)
    positions = {}
    cash_balance = 0.0
    net_external_invested = 0.0
    legacy_equity_net_invested = 0.0

    for _, row in df.iterrows():
        ticker = str(row.get("ticker", "")).upper().strip()
        action = str(row.get("action", "")).upper().strip()
        qty = float(row.get("quantity") or 0.0)
        price = float(row.get("price") or 0.0)
        trade_val = qty * price

        if ticker == "CASH":
            if action in ("DEPOSIT", "BUY"):
                cash_balance += trade_val
                net_external_invested += trade_val
            elif action in ("WITHDRAW", "SELL"):
                cash_balance -= trade_val
                net_external_invested -= trade_val
            continue

        if action == "BUY":
            positions[ticker] = positions.get(ticker, 0.0) + qty
            cash_balance -= trade_val
            legacy_equity_net_invested += trade_val
        elif action == "SELL":
            positions[ticker] = positions.get(ticker, 0.0) - qty
            cash_balance += trade_val
            legacy_equity_net_invested -= trade_val

    active_positions = {t: q for t, q in positions.items() if q > 0}
    return {
        "positions": positions,
        "active_positions": active_positions,
        "cash_balance": cash_balance,
        "net_external_invested": net_external_invested,
        "legacy_equity_net_invested": legacy_equity_net_invested,
    }
