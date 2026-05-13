"""
Vercel serverless function: live commodity price moves.

Returns JSON shaped:
{
  "ok": true,
  "asof": "2026-05-13T09:00:00Z",
  "commodities": {
    "oil":    { "ticker": "BZ=F", "label": "Brent Crude",
                "price": 84.23, "yoy_pct": 5.1, "m6_pct": 2.3, "ok": true },
    ...
  }
}

m6_pct is the 6-month price change (drives the country Commodity Impact arrows).
yoy_pct is the 12-month change (shown as context).

If a single ticker fails, that commodity's `ok` is false and its values are null,
but the function still returns 200 with the rest of the data. The frontend
falls back to manual entry for any commodity that didn't load.

Cached for 1 hour at the CDN via Cache-Control.
"""

import json
import datetime as dt
from http.server import BaseHTTPRequestHandler


# Ticker map. Notes:
# - BZ=F is Brent front-month
# - HG=F is COMEX copper front-month
# - SCO.AX is the iron ore SGX/ASX proxy. Yahoo's TIO=F is unreliable.
# - ^BCOMAG is the Bloomberg Agriculture subindex
# - ^BCOM is the Bloomberg Commodity broad index
TICKERS = {
    "oil":    {"ticker": "BZ=F",    "label": "Brent Crude"},
    "copper": {"ticker": "HG=F",    "label": "Copper"},
    "iron":   {"ticker": "SCO.AX",  "label": "Iron Ore (SGX proxy)"},
    "ag":     {"ticker": "^BCOMAG", "label": "Bloomberg Agriculture"},
    "broad":  {"ticker": "^BCOM",   "label": "Bloomberg Commodity"},
}


def _price_at_offset(closes, days_back, tolerance_days=10):
    """Return the close price at approximately `days_back` days before the most
    recent observation. Walks backward to find the closest available date,
    within `tolerance_days` of the target."""
    target = closes.index[-1] - dt.timedelta(days=days_back)
    earlier = closes[closes.index <= target + dt.timedelta(days=tolerance_days)]
    if earlier.empty:
        return float(closes.iloc[0])
    return float(earlier.iloc[-1])


def fetch_one(ticker_symbol):
    """Returns (price_now, yoy_pct, m6_pct) or raises."""
    import yfinance as yf

    t = yf.Ticker(ticker_symbol)
    # ~13 months of history covers both 6m and 12m anchors with holiday slack
    hist = t.history(period="13mo", interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"No data for {ticker_symbol}")

    closes = hist["Close"].dropna()
    if len(closes) < 30:
        raise RuntimeError(f"Insufficient history for {ticker_symbol}")

    price_now = float(closes.iloc[-1])
    price_yago = _price_at_offset(closes, 365)
    price_6mo = _price_at_offset(closes, 182)

    if price_yago == 0 or price_6mo == 0:
        raise RuntimeError(f"Zero base price for {ticker_symbol}")

    yoy_pct = (price_now / price_yago - 1.0) * 100.0
    m6_pct = (price_now / price_6mo - 1.0) * 100.0
    return price_now, yoy_pct, m6_pct


def build_response():
    out = {}
    for key, meta in TICKERS.items():
        entry = {"ticker": meta["ticker"], "label": meta["label"]}
        try:
            price, yoy, m6 = fetch_one(meta["ticker"])
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
            err = json.dumps({"ok": False, "error": str(e)[:300]}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
