"""
Vercel serverless function: live commodity price moves from Yahoo Finance.

Uses yfinance.download() in a single batched call — same pattern as the
Beta_Pf project (which deploys to Vercel and reliably fetches Yahoo data).

Returns JSON:
{
  "ok": true,
  "asof": "2026-05-13T09:00:00Z",
  "commodities": {
    "oil":    { "ticker": "BZ=F", "label": "Brent Crude",
                "price": 84.23, "yoy_pct": 5.1, "m6_pct": 2.3, "ok": true },
    ...
  }
}

m6_pct  = 6-month % change (drives country Commodity Impact arrows)
yoy_pct = 12-month % change (display context only)

Cached for 1 hour at the CDN.
"""

import json
import datetime as dt
from http.server import BaseHTTPRequestHandler


# Yahoo tickers. Front-month futures where available.
COMMODITIES = {
    "oil":    {"ticker": "BZ=F",    "label": "Brent Crude"},
    "copper": {"ticker": "HG=F",    "label": "Copper"},
    "iron":   {"ticker": "VALE",    "label": "Iron Ore (VALE proxy)"},  # VALE common stock — iron ore proxy
    "ag":     {"ticker": "DBA",     "label": "Agriculture (DBA proxy)"},
    "broad":  {"ticker": "DBC",     "label": "Broad Commodity (DBC)"},
}


def _changes_from_closes(closes):
    """Given a sorted pandas Series of closes (newest last), return
    (price_now, yoy_pct, m6_pct). Raises if insufficient data."""
    import pandas as pd
    closes = closes.dropna()
    if len(closes) < 30:
        raise RuntimeError(f"insufficient history ({len(closes)} closes)")

    price_now = float(closes.iloc[-1])
    last_date = closes.index[-1]
    # If pandas index is tz-aware, the arithmetic below stays consistent

    def closest_at_offset(days_back):
        target = last_date - pd.Timedelta(days=days_back)
        # Closes at or before (target + tolerance window)
        tol = pd.Timedelta(days=10)
        eligible = closes[closes.index <= target + tol]
        if eligible.empty:
            return float(closes.iloc[0])
        return float(eligible.iloc[-1])

    price_yago = closest_at_offset(365)
    price_6mo = closest_at_offset(182)
    if price_yago == 0 or price_6mo == 0:
        raise RuntimeError("zero base price")

    return price_now, (price_now / price_yago - 1) * 100, (price_now / price_6mo - 1) * 100


def build_response():
    """Batch-fetch all tickers via yf.download(), then compute 6m and 12m changes per symbol."""
    import yfinance as yf
    import pandas as pd

    tickers = [meta["ticker"] for meta in COMMODITIES.values()]

    # Single batched request — same pattern as Beta_Pf.
    # period='1y' gives ~252 trading days; we need just under that for 12m comparison.
    # Use '14mo' to be safe with weekends/holidays at the boundary.
    df = yf.download(
        tickers=tickers,
        period="14mo",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=True,
        group_by="ticker",
    )

    out = {}
    for key, meta in COMMODITIES.items():
        symbol = meta["ticker"]
        entry = {"ticker": symbol, "label": meta["label"]}
        try:
            # df has a MultiIndex on columns when multiple tickers: (ticker, field)
            if isinstance(df.columns, pd.MultiIndex):
                if symbol not in df.columns.get_level_values(0):
                    raise RuntimeError(f"no column for {symbol}")
                closes = df[symbol]["Close"]
            else:
                # Single-ticker case (df might collapse)
                closes = df["Close"]

            price, yoy, m6 = _changes_from_closes(closes)
            entry["price"] = round(price, 4)
            entry["yoy_pct"] = round(yoy, 2)
            entry["m6_pct"] = round(m6, 2)
            entry["ok"] = True
        except Exception as e:
            entry["price"] = None
            entry["yoy_pct"] = None
            entry["m6_pct"] = None
            entry["ok"] = False
            entry["error"] = str(e)[:200]
        out[key] = entry

    return {
        "ok": True,
        "asof": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "yfinance",
        "commodities": out,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            payload = build_response()
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "public, s-maxage=3600, stale-while-revalidate=7200")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({
                "ok": False,
                "error": str(e)[:300],
                "type": type(e).__name__,
            }).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
