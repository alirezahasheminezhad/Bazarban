
#!/usr/bin/env python3
"""Fetch NCDEX turmeric futures and USD/Toman, then write data.json."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

NCDEX_URL = "https://www.ncdex.com/index.php/market-watch/live_quotes"
ARZDIGITAL_URL = "https://arzdigital.com/convert/usd/toman/"
INR_FX_URL = "https://open.er-api.com/v6/latest/INR"
OUTPUT = Path("data.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
}


def digits_to_ascii(value):
    return value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    ))


def parse_number(value):
    value = digits_to_ascii(value.strip())
    value = value.replace(",", "").replace("٬", "").replace("₹", "").strip()

    try:
        number = float(value)
        return number if number > 0 else None
    except (ValueError, TypeError):
        return None


def parse_expiry(value):
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def fetch_ncdex_turmeric():
    response = requests.get(NCDEX_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    contracts = []

    for row in soup.select("tr"):
        cells = row.find_all("td", recursive=False)
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

        # NCDEX groups Low / LTP / High in one cell.
        grouped_text = digits_to_ascii(cells[3].get_text(" ", strip=True))
        numbers = re.findall(
            r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?",
            grouped_text,
        )

        if len(numbers) < 3:
            print(
                f"Cannot parse NCDEX Low/LTP/High: {grouped_text!r}",
                file=sys.stderr,
            )
            continue

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
            "No valid turmeric futures quote found on NCDEX."
        )

    selected = min(contracts, key=lambda item: item["expiry"])

    print(
        f"NCDEX LTP: {selected['price']} INR/100 kg; "
        f"contract: {selected['product']}; "
        f"expiry: {selected['expiry']}"
    )

    return selected["price"], selected["expiry"], selected["product"]


def fetch_usd_per_inr():
    response = requests.get(INR_FX_URL, headers=HEADERS, timeout=20)
    response.raise_for_status()
    data = response.json()

    if data.get("result") != "success":
        raise RuntimeError("INR-to-USD provider returned an error.")

    rate = data.get("rates", {}).get("USD")
    if not isinstance(rate, (int, float)) or rate <= 0:
        raise RuntimeError("Could not read INR-to-USD exchange rate.")

    return float(rate)


def fetch_usd_toman():
    """Read the USD-to-Toman rate from ArzDigital's conversion page."""

    response = requests.get(
        ARZDIGITAL_URL,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = digits_to_ascii(text)
    text = re.sub(r"\s+", " ", text)

    # Prefer an explicit USD -> IRT conversion with a full numeric value.
    patterns = [
        r"1\s*USD\s*([0-9][0-9,٬.]*)\s*IRT\b",
        r"قیمت امروز USD به تومان\s*[:：]?\s*([0-9][0-9,٬.]*)\s*(?:تومان|ت)\b",
        r"نرخ تبدیل دلار آمریکا به تومان\s*[:：]?\s*([0-9][0-9,٬.]*)\s*(?:تومان|ت)\b",
        r"هر\s*1\s*دلار آمریکا معادل\s*([0-9][0-9,٬.]*)\s*تومان",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw = match.group(1).replace(",", "").replace("٬", "")

            # Do not accept abbreviated values such as 193K.
            if re.search(r"[KkMm]", raw):
                continue

            try:
                rate = int(float(raw))
            except ValueError:
                continue

            if 1_000 <= rate <= 100_000_000:
                print(f"ArzDigital USD/Toman: {rate}")
                return rate

    # Print useful diagnostics without dumping the entire page.
    position = text.lower().find("usd")
    excerpt = text[max(0, position - 100):position + 700]
    print(
        "Could not parse USD/Toman. Page excerpt: " + excerpt,
        file=sys.stderr,
    )
    raise RuntimeError(
        "Could not reliably extract a full USD/Toman rate from ArzDigital."
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
