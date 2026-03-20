import argparse
import io
import os
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import yfinance as yf

def _get_retry_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "market_intelligence"
REPORT_DIR = ROOT / "reports"
META_PATH = DATA_DIR / "nifty500_metadata.csv"
MASTER_PATH = DATA_DIR / "daily_snapshots.csv"

DEFAULT_RECIPIENT = os.environ.get("GMAIL_RECIPIENT_EMAIL")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def get_nifty500_constituents() -> pd.DataFrame:
    url = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    session = _get_retry_session()
    response = session.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    df = pd.read_csv(io.StringIO(response.text))
    df.columns = [c.strip() for c in df.columns]

    symbol_col = "Symbol" if "Symbol" in df.columns else df.columns[0]
    df["symbol"] = df[symbol_col].astype(str).str.strip().str.upper()
    df["ticker"] = df["symbol"] + ".NS"

    df["industry_constituent"] = df["Industry"].astype(str).str.strip() if "Industry" in df.columns else "Unknown"
    df["company_name"] = df["Company Name"].astype(str).str.strip() if "Company Name" in df.columns else df["symbol"]

    return df[["symbol", "ticker", "company_name", "industry_constituent"]]


def load_metadata_cache() -> pd.DataFrame:
    if META_PATH.exists():
        return pd.read_csv(META_PATH)
    return pd.DataFrame(columns=["ticker", "market_cap", "sector", "industry", "meta_updated_at"])


def fetch_metadata_for_ticker(ticker: str) -> dict:
    market_cap = np.nan
    sector = "Unknown"
    industry = "Unknown"
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        market_cap = info.get("marketCap") or np.nan
        sector = info.get("sector") or "Unknown"
        industry = info.get("industry") or "Unknown"
    except Exception:
        pass

    return {
        "ticker": ticker,
        "market_cap": float(market_cap) if pd.notna(market_cap) else np.nan,
        "sector": str(sector),
        "industry": str(industry),
        "meta_updated_at": datetime.now().strftime("%Y-%m-%d"),
    }


def update_metadata_cache(tickers: list[str], refresh_days: int = 7) -> pd.DataFrame:
    cache = load_metadata_cache()
    if cache.empty:
        cache = pd.DataFrame(columns=["ticker", "market_cap", "sector", "industry", "meta_updated_at"])

    cache["meta_updated_at"] = pd.to_datetime(cache["meta_updated_at"], errors="coerce")
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=refresh_days)

    stale_or_missing = []
    cache_map = {r["ticker"]: r for _, r in cache.iterrows()} if not cache.empty else {}
    for t in tickers:
        row = cache_map.get(t)
        if row is None:
            stale_or_missing.append(t)
            continue
        updated = row.get("meta_updated_at")
        if pd.isna(updated) or updated < cutoff:
            stale_or_missing.append(t)

    updates = []
    for i, t in enumerate(stale_or_missing, start=1):
        updates.append(fetch_metadata_for_ticker(t))
        if i % 10 == 0:
            time.sleep(0.5)

    if updates:
        upd_df = pd.DataFrame(updates)
        cache = cache.drop(columns=["meta_updated_at"], errors="ignore")
        cache = cache.drop_duplicates(subset=["ticker"], keep="last")
        cache = cache[~cache["ticker"].isin(upd_df["ticker"])]
        cache = pd.concat([cache, upd_df], ignore_index=True)

    cache["meta_updated_at"] = pd.to_datetime(cache["meta_updated_at"], errors="coerce").dt.strftime("%Y-%m-%d")
    cache.to_csv(META_PATH, index=False)
    return cache


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def fetch_ohlcv_and_indicators(tickers: list[str], lookback: str = "3mo") -> pd.DataFrame:
    rows: list[dict] = []

    for batch in chunked(tickers, 40):
        data = yf.download(
            tickers=batch,
            period=lookback,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="ticker",
        )

        for ticker in batch:
            try:
                tdf = data.copy() if len(batch) == 1 else data[ticker].copy()
                tdf = tdf.dropna(subset=["Close"])
                if tdf.shape[0] < 2:
                    continue

                tdf = tdf.sort_index()
                latest = tdf.iloc[-1]
                prev = tdf.iloc[-2]

                close_price = float(latest["Close"])
                prev_close = float(prev["Close"])
                pct_change = ((close_price / prev_close) - 1.0) * 100.0 if prev_close > 0 else np.nan

                volume_series = tdf["Volume"].astype(float)
                avg_volume_20 = float(volume_series.tail(20).mean()) if not volume_series.empty else np.nan
                unusual_volume_ratio = float(latest["Volume"] / avg_volume_20) if avg_volume_20 and avg_volume_20 > 0 else np.nan

                ret_series = tdf["Close"].astype(float).pct_change()
                vol20 = float(ret_series.tail(20).std() * 100.0) if ret_series.notna().sum() >= 5 else np.nan
                move_vs_vol = float(abs(pct_change) / vol20) if pd.notna(vol20) and vol20 > 0 else np.nan

                rows.append(
                    {
                        "date": pd.to_datetime(tdf.index[-1]).strftime("%Y-%m-%d"),
                        "ticker": ticker,
                        "open": float(latest["Open"]),
                        "high": float(latest["High"]),
                        "low": float(latest["Low"]),
                        "close": close_price,
                        "prev_close": prev_close,
                        "pct_change": pct_change,
                        "volume": float(latest["Volume"]),
                        "avg_volume_20": avg_volume_20,
                        "unusual_volume_ratio": unusual_volume_ratio,
                        "volatility_20d_pct": vol20,
                        "move_vs_vol": move_vs_vol,
                    }
                )
            except Exception:
                continue

    return pd.DataFrame(rows)


def merge_dataset(constituents: pd.DataFrame, metadata: pd.DataFrame, ohlcv: pd.DataFrame) -> pd.DataFrame:
    df = constituents.merge(metadata, on="ticker", how="left")
    df = df.merge(ohlcv, on="ticker", how="left")

    df["industry"] = df["industry"].fillna(df["industry_constituent"])
    df["sector"] = df["sector"].fillna("Unknown")

    date_val = df["date"].dropna().iloc[0] if not df["date"].dropna().empty else datetime.now().strftime("%Y-%m-%d")
    df["date"] = str(date_val)
    return df


def load_master() -> pd.DataFrame:
    if MASTER_PATH.exists():
        return pd.read_csv(MASTER_PATH)
    return pd.DataFrame()


def update_master(today_df: pd.DataFrame) -> pd.DataFrame:
    master = load_master()
    combined = pd.concat([master, today_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
    combined.to_csv(MASTER_PATH, index=False)
    return combined


def _fmt_num(x: float, kind: str) -> str:
    if pd.isna(x):
        return ""
    if kind == "pct":
        return f"{x:.2f}%"
    if kind == "ratio":
        return f"{x:.2f}"
    if kind == "int":
        return f"{x:,.0f}"
    return f"{x:,.2f}"


def _markdown_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "_No data available._"

    table = df[cols].copy()
    for c in table.columns:
        if pd.api.types.is_numeric_dtype(table[c]):
            if c in {"pct_change", "volatility_20d_pct"}:
                table[c] = table[c].map(lambda x: _fmt_num(x, "pct"))
            elif c in {"unusual_volume_ratio", "move_vs_vol", "put_call_ratio"}:
                table[c] = table[c].map(lambda x: _fmt_num(x, "ratio"))
            elif c in {"market_cap", "volume", "avg_volume_20", "long_value", "short_value", "net_value", "buy_value", "sell_value", "net_flow"}:
                table[c] = table[c].map(lambda x: _fmt_num(x, "int"))
            else:
                table[c] = table[c].map(lambda x: _fmt_num(x, "float"))

    header = "| " + " | ".join(table.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(table.columns)) + " |"
    rows = ["| " + " | ".join(map(str, r)) + " |" for r in table.values.tolist()]
    return "\n".join([header, sep] + rows)


def _html_table(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "<p><i>No data available.</i></p>"

    table = df[cols].copy()
    for c in table.columns:
        if pd.api.types.is_numeric_dtype(table[c]):
            if c in {"pct_change", "volatility_20d_pct"}:
                table[c] = table[c].map(lambda x: _fmt_num(x, "pct"))
            elif c in {"unusual_volume_ratio", "move_vs_vol", "put_call_ratio"}:
                table[c] = table[c].map(lambda x: _fmt_num(x, "ratio"))
            elif c in {"market_cap", "volume", "avg_volume_20", "long_value", "short_value", "net_value", "buy_value", "sell_value", "net_flow"}:
                table[c] = table[c].map(lambda x: _fmt_num(x, "int"))
            else:
                table[c] = table[c].map(lambda x: _fmt_num(x, "float"))

    return table.to_html(index=False, border=0, classes="report-table", justify="left")


def _nse_session() -> requests.Session:
    session = _get_retry_session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    session.headers.update(headers)
    try:
        session.get("https://www.nseindia.com", timeout=20)
    except Exception:
        pass
    return session


def _fetch_participant_csv(session: requests.Session, kind: str, report_date: str) -> pd.DataFrame:
    # kind: 'oi' or 'vol'
    ddmmyyyy = pd.to_datetime(report_date).strftime("%d%m%Y")
    candidates = [
        f"https://archives.nseindia.com/content/nsccl/fao_participant_{kind}_{ddmmyyyy}.csv",
        f"https://www.nseindia.com/content/nsccl/fao_participant_{kind}_{ddmmyyyy}.csv",
    ]

    for url in candidates:
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200 and r.text and "," in r.text:
                df = pd.read_csv(io.StringIO(r.text))
                if not df.empty:
                    df.columns = [str(c).strip() for c in df.columns]
                    return df
        except Exception:
            continue
    return pd.DataFrame()


def _normalize_participant(df: pd.DataFrame, kind: str, report_date: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["date", "client_type", "long_value", "short_value", "net_value", "buy_value", "sell_value", "net_flow", "source_kind"])

    client_col = "Client Type" if "Client Type" in df.columns else (df.columns[0] if len(df.columns) else "client")
    work = df.copy()
    work[client_col] = work[client_col].astype(str).str.strip().str.upper()
    work = work[work[client_col].isin(["FII", "DII"])].copy()

    if work.empty:
        return pd.DataFrame(columns=["date", "client_type", "long_value", "short_value", "net_value", "buy_value", "sell_value", "net_flow", "source_kind"])

    numeric_cols = [c for c in work.columns if c != client_col]
    for c in numeric_cols:
        work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0.0)

    long_cols = [c for c in numeric_cols if "LONG" in c.upper()]
    short_cols = [c for c in numeric_cols if "SHORT" in c.upper()]
    buy_cols = [c for c in numeric_cols if "BUY" in c.upper()]
    sell_cols = [c for c in numeric_cols if "SELL" in c.upper()]

    out = pd.DataFrame()
    out["date"] = [report_date] * work.shape[0]
    out["client_type"] = work[client_col].values
    out["source_kind"] = [kind] * work.shape[0]

    out["long_value"] = work[long_cols].sum(axis=1) if long_cols else 0.0
    out["short_value"] = work[short_cols].sum(axis=1) if short_cols else 0.0
    out["net_value"] = out["long_value"] - out["short_value"]

    out["buy_value"] = work[buy_cols].sum(axis=1) if buy_cols else 0.0
    out["sell_value"] = work[sell_cols].sum(axis=1) if sell_cols else 0.0
    out["net_flow"] = out["buy_value"] - out["sell_value"]

    return out


def fetch_derivatives_participant_data(report_date: str) -> dict:
    session = _nse_session()

    oi_raw = _fetch_participant_csv(session, "oi", report_date)
    vol_raw = _fetch_participant_csv(session, "vol", report_date)

    oi_df = _normalize_participant(oi_raw, "open_interest", report_date)
    vol_df = _normalize_participant(vol_raw, "volume", report_date)

    return {
        "oi": oi_df,
        "volume": vol_df,
        "available": (not oi_df.empty) or (not vol_df.empty),
    }


def fetch_daily_pcr(report_date: str) -> pd.DataFrame:
    session = _nse_session()
    rows = []

    for symbol in ["NIFTY", "BANKNIFTY"]:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        try:
            r = session.get(url, timeout=30)
            if r.status_code != 200:
                continue
            data = r.json() if r.text else {}

            records = data.get("records", {})
            ce_oi = float(records.get("totalCEOpenInterest", 0.0) or 0.0)
            pe_oi = float(records.get("totalPEOpenInterest", 0.0) or 0.0)
            pcr = (pe_oi / ce_oi) if ce_oi > 0 else np.nan

            rows.append(
                {
                    "date": report_date,
                    "symbol": symbol,
                    "put_open_interest": pe_oi,
                    "call_open_interest": ce_oi,
                    "put_call_ratio": pcr,
                }
            )
        except Exception:
            continue

    return pd.DataFrame(rows)


def compute_sector_rotation(today_sector: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    if master.empty:
        out = today_sector.copy()
        out["prior_5d_avg_return"] = np.nan
        out["momentum_delta"] = np.nan
        return out

    hist = master.dropna(subset=["pct_change", "sector"])
    hist_grouped = (
        hist.groupby(["date", "sector"], as_index=False)["pct_change"]
        .mean()
        .rename(columns={"pct_change": "sector_return"})
    )
    hist_grouped["date"] = pd.to_datetime(hist_grouped["date"], errors="coerce")

    today_date = pd.to_datetime(today_sector["date"].iloc[0])
    prior = hist_grouped[hist_grouped["date"] < today_date]

    prior_5 = (
        prior.sort_values(["sector", "date"])
        .groupby("sector", as_index=False)
        .tail(5)
        .groupby("sector", as_index=False)["sector_return"]
        .mean()
        .rename(columns={"sector_return": "prior_5d_avg_return"})
    )

    out = today_sector.merge(prior_5, on="sector", how="left")
    out["momentum_delta"] = out["pct_change"] - out["prior_5d_avg_return"]
    return out


def build_key_observations(df: pd.DataFrame, sector_rank: pd.DataFrame, pcr_df: pd.DataFrame, deriv_oi: pd.DataFrame) -> list[str]:
    obs: list[str] = []

    adv = int((df["pct_change"] > 0).sum())
    dec = int((df["pct_change"] < 0).sum())
    avg_ret = float(df["pct_change"].mean()) if not df.empty else 0.0
    adv_dec_ratio = (adv / dec) if dec > 0 else float("inf")
    obs.append(f"Breadth closed at {adv}:{dec} (A/D {adv_dec_ratio:.2f}) with average stock return {avg_ret:.2f}%.")

    if not sector_rank.empty:
        best = sector_rank.iloc[0]
        worst = sector_rank.iloc[-1]
        obs.append(f"Leadership came from {best['sector']} ({best['pct_change']:.2f}%), while {worst['sector']} lagged ({worst['pct_change']:.2f}%).")

    uv = df[df["unusual_volume_ratio"] >= 2.0]
    if not uv.empty:
        obs.append(f"{len(uv)} stocks traded above 2x 20-day average volume, signaling concentrated participation.")

    if not pcr_df.empty:
        nifty = pcr_df[pcr_df["symbol"] == "NIFTY"]
        if not nifty.empty and pd.notna(nifty.iloc[0]["put_call_ratio"]):
            obs.append(f"NIFTY PCR closed at {nifty.iloc[0]['put_call_ratio']:.2f}, indicating derivatives sentiment balance.")

    if not deriv_oi.empty:
        fii = deriv_oi[deriv_oi["client_type"] == "FII"]
        if not fii.empty:
            net = float(fii.iloc[0]["net_value"])
            side = "net long" if net > 0 else "net short"
            obs.append(f"FII derivatives positioning was {side} ({abs(net):,.0f} contracts equivalent).")

    return obs[:5]


def build_report_components(today_df: pd.DataFrame, master: pd.DataFrame, deriv_data: dict, pcr_df: pd.DataFrame) -> dict:
    valid = today_df.dropna(subset=["pct_change", "close"]).copy()
    if valid.empty:
        raise RuntimeError("No valid EOD data found to build report.")

    report_date = valid["date"].iloc[0]
    advancers = int((valid["pct_change"] > 0).sum())
    decliners = int((valid["pct_change"] < 0).sum())
    total = int(valid.shape[0])
    avg_return = float(valid["pct_change"].mean())
    breadth_ratio = (advancers / decliners) if decliners > 0 else float("inf")
    pct_above_prev_close = (advancers / total * 100.0) if total > 0 else 0.0

    sentiment = "Neutral"
    if avg_return > 0.25 and breadth_ratio > 1.2:
        sentiment = "Bullish"
    elif avg_return < -0.25 and breadth_ratio < 0.8:
        sentiment = "Risk-Off"

    gainers = valid.nlargest(10, "pct_change")
    losers = valid.nsmallest(10, "pct_change")

    sector_rank = (
        valid.groupby("sector", as_index=False)
        .agg(pct_change=("pct_change", "mean"), stocks=("ticker", "count"))
        .sort_values("pct_change", ascending=False)
    )
    sector_rank["date"] = report_date
    rotation = compute_sector_rotation(sector_rank[["date", "sector", "pct_change", "stocks"]], master)

    unusual_volume = valid[valid["unusual_volume_ratio"] >= 2.0].sort_values("unusual_volume_ratio", ascending=False).head(15)
    vol_adjusted_moves = valid.dropna(subset=["move_vs_vol"]).sort_values("move_vs_vol", ascending=False).head(15)

    deriv_oi = deriv_data.get("oi", pd.DataFrame())
    deriv_vol = deriv_data.get("volume", pd.DataFrame())

    observations = build_key_observations(valid, sector_rank, pcr_df, deriv_oi)

    summary = {
        "date": report_date,
        "sentiment": sentiment,
        "advancers": advancers,
        "decliners": decliners,
        "advance_decline_ratio": breadth_ratio,
        "average_return": avg_return,
        "pct_above_prev_close": pct_above_prev_close,
    }

    return {
        "summary": summary,
        "gainers": gainers,
        "losers": losers,
        "sector_rank": sector_rank,
        "rotation": rotation,
        "unusual_volume": unusual_volume,
        "vol_adjusted_moves": vol_adjusted_moves,
        "observations": observations,
        "deriv_oi": deriv_oi,
        "deriv_volume": deriv_vol,
        "pcr": pcr_df,
    }


def generate_markdown_report(parts: dict) -> str:
    s = parts["summary"]
    sector_rank = parts["sector_rank"]

    report = []
    report.append(f"# Nifty 500 Daily Market Report ({s['date']})")
    report.append("")
    report.append("## Market Summary")
    report.append(f"- Overall sentiment: **{s['sentiment']}**")
    report.append(f"- Advance / Decline: **{s['advancers']} / {s['decliners']}** (A/D ratio: **{s['advance_decline_ratio']:.2f}**)")
    report.append(f"- Average return across Nifty 500: **{s['average_return']:.2f}%**")
    report.append(f"- % stocks above previous close: **{s['pct_above_prev_close']:.2f}%**")
    report.append("")

    report.append("## Top Movers")
    report.append("### Top 10 Gainers")
    report.append(_markdown_table(parts["gainers"], ["ticker", "sector", "close", "pct_change", "volume"]))
    report.append("")
    report.append("### Top 10 Losers")
    report.append(_markdown_table(parts["losers"], ["ticker", "sector", "close", "pct_change", "volume"]))
    report.append("")

    report.append("## Sector Performance")
    report.append(_markdown_table(sector_rank, ["sector", "pct_change", "stocks"]))
    if not sector_rank.empty:
        report.append("")
        report.append(f"- Best performing sector: **{sector_rank.iloc[0]['sector']} ({sector_rank.iloc[0]['pct_change']:.2f}%)**")
        report.append(f"- Worst performing sector: **{sector_rank.iloc[-1]['sector']} ({sector_rank.iloc[-1]['pct_change']:.2f}%)**")
    report.append("")

    report.append("## Sector Rotation")
    report.append(_markdown_table(parts["rotation"].sort_values("momentum_delta", ascending=False), ["sector", "pct_change", "prior_5d_avg_return", "momentum_delta", "stocks"]))
    report.append("")

    report.append("## Derivatives Positioning")
    report.append("### FII/DII Open Interest (Derivatives Segment)")
    report.append(_markdown_table(parts["deriv_oi"], ["client_type", "long_value", "short_value", "net_value"]))
    report.append("")
    report.append("### FII/DII Trading Volume (Derivatives Segment)")
    report.append(_markdown_table(parts["deriv_volume"], ["client_type", "buy_value", "sell_value", "net_flow"]))
    report.append("")
    report.append("### Daily Put-Call Ratio")
    report.append(_markdown_table(parts["pcr"], ["symbol", "put_open_interest", "call_open_interest", "put_call_ratio"]))
    report.append("")

    report.append("## Notable Activity")
    report.append("### Unusual Volume")
    report.append(_markdown_table(parts["unusual_volume"], ["ticker", "sector", "pct_change", "volume", "avg_volume_20", "unusual_volume_ratio"]))
    report.append("")
    report.append("### Largest Moves vs Historical Volatility")
    report.append(_markdown_table(parts["vol_adjusted_moves"], ["ticker", "sector", "pct_change", "volatility_20d_pct", "move_vs_vol"]))
    report.append("")

    report.append("## Key Observations")
    for i, line in enumerate(parts["observations"], start=1):
        report.append(f"{i}. {line}")

    return "\n".join(report)


def generate_html_report(parts: dict) -> str:
    s = parts["summary"]
    sector_rank = parts["sector_rank"]

    best_sector = "N/A"
    worst_sector = "N/A"
    if not sector_rank.empty:
        best_sector = f"{sector_rank.iloc[0]['sector']} ({sector_rank.iloc[0]['pct_change']:.2f}%)"
        worst_sector = f"{sector_rank.iloc[-1]['sector']} ({sector_rank.iloc[-1]['pct_change']:.2f}%)"

    observations_html = "".join([f"<li>{o}</li>" for o in parts["observations"]])

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <style>
    body {{ font-family: Arial, sans-serif; color: #1f2937; line-height: 1.5; }}
    .container {{ max-width: 980px; margin: 0 auto; padding: 16px; }}
    h1, h2, h3 {{ color: #0f172a; }}
    .card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; margin-bottom: 12px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(2, minmax(220px, 1fr)); gap: 8px; }}
    .metric {{ background: #ffffff; border: 1px solid #e5e7eb; border-radius: 6px; padding: 8px; }}
    table.report-table {{ border-collapse: collapse; width: 100%; margin: 8px 0 14px 0; font-size: 13px; }}
    table.report-table th, table.report-table td {{ border: 1px solid #d1d5db; padding: 6px 8px; text-align: left; }}
    table.report-table th {{ background: #f3f4f6; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Nifty 500 Daily Market Report ({s['date']})</h1>

    <div class="card">
      <h2>Market Summary</h2>
      <div class="metrics">
        <div class="metric"><b>Sentiment:</b> {s['sentiment']}</div>
        <div class="metric"><b>Advance/Decline:</b> {s['advancers']} / {s['decliners']} (A/D {s['advance_decline_ratio']:.2f})</div>
        <div class="metric"><b>Average Return:</b> {s['average_return']:.2f}%</div>
        <div class="metric"><b>% Above Previous Close:</b> {s['pct_above_prev_close']:.2f}%</div>
      </div>
    </div>

    <h2>Top Movers</h2>
    <h3>Top 10 Gainers</h3>
    {_html_table(parts['gainers'], ['ticker', 'sector', 'close', 'pct_change', 'volume'])}

    <h3>Top 10 Losers</h3>
    {_html_table(parts['losers'], ['ticker', 'sector', 'close', 'pct_change', 'volume'])}

    <h2>Sector Performance</h2>
    {_html_table(sector_rank, ['sector', 'pct_change', 'stocks'])}
    <p><b>Best sector:</b> {best_sector}<br/><b>Worst sector:</b> {worst_sector}</p>

    <h3>Sector Rotation (vs Prior 5-Day Avg)</h3>
    {_html_table(parts['rotation'].sort_values('momentum_delta', ascending=False), ['sector', 'pct_change', 'prior_5d_avg_return', 'momentum_delta', 'stocks'])}

    <h2>Derivatives Positioning</h2>
    <h3>FII/DII Open Interest (Derivatives Segment)</h3>
    {_html_table(parts['deriv_oi'], ['client_type', 'long_value', 'short_value', 'net_value'])}

    <h3>FII/DII Trading Volume (Derivatives Segment)</h3>
    {_html_table(parts['deriv_volume'], ['client_type', 'buy_value', 'sell_value', 'net_flow'])}

    <h3>Daily Put-Call Ratio</h3>
    {_html_table(parts['pcr'], ['symbol', 'put_open_interest', 'call_open_interest', 'put_call_ratio'])}

    <h2>Notable Activity</h2>
    <h3>Unusual Volume (>=2x 20-day average)</h3>
    {_html_table(parts['unusual_volume'], ['ticker', 'sector', 'pct_change', 'volume', 'avg_volume_20', 'unusual_volume_ratio'])}

    <h3>Largest Moves vs Historical Volatility</h3>
    {_html_table(parts['vol_adjusted_moves'], ['ticker', 'sector', 'pct_change', 'volatility_20d_pct', 'move_vs_vol'])}

    <h2>Key Observations</h2>
    <ol>{observations_html}</ol>
  </div>
</body>
</html>
"""
    return html


def parse_recipients(raw: str | None) -> list[str]:
    if not raw:
        return [DEFAULT_RECIPIENT] if DEFAULT_RECIPIENT else []
    return [x.strip() for x in raw.split(",") if x.strip()]


def send_gmail_report(subject: str, html_body: str, recipients: list[str]) -> None:
    sender = os.getenv("GMAIL_SENDER_EMAIL")
    app_password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not sender or not app_password:
        raise RuntimeError("GMAIL_SENDER_EMAIL or GMAIL_APP_PASSWORD is not set. Cannot send email.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, recipients, msg.as_string())


def run_pipeline(as_of: str | None = None, recipients_raw: str | None = None, send_email: bool = True) -> dict:
    ensure_dirs()

    constituents = get_nifty500_constituents()
    tickers = constituents["ticker"].tolist()

    metadata = update_metadata_cache(tickers)
    ohlcv = fetch_ohlcv_and_indicators(tickers)
    if as_of is not None:
        ohlcv = ohlcv[ohlcv["date"] == as_of]

    today_df = merge_dataset(constituents, metadata, ohlcv)
    master = update_master(today_df)

    report_date = as_of if as_of else (today_df["date"].dropna().iloc[0] if not today_df.empty else datetime.now().strftime("%Y-%m-%d"))
    derivatives = fetch_derivatives_participant_data(report_date)
    pcr_df = fetch_daily_pcr(report_date)

    parts = build_report_components(today_df, master, derivatives, pcr_df)
    report_md = generate_markdown_report(parts)
    report_html = generate_html_report(parts)

    report_date = parts["summary"]["date"]
    report_md_path = REPORT_DIR / f"nifty500_market_report_{report_date}.md"
    report_html_path = REPORT_DIR / f"nifty500_market_report_{report_date}.html"
    snapshot_path = DATA_DIR / f"nifty500_snapshot_{report_date}.csv"

    today_df.to_csv(snapshot_path, index=False)
    report_md_path.write_text(report_md, encoding="utf-8")
    report_html_path.write_text(report_html, encoding="utf-8")

    # Persist derivatives snapshot used in this report.
    if derivatives.get("available", False):
        if not derivatives.get("oi", pd.DataFrame()).empty:
            derivatives["oi"].to_csv(DATA_DIR / f"fii_dii_derivatives_oi_{report_date}.csv", index=False)
        if not derivatives.get("volume", pd.DataFrame()).empty:
            derivatives["volume"].to_csv(DATA_DIR / f"fii_dii_derivatives_volume_{report_date}.csv", index=False)
    if not pcr_df.empty:
        pcr_df.to_csv(DATA_DIR / f"daily_pcr_{report_date}.csv", index=False)

    email_status = "not attempted"
    recipients = parse_recipients(recipients_raw)
    if send_email:
        subject = f"Nifty 500 Daily Market Report - {report_date} | {parts['summary']['sentiment']}"
        send_gmail_report(subject, report_html, recipients)
        email_status = f"sent to {', '.join(recipients)}"

    print(f"Saved snapshot : {snapshot_path}")
    print(f"Saved markdown : {report_md_path}")
    print(f"Saved HTML     : {report_html_path}")
    print(f"Email status   : {email_status}")

    return {
        "summary": parts["summary"],
        "snapshot_path": str(snapshot_path),
        "report_md_path": str(report_md_path),
        "report_html_path": str(report_html_path),
        "email_status": email_status,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nifty 500 Daily Market Intelligence Pipeline")
    parser.add_argument("--date", type=str, default=None, help="Optional YYYY-MM-DD valuation date filter")
    parser.add_argument("--email-to", type=str, default=None, help="Comma-separated recipients")
    parser.add_argument("--no-email", action="store_true", help="Disable email sending")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(as_of=args.date, recipients_raw=args.email_to, send_email=(not args.no_email))
