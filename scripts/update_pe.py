#!/usr/bin/env python3
"""
Refresh the current P/E ("pe") of every ETF in data.json from Yahoo Finance.

- Only `pe`, `pe_asof` and `pe_source` are written. `hist_pe` and every other
  input are left untouched.
- A ticker keeps its previous P/E (and is reported) when Yahoo returns nothing,
  a non-numeric value, a value outside [PE_MIN, PE_MAX], or a jump larger than
  MAX_JUMP vs. the stored value. Pass --force to accept large jumps.
- Exits 1 only if every ticker failed (so a CI run is flagged as broken).

Usage:
    python scripts/update_pe.py              # update data.json in place
    python scripts/update_pe.py --dry-run    # print what would change
    python scripts/update_pe.py --force      # accept jumps > MAX_JUMP
"""
import argparse
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data.json"

PE_FIELD = "trailingPE"   # Yahoo field for ETFs (forwardPE is usually missing for ETFs)
PE_MIN, PE_MAX = 2.0, 80.0
MAX_JUMP = 0.40           # 40% move vs stored value is treated as suspect
RETRIES = 3


def fetch_pe(ticker, yf):
    """Return Yahoo's P/E for one ticker, or None."""
    for attempt in range(RETRIES):
        try:
            info = yf.Ticker(ticker).info or {}
            val = info.get(PE_FIELD)
            if val is None:
                return None
            val = float(val)
            return val if math.isfinite(val) else None
        except Exception as e:  # rate limits / transient network errors
            print(f"  {ticker}: attempt {attempt + 1} failed ({type(e).__name__}: {e})")
            time.sleep(2 * (attempt + 1))
    return None


def update(rows, fetch, force=False, today=None):
    today = today or dt.date.today().isoformat()
    changed, failed = [], []
    for r in rows:
        t = r["ticker"]
        old = r.get("pe")
        new = fetch(t)
        if new is None:
            failed.append((t, "no data"))
            continue
        if not (PE_MIN <= new <= PE_MAX):
            failed.append((t, f"out of range ({new:.2f})"))
            continue
        if old and not force and abs(new / old - 1) > MAX_JUMP:
            failed.append((t, f"jump {old:.2f} -> {new:.2f} exceeds {MAX_JUMP:.0%}; use --force"))
            continue
        new = round(new, 2)
        r["pe"] = new
        r["pe_asof"] = today
        r["pe_source"] = f"yahoo:{PE_FIELD}"
        changed.append((t, old, new))
    return changed, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    import yfinance as yf

    rows = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    def fetch(t):
        v = fetch_pe(t, yf)
        time.sleep(0.5)  # be gentle with Yahoo
        return v

    changed, failed = update(rows, fetch, force=args.force)

    for t, old, new in changed:
        print(f"OK    {t:<5} {old} -> {new}")
    for t, why in failed:
        print(f"KEPT  {t:<5} {why}")
    print(f"\n{len(changed)} updated, {len(failed)} kept old value")

    if not changed:
        print("No ticker updated — failing so the problem is visible.")
        sys.exit(1)

    if not args.dry_run:
        DATA_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {DATA_PATH}")


if __name__ == "__main__":
    main()
