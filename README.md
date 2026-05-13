# DL Country Framework v0.9.9

Static dashboard for country-level equity expected returns across 22 ETFs, with a Vercel serverless function for live commodity prices.

## Repo structure

```
.
├── index.html              ← the dashboard (single file, no build)
├── data.json               ← country inputs
├── vercel.json             ← Vercel config
├── api/
│   ├── commodities.py      ← serverless function, fetches via Stooq (primary) + yfinance (fallback)
│   └── requirements.txt    ← Python deps (yfinance)
├── README.md
└── .gitignore
```

## Tabs

1. **§01 Front Dashboard** — tier aggregate banner (US / DM / EM / EMHR), compact country matrix with pass/fail vs hurdle
2. **§02 Full Inputs** — wide editable matrix with FX decomposition (parity / overshoot / override) visible
3. **§03 Hurdle Rate** — sorted by gap vs SPY-based hurdle, pass/near/fail badges
4. **§04 Country Detail** — multiplicative return waterfall, three-mode growth comparison, narrative, full editable inputs
5. **§05 Methodology** — in-app explainer

## Growth modes

| Mode | Formula |
|---|---|
| **Median** (default) | per-country median of GDP×β, LT EPS, SGR |
| GDP × β | `(gdp × λ_g + inflation) × β_GDP` |
| LT EPS | realized 10y EPS CAGR |
| SGR | `ROE × (1 − payout)` |

## Return assembly

```
local return  = FCF yield + earnings growth + multiple reversion
local adj     = local return × (1 − 0.4 × bank weight)
r_USD         = (1 + local adj) × (1 + ΔFX) − 1
```

Where:
- **Multiple reversion** = `(PE_terminal / PE_current)^(1/5) − 1` (5y horizon; default terminal = current)
- **FX** = inflation parity + CAD overshoot, or user override:
  - `ΔFX_parity = −(π_local − π_US)` with `π_US = 2.5%`
  - `ΔFX_overshoot = (CAD − (−3%)) × 1.0` when CAD < −3%, else 0
  - `ΔFX = parity + overshoot` unless `fx_user` is set, in which case override replaces both

## Commodity panel

Live moves from `/api/commodities`, a Vercel Python function with two data sources:

1. **Stooq** (primary) — reliable from cloud IPs, no rate limit. Uses US-listed ETF proxies.
2. **yfinance** (fallback) — Yahoo Finance via the `yfinance` library. Frequently rate-limited from cloud IPs, but tried when Stooq fails for a specific commodity.

| Commodity | Stooq (primary) | yfinance (fallback) |
|---|---|---|
| Brent oil | `BNO.US` (United States Brent Oil Fund) | `BZ=F` |
| Copper | `CPER.US` (United States Copper Index Fund) | `HG=F` |
| Iron ore | `VALE.US` (Vale — iron ore proxy) | `TIO=F` |
| Agriculture | `DBA.US` (Invesco DB Agriculture) | `^BCOMAG` |
| Broad commodity | `DBC.US` (Invesco DB Commodity) | `^BCOM` |

The function returns both `m6_pct` (6-month % change, drives arrow signal) and `yoy_pct` (12-month % change, display context). Cached 1 hour at the CDN. If both sources fail for any commodity, the UI falls back to manual input for that one.

The commodity panel drives only the qualitative Commodity Impact arrow per country. Numerical commodity coefficients remain zeroed in the math.

Force a specific data source via query parameter for debugging:
- `/api/commodities?source=stooq` — Stooq only
- `/api/commodities?source=yfinance` — yfinance only
- `/api/commodities` — auto (Stooq, then yfinance fallback per commodity)

## Run locally

```bash
# Frontend only (commodity panel will show "Live fetch failed" — manual entry boxes)
python3 -m http.server 8000

# Full local with Vercel dev (requires `vercel` CLI)
npm i -g vercel
vercel dev
```

## Deploy to Vercel

1. **Push to GitHub:**
   ```bash
   git add .
   git commit -m "v0.9.9: Stooq commodity feed, methodology cleanup"
   git push
   ```

2. Vercel auto-rebuilds on push. `api/commodities.py` is auto-detected as a Python serverless function; `api/requirements.txt` is installed automatically.

3. **Verify the function** by visiting `your-app.vercel.app/api/commodities` — should return JSON with five commodities, each with `price`, `yoy_pct`, `m6_pct`, and `source` (showing "stooq" or "yfinance" per commodity).

## Inputs schema

```json
{
  "ticker": "SPY",
  "country": "United States",
  "tier": "US",
  "fin": 11.92,
  "tech": 45.10,
  "com": 6.08,
  "gdp": 1.99,
  "inflation": 2.24,
  "fx_chg": 0.42,
  "fx_user": null,
  "fx_vol": 0.00,
  "roe": 20.71,
  "payout": 28.78,
  "sgr": 14.75,
  "c": 0.754,
  "cad_curr": -3.71,
  "cad_n5y": -3.59,
  "lt_eps": 12.78,
  "beta_gdp": 1.40,
  "pe": 20.98,
  "pe_terminal": null,
  "com_profile": "NEUTRAL"
}
```

Field notes:
- `tier`: `US` | `DM` | `EM` | `EMHR`
- `inflation`: drives FX parity. Current/forward, not historical.
- `fx_chg`: historical 10y FX move. Kept for reference, not used in math.
- `fx_user`: per-country FX override. `null` = use parity construction.
- `cad_curr`: current account % GDP. Negative = deficit. Drives CAD overshoot below −3%.
- `pe_terminal`: optional 5y exit P/E. `null` = no reversion (terminal = current).
- `com_profile`: `OIL_HEAVY` | `BROAD_EXP` | `MILD_EXP` | `NEUTRAL` | `MILD_IMP` | `HEAVY_IMP`

## License

Internal research tool.
