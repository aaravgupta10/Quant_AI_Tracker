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

    Includes:
    - current positions
    - open position cost basis (average-cost method)
    - legacy equity net invested
    - cash/external flow tracking (kept for compatibility)
    """
    if df_tx is None or df_tx.empty:
        return {
            "positions": {},
            "active_positions": {},
            "position_cost_basis": {},
            "open_cost_basis_total": 0.0,
            "cash_balance": 0.0,
            "net_external_invested": 0.0,
            "legacy_equity_net_invested": 0.0,
        }

    df = normalize_ledger_dates(df_tx)
    positions = {}
    position_cost_basis = {}
    strategy_tags = {}
    cash_balance = 0.0
    net_external_invested = 0.0
    legacy_equity_net_invested = 0.0

    for _, row in df.iterrows():
        ticker = str(row.get("ticker", "")).upper().strip()
        action = str(row.get("action", "")).upper().strip()
        qty = abs(float(row.get("quantity") or 0.0))
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

        cur_qty = positions.get(ticker, 0.0)
        cur_cost = position_cost_basis.get(ticker, 0.0)

        if action == "BUY":
            positions[ticker] = cur_qty + qty
            position_cost_basis[ticker] = cur_cost + trade_val
            
            stag = str(row.get("strategy_tag") or "").strip()
            if stag and stag != "nan":
                strategy_tags[ticker] = stag
            elif ticker not in strategy_tags:
                strategy_tags[ticker] = "Core"
                
            cash_balance -= trade_val
            legacy_equity_net_invested += trade_val

        elif action == "SELL":
            sell_qty = min(qty, max(cur_qty, 0.0))
            avg_cost = (cur_cost / cur_qty) if cur_qty > 0 else 0.0
            reduced_cost = avg_cost * sell_qty

            new_qty = cur_qty - sell_qty
            new_cost = max(cur_cost - reduced_cost, 0.0)

            if new_qty <= 1e-9:
                positions[ticker] = 0.0
                position_cost_basis[ticker] = 0.0
            else:
                positions[ticker] = new_qty
                position_cost_basis[ticker] = new_cost

            cash_balance += trade_val
            legacy_equity_net_invested -= trade_val

    active_positions = {t: q for t, q in positions.items() if q > 0}
    active_cost_basis = {
        t: float(position_cost_basis.get(t, 0.0))
        for t, q in active_positions.items()
        if q > 0
    }
    active_strategies = {
        t: strategy_tags.get(t, "Core")
        for t in active_positions
    }

    return {
        "positions": positions,
        "active_positions": active_positions,
        "position_cost_basis": active_cost_basis,
        "active_strategies": active_strategies,
        "open_cost_basis_total": float(sum(active_cost_basis.values())),
        "cash_balance": cash_balance,
        "net_external_invested": net_external_invested,
        "legacy_equity_net_invested": legacy_equity_net_invested,
    }

