
#!/usr/bin/env python3
"""Fetch turmeric futures from NCDEX and USD/Toman, then update data.json."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

NCDEX_URL = "https://www.ncdex.com/index.php/market-watch/live_quotes"
ARZDIGITAL_URL = "https://arzdigital.com/currencies/united-states-dollar/"
INR_FX_URL = "https://open.er-api.com/v6/latest/INR"

OUTPUT = Path("data.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "text/html,application/json,*/*",
}


def digits_to_ascii(value):
    return value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    ))


def parse_number(value):
    value = digits_to_ascii(value.strip())
    value = value.replace(",", "").replace("₹", "").strip()

    try:
        number = float(value)
        return number if number > 0 else None
    except (ValueError, TypeError):
        return None


def parse_expiry(value):
    value = value.strip()

    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass

    return None


def fetch_ncdex_turmeric():
    """Get LTP from the Low/LTP/High group on the NCDEX table."""

    response = requests.get(
        NCDEX_URL,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    contracts = []

    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False)

        # NCDEX live-quotes rows contain product, expiry, open,
        # a grouped Low/LTP/High cell, then close and other fields.
        if len(cells) < 5:
            continue

        product = cells[0].get_text(" ", strip=True)
        expiry_text = cells[1].get_text(" ", strip=True)

        if "turmeric" not in product.lower():
            continue

        if "farmer polished" not in product.lower():
            continue

        expiry = parse_expiry(expiry_text)
        if expiry is None or expiry < today:
            continue

        # Low, LTP and High are grouped in one table cell.
        grouped_text = cells[3].get_text(" ", strip=True)
        numbers = re.findall(
            r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?",
            digits_to_ascii(grouped_text),
        )

        if len(numbers) < 3:
            print(
                f"Could not parse Low/LTP/High for {product}: "
                f"{grouped_text!r}",
                file=sys.stderr,
            )
            continue

        # The middle number in the group is LTP.
        ltp = parse_number(numbers[1])
        if ltp is None:
            continue

        contracts.append({
            "product": product,
            "expiry": expiry,
            "price": ltp,
        })

    if not contracts:
        raise RuntimeError(
            "No valid turmeric LTP found in NCDEX. "
            "The website table structure may have changed."
        )

    # Use the nearest contract that has not expired.
    selected = min(contracts, key=lambda item: item["expiry"])

    print(
        f"NCDEX turmeric LTP: {selected['price']} INR/100 kg; "
        f"contract: {selected['product']}; "
        f"expiry: {selected['expiry']}"
    )

    return selected["price"], selected["expiry"], selected["product"]


def fetch_usd_per_inr():
    response = requests.get(
        INR_FX_URL,
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("result") != "success":
        raise RuntimeError("INR-to-USD rate service returned an error")

    rate = data.get("rates", {}).get("USD")

    if not isinstance(rate, (int, float)) or rate <= 0:
        raise RuntimeError("Could not read INR-to-USD exchange rate")

    return float(rate)


def fetch_usd_toman():
    response = requests.get(
        ARZDIGITAL_URL,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    text = re.sub(r"<[^>]+>", " ", response.text)
    text = digits_to_ascii(text)
    text = re.sub(r"\s+", " ", text)

    patterns = [
        r"نرخ تبادل دلار آمریکا و تومان در حال حاضر\s*([0-9][0-9,٬.]*)",
        r"قیمت لحظه(?:‌| )?ای دلار نیز\s*([0-9][0-9,٬.]*)\s*تومان",
        r"نرخ لحظه(?:‌| )?ای دلار[^0-9]{0,80}([0-9][0-9,٬.]*)\s*تومان",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if not match:
            continue

        value = re.sub(r"[,٬.]", "", match.group(1))

        try:
            rate = int(value)
        except ValueError:
            continue

        if 1_000 <= rate <= 100_000_000:
            return rate

    raise RuntimeError(
        "Could not reliably parse USD/Toman from ArzDigital. "
        "No new data.json was published."
    )


def main():
    quote, expiry, product = fetch_ncdex_turmeric()
    usd_per_inr = fetch_usd_per_inr()
    usd_toman = fetch_usd_toman()

    # INR per 100 kg -> INR per metric tonne -> USD per metric tonne.
    usd_per_ton = quote * 10 * usd_per_inr

    if not 1 <= usd_per_ton <= 1_000_000:
        raise RuntimeError(
            f"Converted USD/ton looks implausible: {usd_per_ton}"
        )

    result = {
        "usd_per_ton": round(usd_per_ton, 4),
        "usd_toman": usd_toman,
        "source_price": "NCDEX Live Quotes",
        "source_price_value": quote,
        "source_price_unit": "INR per 100 kg",
        "contract": product,
        "contract_expiry": expiry.isoformat(),
        "inr_to_usd": round(usd_per_inr, 8),
        "updated": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
        "status": "ok",
    }

    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Price data updated successfully:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Price update failed: {exc}", file=sys.stderr)
        sys.exit(1)
