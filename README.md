# DL Country Framework v0.9.8

Static dashboard for country-level equity expected returns across 22 ETFs, with a Vercel serverless function for live commodity prices.

## v0.9.8 changes vs the prior iteration

- **FX reverted to parity + CAD overshoot.** Prior iteration used historical 10y FX CAGR (`fx_chg` field); this version restores the v0.9.4 forward-looking spec — `−(π_local − π_US)` plus CAD drag if deficit > 3%. Material for high-inflation countries: Turkey rUSD shifts from roughly −12% (under historical FX) to roughly +0% (under parity), because current inflation is far below the 10y average. Per-country override available via `fx_user` (blank = parity).
- **6-month commodity signal.** Commodity panel now fetches both 6m and 12m % moves. The 6m drives the country **Commodity Impact** arrows on the Front Dashboard (renamed from "Risk"). 12m is shown as context. Both can be manually overridden by clicking any value.

## Repo structure

```
.
├── index.html              ← the dashboard (single file, no build)
├── data.json               ← country inputs
├── vercel.json             ← Vercel config
├── api/
│   ├── commodities.py      ← serverless function, fetches commodity prices via yfinance
│   └── requirements.txt    ← Python deps (yfinance)
├── README.md
└── .gitignore
```

## Tabs

1. **§01 Front Dashboard** — tier aggregate banner (US / DM / EM / EMHR), compact country matrix with pass/fail vs hurdle
2. **§02 Full Inputs** — wide editable matrix with FX decomposition (parity / overshoot / override) visible
3. **§03 Hurdle Rate** — sorted by gap vs SPY-based hurdle, pass/fail badges
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

FX is multiplicative (not additive — corrects v0.9.4 linearization at high inflation). Bank haircut applies to local return before FX translation.

## Commodity panel

Live moves from `/api/commodities` (Vercel serverless function pulling Yahoo Finance via `yfinance`):

- Brent Crude — `BZ=F`
- Copper — `HG=F`
- Iron Ore — `SCO.AX` (SGX-linked proxy; spot iron ore on Yahoo is unreliable)
- Bloomberg Agriculture — `^BCOMAG`
- Bloomberg Commodity — `^BCOM`

The Python function returns both `m6_pct` (6-month % change, drives arrow signal) and `yoy_pct` (12-month % change, display context). Cached 1 hour at the CDN. If yfinance fails for any ticker, the UI falls back to manual input for that one. Click any live value to override.

**The commodity panel drives only the qualitative Commodity Impact arrow per country.** Numerical commodity coefficients remain zeroed in the math.

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
   git commit -m "v0.9.8: FX parity reverted, 6m commodity signal"
   git push
   ```

2. Vercel auto-rebuilds on push. `api/commodities.py` is auto-detected as a Python serverless function; `api/requirements.txt` is installed automatically.

3. **Verify the function** by visiting `your-app.vercel.app/api/commodities` — should return JSON with five commodities, each with `price`, `yoy_pct`, and `m6_pct`.

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
- `tier`: `US` | `DM` | `EM` | `EMHR` (string `"EM HR"` is auto-normalized)
- `inflation`: drives FX parity. Current/forward, not historical.
- `fx_chg`: historical 10y FX move. **No longer drives the FX calc** — kept in the schema for reference / future use.
- `fx_user`: per-country FX override. `null` = use parity construction.
- `cad_curr`: current account % GDP. Negative = deficit. Drives CAD overshoot below −3%.
- `pe_terminal`: optional 5y exit P/E. `null` = no reversion (terminal = current).
- `com_profile`: `OIL_HEAVY` | `BROAD_EXP` | `MILD_EXP` | `NEUTRAL` | `MILD_IMP` | `HEAVY_IMP`

## License

Internal research tool.
