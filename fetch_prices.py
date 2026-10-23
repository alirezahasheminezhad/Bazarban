#!/usr/bin/env python3
"""Fetch reference turmeric futures price and USD/Toman rate, then write data.json.

TradingView quotes this contract in INR per 100 kg. The script converts it to
USD per metric tonne using a separate INR->USD FX rate, then the dashboard uses
USD/Toman to calculate the landed cost. The TradingView scanner endpoint is
unofficial and may change or block automated requests; failures intentionally
stop the workflow rather than publishing fabricated values.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

TRADINGVIEW_SCANNER = "https://scanner.tradingview.com/india/scan"
ARZDIGITAL_URL = "https://arzdigital.com/currencies/united-states-dollar/"
INR_FX_URL = "https://open.er-api.com/v6/latest/INR"
OUTPUT = Path("data.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BazarbanPriceUpdater/1.0)",
    "Accept": "application/json,text/html,*/*",
}


def digits_to_ascii(s: str) -> str:
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return s.translate(table)


def fetch_tradingview_inr_per_100kg() -> float:
    payload = {
        "symbols": {"tickers": ["NCDEX:TMCFGRNZM1!"], "query": {"types": []}},
        "columns": ["close", "currency", "pricescale", "minmov"],
    }
    r = requests.post(TRADINGVIEW_SCANNER, json=payload, headers=HEADERS, timeout=25)
    r.raise_for_status()
    data = r.json()
    rows = data.get("data") or []
    if not rows or not rows[0].get("d"):
        raise RuntimeError("TradingView returned no quote for NCDEX:TMCFGRNZM1!")
    row = rows[0]
    vals = row.get("d", [])
    price = vals[0] if vals else None
    if not isinstance(price, (int, float)) or price <= 0:
        raise RuntimeError(f"Unexpected TradingView quote: {vals!r}")
    # TradingView's public symbol page identifies this instrument as INR / 100 KGM.
    # Scanner's close is expected to be the displayed quote. Validate this assumption
    # against the symbol page before relying on production calculations.
    return float(price)


def fetch_usd_per_inr() -> float:
    r = requests.get(INR_FX_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("result") != "success":
        raise RuntimeError(f"INR FX provider returned an error: {data}")
    rate = data.get("rates", {}).get("USD")
    if not isinstance(rate, (int, float)) or rate <= 0:
        raise RuntimeError("Could not find INR->USD rate")
    return float(rate)


def fetch_usd_toman_from_arzdigital() -> int:
    r = requests.get(ARZDIGITAL_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    html = r.text
    # Restrict parsing to text near the USD/Toman descriptions to avoid picking
    # unrelated prices elsewhere on the page. The page layout can change over time.
    text = digits_to_ascii(re.sub(r"<[^>]+>", " ", html))
    text = re.sub(r"\s+", " ", text)
    patterns = [
        r"نرخ تبادل دلار آمریکا و تومان در حال حاضر\s*([0-9][0-9,٬.]*)",
        r"قیمت لحظه(?: ای)? دلار نیز\s*([0-9][0-9,٬.]*)\s*تومان",
        r"نرخ لحظه(?: ای)? دلار[^0-9]{0,80}([0-9][0-9,٬.]*)\s*تومان",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = re.sub(r"[,٬.]", "", match.group(1))
            rate = int(value)
            if 1000 <= rate <= 100_000_000:
                return rate
    raise RuntimeError("Could not reliably parse USD/Toman rate from ArzDigital; no data.json update was published")


def main() -> None:
    quote_inr_100kg = fetch_tradingview_inr_per_100kg()
    usd_per_inr = fetch_usd_per_inr()
    usd_toman = fetch_usd_toman_from_arzdigital()

    # Quote INR/100kg -> INR/metric tonne (x10) -> USD/metric tonne.
    usd_per_ton = quote_inr_100kg * 10 * usd_per_inr
    if not (1 <= usd_per_ton <= 1_000_000):
        raise RuntimeError(f"Converted USD/ton looks implausible: {usd_per_ton}")

    result = {
        "usd_per_ton": round(usd_per_ton, 4),
        "usd_toman": usd_toman,
        "source_price": "TradingView NCDEX:TMCFGRNZM1!",
        "source_price_value": quote_inr_100kg,
        "source_price_unit": "INR per 100 kg",
        "inr_to_usd": round(usd_per_inr, 8),
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status": "ok",
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Price update failed: {exc}", file=sys.stderr)
        sys.exit(1)
