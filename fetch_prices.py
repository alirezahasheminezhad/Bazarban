#!/usr/bin/env python3
"""Fetch turmeric and black-pepper futures quotes from the official NCDEX site.

NCDEX futures quotes are treated as INR per 100 kg (one quintal). Prices are
converted to USD per metric tonne using the INR->USD FX rate; USD/Toman is read
from ArzDigital. data.json is only written after all required sources validate.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

NCDEX_URLS = [
    "https://www.ncdex.com/market-watch/live_quotes",
    "https://www.ncdex.com/",
]
ARZDIGITAL_URL = "https://arzdigital.com/currencies/united-states-dollar/"
INR_FX_URL = "https://open.er-api.com/v6/latest/INR"
OUTPUT = Path("data.json")
HISTORY_LIMIT = 2000
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
MONTHS = {m.lower(): i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def digits_to_ascii(value: str) -> str:
    return value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def parse_number(value: str) -> float | None:
    value = digits_to_ascii(value.strip()).replace(",", "").replace("٬", "").replace("₹", "")
    match = re.search(r"(?<![A-Za-z])\d+(?:\.\d+)?", value)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    return number if number > 0 else None


def expiry_key(text: str) -> tuple[int, int] | None:
    """Parse common NCDEX labels such as Nov26, Dec 2026, or 20-Nov-2026."""
    text = digits_to_ascii(text).strip()
    m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s\-/]*(\d{2,4})\b", text, re.I)
    if m:
        month = MONTHS[m.group(1).lower()[:3]]
        year = int(m.group(2))
        if year < 100:
            year += 2000
        return (year, month)
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(text, fmt).date()
            return (d.year, d.month)
        except ValueError:
            pass
    return None


def _candidate_rows(soup: BeautifulSoup, product: str) -> list[dict]:
    """Read product rows from NCDEX live-quote tables; tolerate table layout changes."""
    candidates = []
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    month_now = (today.year, today.month)
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_row = next((r for r in rows if r.find_all(["th", "td"], recursive=False)), None)
        headers = [c.get_text(" ", strip=True).lower() for c in header_row.find_all(["th", "td"], recursive=False)] if header_row else []
        ltp_idx = next((i for i, h in enumerate(headers) if "ltp" in h or "last traded" in h), None)
        for row in rows:
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 2:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
            row_text = " | ".join(texts)
            low = row_text.lower()
            if product == "pepper":
                if "pepper" not in low:
                    continue
            else:
                if "turmeric" not in low:
                    continue
                # Prefer the Farmer Polished futures contract when the site labels it.
                if "farmer polished" not in low and "turmeric" in low:
                    # Some tables shorten the contract label to Turmeric; allow it as fallback.
                    pass

            exp = None
            for text in texts:
                exp = expiry_key(text)
                if exp:
                    break
            if exp and exp < month_now:
                continue

            price = None
            # The official live-quotes page groups Low / LTP / High in one cell.
            # Never take the first number (Low) when the column header is grouped.
            if ltp_idx is not None and ltp_idx < len(texts):
                header = headers[ltp_idx] if ltp_idx < len(headers) else ""
                if "low" in header and "high" in header:
                    grouped = digits_to_ascii(texts[ltp_idx])
                    numbers = re.findall(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?", grouped)
                    if len(numbers) >= 3:
                        price = parse_number(numbers[1])
                else:
                    price = parse_number(texts[ltp_idx])
            # Older NCDEX quote tables also group Low / LTP / High into the fourth cell.
            if price is None and len(cells) >= 4:
                grouped = digits_to_ascii(texts[3])
                numbers = re.findall(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?", grouped)
                if len(numbers) >= 3:
                    price = parse_number(numbers[1])
            # On the homepage, the row is typically Contract | LTP | Change(%).
            if price is None:
                numeric = [parse_number(t) for t in texts]
                numeric = [n for n in numeric if n is not None]
                if numeric:
                    # Last numeric field is usually percentage change; choose the first plausible quote.
                    price = numeric[0]
            if price is None or not (1 <= price <= 10_000_000):
                continue
            candidates.append({"price": price, "expiry": exp, "label": row_text, "has_expiry": exp is not None})
    # Remove duplicate rows that can appear in multiple widgets/tables.
    unique = {}
    for item in candidates:
        key = (item["price"], item["expiry"], item["label"])
        unique[key] = item
    candidates = list(unique.values())
    if not candidates:
        return []
    dated = [x for x in candidates if x["expiry"] is not None]
    if dated:
        return [min(dated, key=lambda x: x["expiry"])]
    return candidates[:1]


def fetch_ncdex_quote(product: str) -> dict:
    errors = []
    for url in NCDEX_URLS:
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            candidates = _candidate_rows(soup, product)
            if candidates:
                selected = candidates[0]
                print(f"NCDEX {product}: {selected['price']} INR/100 kg; {selected['label']}")
                return {**selected, "url": url}
            errors.append(f"{url}: no matching quote rows found")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError(f"Could not read {product} futures price from NCDEX. " + "; ".join(errors))


def fetch_usd_per_inr() -> float:
    response = requests.get(INR_FX_URL, headers=HEADERS, timeout=20)
    response.raise_for_status()
    data = response.json()
    if data.get("result") != "success":
        raise RuntimeError("INR/USD FX provider returned an error")
    rate = data.get("rates", {}).get("USD")
    if not isinstance(rate, (int, float)) or rate <= 0:
        raise RuntimeError("Could not read INR-to-USD rate")
    return float(rate)


def fetch_usd_toman() -> int:
    response = requests.get(ARZDIGITAL_URL, headers=HEADERS, timeout=25)
    response.raise_for_status()
    text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
    text = digits_to_ascii(re.sub(r"\s+", " ", text))
    patterns = [
        r"نرخ تبادل دلار آمریکا و تومان در حال حاضر\s*([0-9][0-9,٬.]*)",
        r"قیمت لحظه(?:‌| )?ای دلار نیز\s*([0-9][0-9,٬.]*)\s*تومان",
        r"نرخ لحظه(?:‌| )?ای دلار[^0-9]{0,80}([0-9][0-9,٬.]*)\s*تومان",
        r"نرخ تبدیل دلار آمریکا به تومان\s*[:：]?\s*([0-9][0-9,٬.]*)\s*(?:تومان|ت)\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw = match.group(1).replace(",", "").replace("٬", "")
            if re.search(r"[KkMm]", raw):
                continue
            try:
                rate = int(float(raw))
            except ValueError:
                continue
            if 1_000 <= rate <= 100_000_000:
                print(f"ArzDigital USD/Toman: {rate}")
                return rate
    raise RuntimeError("Could not reliably parse USD/Toman from ArzDigital; data.json was not updated")


def load_previous_data() -> dict:
    try:
        data = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    turmeric = fetch_ncdex_quote("turmeric")
    pepper = fetch_ncdex_quote("pepper")
    usd_per_inr = fetch_usd_per_inr()
    usd_toman = fetch_usd_toman()

    # NCDEX quote is INR per 100 kg: x10 gives INR per metric tonne.
    turmeric_usd_ton = turmeric["price"] * 10 * usd_per_inr
    pepper_usd_ton = pepper["price"] * 10 * usd_per_inr
    for name, value in (("turmeric", turmeric_usd_ton), ("pepper", pepper_usd_ton)):
        if not (1 <= value <= 1_000_000):
            raise RuntimeError(f"Converted {name} USD/ton looks implausible: {value}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    previous = load_previous_data()
    history = previous.get("history", [])
    if not isinstance(history, list):
        history = []
    # One durable history record per successful scheduled fetch, even if the quote is unchanged.
    history.append({"t": now, "p": round(turmeric_usd_ton, 4), "b": round(pepper_usd_ton, 4), "r": usd_toman})
    history = history[-HISTORY_LIMIT:]

    result = {
        "usd_per_ton": round(turmeric_usd_ton, 4),
        "pepper_usd_per_ton": round(pepper_usd_ton, 4),
        "usd_toman": usd_toman,
        "source_price": "NCDEX official live futures quotes",
        "turmeric_source_price_value": turmeric["price"],
        "turmeric_source_price_unit": "INR per 100 kg",
        "turmeric_contract": turmeric["label"],
        "pepper_source_price_value": pepper["price"],
        "pepper_source_price_unit": "INR per 100 kg",
        "pepper_contract": pepper["label"],
        "inr_to_usd": round(usd_per_inr, 8),
        "updated": now,
        "status": "ok",
        "history": history,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "history"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Price update failed: {exc}", file=sys.stderr)
        sys.exit(1)
