#!/usr/bin/env python3
"""Free world spice reference prices -> data["world"] in data.json (same job as fx_prices.py).

Two machine-readable free sources, each independent (a failure only drops that source):
  * IPC (International Pepper Community): daily FOB-style pepper prices, USD per tonne.
  * Spices Board of India: "Current Market Price" table, INR per kg (also shown as USD/t).
Prices stay in separate groups on purpose: an origin-market price, an export (FOB) price
and a customs unit value are three different numbers and must never be averaged together.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from fetch_prices import HEADERS, digits_to_ascii
from fx_prices import update_health

OUTPUT = Path("data.json")
IPC_URL = "https://www.ipcnet.org/"
SPICES_BOARD_URL = "https://www.indianspices.com/marketing/price/domestic/current-market-price.html"
SPICES = {"turmeric": "زردچوبه", "ginger": "زنجبیل", "pepper": "فلفل سیاه", "chilli": "فلفل قرمز", "chili": "فلفل قرمز"}
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _num(text: str) -> float | None:
    m = NUM.search(digits_to_ascii(text))
    return float(m.group(0).replace(",", "")) if m else None


def _rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    return [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])] for tr in soup.find_all("tr")]


def parse_ipc(html: str) -> dict | None:
    """-> {"date": "2026-11-20", "items": [{"name": "Viet Nam - Black Pepper 500 g/l", "usd_ton": 5700}]}"""
    text = re.sub(r"\s+", " ", digits_to_ascii(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)))
    d = re.search(r"DAILY PRICES\s*(\d{2})-(\d{2})-(\d{4})", text, re.I)
    items = []
    for cells in _rows(html):
        # "Brazil Black - Pepper ASTA 570" is how the site really labels one of the rows: keep the label as published
        if len(cells) < 2 or "pepper" not in cells[0].lower():
            continue
        price = next((v for v in map(_num, cells[1:]) if v and 500 <= v <= 50_000), None)  # skips the "% change" cell
        if price:
            items.append({"name": cells[0], "usd_ton": price})
    if not d or not items:
        return None
    return {"date": f"{d.group(3)}-{d.group(2)}-{d.group(1)}", "items": items}


def parse_spices_board(html: str, usd_per_inr: float) -> dict | None:
    """Table: Date | Spice | Market Centre | State | Grade | Source | Min | Max | Avg (INR/kg; min/max are often blank)."""
    items, dates = [], []
    for cells in _rows(html):
        if len(cells) < 9:
            continue
        date_m = re.match(r"(\d{2})-([A-Za-z]{3})-(\d{4})", cells[0])
        spice = next((fa for key, fa in SPICES.items() if key in cells[1].lower()), None)
        avg = _num(cells[8]) or _num(cells[7]) or _num(cells[6])
        if not (date_m and spice and avg and 1 <= avg <= 100_000):
            continue
        dates.append(datetime.strptime(cells[0][:11], "%d-%b-%Y").date())
        items.append({"spice": spice, "market": cells[2], "grade": cells[4], "source": cells[5],
                      "inr_kg": avg, "usd_ton": round(avg * 1000 * usd_per_inr, 1)})
    if not items:
        return None
    return {"date": max(dates).isoformat(), "items": items}


def get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def run(data: dict, health: dict) -> list[str]:
    usd_per_inr = float(data.get("inr_to_usd") or 0)
    world, alerts = {}, []
    for key, label, fn in (
        ("ipc", "IPC (قیمت جهانی فلفل)", lambda: parse_ipc(get(IPC_URL))),
        ("spices_board", "Spices Board هند", lambda: parse_spices_board(get(SPICES_BOARD_URL), usd_per_inr) if usd_per_inr else None),
    ):
        try:
            got = fn()
        except Exception as e:
            print(f"world {key}: {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}", file=sys.stderr)
            got = None
        print(f"world {key}: {len(got['items']) if got else 'none'} items, date {got['date'] if got else '-'}")
        if got:
            world[key] = got
        alerts += update_health(health, f"world|{key}", label, bool(got))
    if world:
        data["world"] = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), **world}
    return alerts


def main() -> None:
    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    health = data.setdefault("fx_health", {})
    alerts = run(data, health)
    if alerts:  # transition events only (miss streak / recovery), same rule as fx_prices.py
        data["alerts"] = data.get("alerts", []) + alerts
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
