
#!/usr/bin/env python3
"""Fetch turmeric futures from NCDEX and USD/Toman, then update data.json."""

import json
import re
import sys
from datetime import datetime
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
    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    )
    return value.translate(table)


def parse_number(value):
    value = digits_to_ascii(value.strip())
    value = value.replace(",", "").replace("₹", "").strip()

    if not value or value in {"-", "--", "N/A", "NA"}:
        return None

    try:
        return float(value)
    except ValueError:
        return None


def fetch_ncdex_turmeric():
    """Read the nearest unexpired polished-turmeric futures contract."""

    response = requests.get(
        NCDEX_URL,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    contracts = []

    for table in soup.find_all("table"):
        header_row = None
        headers = []

        for tr in table.find_all("tr"):
            ths = tr.find_all("th")
            if not ths:
                continue

            candidate = [
                re.sub(r"\s+", " ", th.get_text(" ", strip=True))
                for th in ths
            ]
            normalized = [h.lower() for h in candidate]

            if (
                any("product name" in h or "symbol" in h for h in normalized)
                and any("expiry" in h for h in normalized)
                and any(h == "ltp" or "last traded" in h for h in normalized)
            ):
                header_row = tr
                headers = candidate
                break

        if not header_row:
            continue

        normalized_headers = [h.lower() for h in headers]

        product_index = next(
            (i for i, h in enumerate(normalized_headers)
             if "product name" in h or "symbol" in h),
            None,
        )
        expiry_index = next(
            (i for i, h in enumerate(normalized_headers) if "expiry" in h),
            None,
        )
        ltp_index = next(
            (i for i, h in enumerate(normalized_headers)
             if h == "ltp" or "last traded" in h),
            None,
        )

        if None in (product_index, expiry_index, ltp_index):
            continue

        for tr in table.find_all("tr"):
            if tr is header_row:
                continue

            cells = tr.find_all("td")
            if not cells:
                continue

            values = [
                re.sub(r"\s+", " ", cell.get_text(" ", strip=True))
                for cell in cells
            ]

            if max(product_index, expiry_index, ltp_index) >= len(values):
                continue

            product = values[product_index]
            if "turmeric" not in product.lower():
                continue

            # Prefer the Farmer Polished futures product, not spot quotes
            # or other turmeric grades.
            if "farmer polished" not in product.lower():
                continue

            expiry_text = values[expiry_index]
            expiry = None

            for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    expiry = datetime.strptime(
                        expiry_text, fmt
                    ).date()
                    break
                except ValueError:
                    pass

            if expiry is None or expiry < today:
                continue

            price = parse_number(values[ltp_index])
            if price is None or price <= 0:
                continue

            contracts.append({
                "expiry": expiry,
                "price": price,
                "product": product,
            })

    if not contracts:
        raise RuntimeError(
            "No valid Farmer Polished turmeric futures quote found "
            "in the NCDEX live-quotes table. The page layout may have changed."
        )

    selected = min(contracts, key=lambda item: item["expiry"])

    print(
        "NCDEX quote: "
        f"{selected['price']} INR/quintal; "
        f"contract={selected['product']}; "
        f"expiry={selected['expiry']}"
    )

    # One quintal is 100 kg, so INR/quintal == INR/100 kg.
    return selected["price"], selected["expiry"], selected["product"]


def fetch_usd_per_inr():
    response = requests.get(INR_FX_URL, headers=HEADERS, timeout=20)
    response.raise_for_status()
    data = response.json()

    if data.get("result") != "success":
        raise RuntimeError("INR-to-USD exchange-rate service returned an error")

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

    text = digits_to_ascii(
        re.sub(r"<[^>]+>", " ", response.text)
    )
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

        raw = re.sub(r"[,٬.]", "", match.group(1))
        rate = int(raw)

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

    # NCDEX quote is INR per quintal (100 kg).
    # Convert to INR per metric tonne, then to USD per metric tonne.
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

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    from datetime import timezone

    try:
        main()
    except Exception as exc:
        print(f"Price update failed: {exc}", file=sys.stderr)
        sys.exit(1)
