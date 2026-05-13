"""
Vercel serverless function: live commodity price moves.

Data sources (with fallback chain):
  1. Stooq.com  — primary. No auth, no rate limit, cloud-friendly. CSV endpoint.
  2. yfinance   — fallback. Yahoo aggressively rate-limits cloud IPs; works
                  intermittently but worth trying as a second pass.

Returns JSON:
{
  "ok": true,
  "asof": "2026-05-13T09:00:00Z",
  "source": "stooq" | "yfinance" | "mixed",
  "commodities": {
    "oil":    { "ticker": "...", "label": "...",
                "price": 84.23, "yoy_pct": 5.1, "m6_pct": 2.3,
                "source": "stooq", "ok": true },
    ...
  }
}

m6_pct  = 6-month % change (drives country Commodity Impact arrows)
yoy_pct = 12-month % change (display context only)

If a single commodity fails on both sources, that entry's `ok` is false and
its numeric fields are null. The function still returns 200; the frontend
falls back to manual entry for any commodity that didn't load.

Cached for 1 hour at the CDN via Cache-Control.

Force a specific source for debugging:
  /api/commodities                  → auto (Stooq, then yfinance fallback)
  /api/commodities?source=stooq     → Stooq only
  /api/commodities?source=yfinance  → yfinance only
"""

import json
import datetime as dt
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler


# Symbol map. Each commodity has:
#   - stooq: a reliable Stooq symbol (typically a US-listed ETF proxy, daily-liquid)
#   - yfinance: the cleanest Yahoo ticker (typically the underlying future)
#
# The Stooq US-ETF route is more reliable from cloud hosts; yfinance is the
# fallback. ETF proxies move directionally with their underlying — the
# magnitude can differ a few percent due to roll yield, but for a 6m signal
# that drives a qualitative arrow this is plenty accurate.
#
# Proxies chosen:
#   oil    → BNO (Brent ETF)        / BZ=F (Brent front-month future)
#   copper → CPER (Copper ETF)      / HG=F (COMEX copper future)
#   iron   → VALE (Vale, ~60% iron) / TIO=F (unreliable everywhere)
#   ag     → DBA (Agriculture ETF)  / ^BCOMAG (Bloomberg Ag subindex)
#   broad  → DBC (Commodity ETF)    / ^BCOM (Bloomberg Commodity broad)
COMMODITIES = {
    "oil":    {"label": "Brent Crude (BNO proxy)",   "stooq": "bno.us",  "yfinance": "BZ=F"},
    "copper": {"label": "Copper (CPER proxy)",       "stooq": "cper.us", "yfinance": "HG=F"},
    "iron":   {"label": "Iron Ore (VALE proxy)",     "stooq": "vale.us", "yfinance": "TIO=F"},
    "ag":     {"label": "Agriculture (DBA proxy)",   "stooq": "dba.us",  "yfinance": "^BCOMAG"},
    "broad":  {"label": "Broad Commodity (DBC)",     "stooq": "dbc.us",  "yfinance": "^BCOM"},
}


def _fetch_url(url, timeout=10):
    """Plain GET with a browser-like User-Agent. Returns response bytes or raises."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ============================================================================
# STOOQ
# ============================================================================
def fetch_stooq(symbol):
    """Returns (price_now, yoy_pct, m6_pct) for a Stooq symbol or raises.

    Stooq CSV endpoint:
      https://stooq.com/q/d/l/?s=SYMBOL&d1=YYYYMMDD&d2=YYYYMMDD&i=d

    Returns daily OHLCV with header: Date,Open,High,Low,Close,Volume
    Missing data = empty body or 'No data'.
    """
    if not symbol:
        raise RuntimeError("no Stooq symbol")

    end = dt.date.today()
    # Pull ~14 months to cover both 6m and 12m windows with holiday slack
    start = end - dt.timedelta(days=420)
    url = (f"https://stooq.com/q/d/l/?s={urllib.parse.quote(symbol)}"
           f"&d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}&i=d")

    body = _fetch_url(url).decode("utf-8", errors="replace").strip()
    if not body or "No data" in body[:50]:
        raise RuntimeError(f"Stooq returned no data for {symbol}")

    lines = body.splitlines()
    if len(lines) < 30:
        raise RuntimeError(f"Stooq returned only {len(lines)} lines for {symbol}")

    # Parse header to find Close column position
    header = [c.strip().lower() for c in lines[0].split(",")]
    if "close" not in header:
        raise RuntimeError(f"Stooq CSV missing Close column for {symbol}")
    close_idx = header.index("close")
    date_idx = header.index("date") if "date" in header else 0

    rows = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= max(close_idx, date_idx):
            continue
        try:
            d = dt.datetime.strptime(parts[date_idx], "%Y-%m-%d").date()
            c = float(parts[close_idx])
        except (ValueError, IndexError):
            continue
        rows.append((d, c))

    if len(rows) < 30:
        raise RuntimeError(f"Stooq parsed only {len(rows)} valid rows for {symbol}")
    rows.sort(key=lambda x: x[0])

    return _compute_changes(rows)


# ============================================================================
# YFINANCE FALLBACK
# ============================================================================
def fetch_yfinance(symbol):
    """Returns (price_now, yoy_pct, m6_pct) or raises."""
    if not symbol:
        raise RuntimeError("no yfinance symbol")
    import yfinance as yf
    t = yf.Ticker(symbol)
    hist = t.history(period="14mo", interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"yfinance returned empty for {symbol}")
    closes = hist["Close"].dropna()
    if len(closes) < 30:
        raise RuntimeError(f"yfinance: only {len(closes)} closes for {symbol}")
    rows = [(idx.date() if hasattr(idx, "date") else idx, float(c))
            for idx, c in closes.items()]
    return _compute_changes(rows)


# ============================================================================
# SHARED HELPERS
# ============================================================================
def _compute_changes(rows):
    """rows = list of (date, close), sorted ascending. Returns (price, yoy%, m6%)."""
    today_date = rows[-1][0]
    price_now = rows[-1][1]

    def closest_before(target_date, tolerance_days=10):
        """Find the latest row on or before target_date + tolerance_days."""
        cutoff = target_date + dt.timedelta(days=tolerance_days)
        candidates = [r for r in rows if r[0] <= cutoff]
        if not candidates:
            return rows[0][1]
        return candidates[-1][1]

    price_yago = closest_before(today_date - dt.timedelta(days=365))
    price_6mo = closest_before(today_date - dt.timedelta(days=182))

    if price_yago == 0 or price_6mo == 0:
        raise RuntimeError("zero base price")

    return price_now, (price_now / price_yago - 1) * 100, (price_now / price_6mo - 1) * 100


def fetch_with_fallback(key, meta, source_pref="auto"):
    """Try Stooq first, fall back to yfinance. Returns entry dict.

    source_pref: 'auto' | 'stooq' | 'yfinance'
    """
    errors = []
    used_source = None
    price = yoy = m6 = None

    sources_to_try = []
    if source_pref == "stooq":
        sources_to_try = [("stooq", meta["stooq"])]
    elif source_pref == "yfinance":
        sources_to_try = [("yfinance", meta["yfinance"])]
    else:  # auto
        sources_to_try = [("stooq", meta["stooq"]), ("yfinance", meta["yfinance"])]

    for source_name, symbol in sources_to_try:
        if not symbol:
            errors.append(f"{source_name}: no symbol")
            continue
        try:
            if source_name == "stooq":
                price, yoy, m6 = fetch_stooq(symbol)
            else:
                price, yoy, m6 = fetch_yfinance(symbol)
            used_source = source_name
            break
        except Exception as e:
            errors.append(f"{source_name}({symbol}): {str(e)[:120]}")
            continue

    if used_source is None:
        return {
            "label": meta["label"],
            "ticker": meta["yfinance"] or meta["stooq"],
            "price": None, "yoy_pct": None, "m6_pct": None,
            "source": None, "ok": False,
            "error": "; ".join(errors)[:300],
        }

    return {
        "label": meta["label"],
        "ticker": meta["yfinance"] or meta["stooq"],
        "stooq_symbol": meta["stooq"] or None,
        "price": round(price, 4),
        "yoy_pct": round(yoy, 2),
        "m6_pct": round(m6, 2),
        "source": used_source,
        "ok": True,
    }


def build_response(source_pref="auto"):
    out = {}
    sources_used = set()
    for key, meta in COMMODITIES.items():
        entry = fetch_with_fallback(key, meta, source_pref=source_pref)
        if entry["ok"]:
            sources_used.add(entry["source"])
        out[key] = entry

    if len(sources_used) == 0:
        agg_source = "none"
    elif len(sources_used) == 1:
        agg_source = next(iter(sources_used))
    else:
        agg_source = "mixed"

    return {
        "ok": True,
        "asof": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": agg_source,
        "commodities": out,
    }


# ============================================================================
# HANDLER
# ============================================================================
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            # Parse ?source= query param
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            source_pref = (qs.get("source", ["auto"])[0] or "auto").lower()
            if source_pref not in ("auto", "stooq", "yfinance"):
                source_pref = "auto"

            payload = build_response(source_pref=source_pref)
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
