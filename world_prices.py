#!/usr/bin/env python3
"""Free world spice reference prices -> data["world"] in data.json (same job as fx_prices.py).

Free machine-readable sources, each independent (a failure only drops that source):
  * IPC (International Pepper Community): daily FOB-style pepper prices, USD per tonne.
  * Spices Board of India: "Current Market Price" table (pepper), INR per kg (also shown as USD/t).
  * Spices Board weekly "Spice Market" PDF: wholesale turmeric / ginger / chilli, INR per kg.
  * Govi Saviya (Sri Lanka): the public JSON the site itself uses, LKR per kg (ginger, turmeric, dried chilli).
  * manual_prices.json: prices typed by the owner from sources that forbid bots (e.g. VPSA).
  * armpk.pk: Pakistani mandi rates for dry red chilli and turmeric, PKR per kg (every row has its own date).
Prices stay in separate groups on purpose: an origin-market price, an export (FOB) price
and a customs unit value are three different numbers and must never be averaged together.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup

from fetch_prices import HEADERS, digits_to_ascii
from fx_prices import update_health

OUTPUT = Path("data.json")
IPC_URL = "https://www.ipcnet.org/"
SPICES_BOARD_URL = "https://www.indianspices.com/marketing/price/domestic/current-market-price.html"
WEEKLY_PAGE = "https://www.indianspices.com/weekly-price-domestic.html"
ARM_PAGES = (("فلفل قرمز خشک (کامل)", "https://armpk.pk/mandi-rates/red-chilli-whole-dry-rate-today/"),
             ("زردچوبه (کامل)", "https://armpk.pk/mandi-rates/turmeric-whole-rate-today/"))
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
            items.append({"name": cells[0], "name_fa": ipc_fa(cells[0]), "usd_ton": price})
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
        items.append({"spice": spice, "market": cells[2], "market_fa": to_fa(cells[2]), "grade": cells[4], "grade_fa": to_fa(cells[4]), "source": cells[5],
                      "inr_kg": avg, "usd_ton": round(avg * 1000 * usd_per_inr, 1)})
    if not items:
        return None
    return {"date": max(dates).isoformat(), "items": items}


ARM_RANGE = re.compile(r"(\d[\d,]*(?:\.\d+)?)(?:\s*[–-]\s*(\d[\d,]*(?:\.\d+)?))?")


def parse_arm(html: str, commodity: str) -> list[dict]:
    """Rows: Mandi | Date | Per 100 kg | Per 40 kg | Per kg ("Per kg300 – 325"). Mandi with a trailing * is an outlier."""
    items = []
    for cells in _rows(html):
        if len(cells) < 5:
            continue
        try:
            day = datetime.strptime(re.sub(r"\s+", " ", cells[1]).strip(), "%d %B %Y").date()
        except ValueError:
            continue  # header row or a non-data row
        m = ARM_RANGE.search(digits_to_ascii(cells[-1]).replace("Per kg", "").replace(",", ""))
        if not m:
            continue
        lo = float(m.group(1))
        hi = float(m.group(2) or m.group(1))
        if not (1 <= lo <= hi <= 100_000):
            continue
        items.append({"commodity": commodity, "market": cells[0].rstrip("* ").strip(), "market_fa": to_fa(cells[0].rstrip("* ").strip()), "date": day.isoformat(),
                      "kg_min": lo, "kg_max": hi, "outlier": "*" in cells[0]})
    return items


# Persian equivalents of the terms the sources publish (longest first; matched case-insensitively as whole words)
GLOSSARY = (
    ("black pepper", "فلفل سیاه"), ("white pepper", "فلفل سفید"), ("pepper", "فلفل"), ("ungarbled", "پاک‌نشده (آن‌گاربلد)"), ("garbled", "پاک‌شده (گاربلد)"), ("viet nam", "ویتنام"), ("vietnam", "ویتنام"), ("indonesia", "اندونزی"),
    ("brazil", "برزیل"), ("malaysia", "مالزی"), ("sri lanka", "سریلانکا"), ("india", "هند"), ("lampung", "لامپونگ"),
    ("muntok", "مونتوک"), ("sarawak", "ساراواک"), ("cochin", "کوچین"), ("kochi", "کوچی"), ("erode", "ایرود"),
    ("salem", "سیلم"), ("finger", "انگشتی"), ("nizamabad", "نظام‌آباد"), ("guntur", "گونتور"),
    ("malabar", "مالابار"), ("chennai", "چنای"), ("multan", "مولتان"), ("lahore", "لاهور"), ("karachi", "کراچی"),
    ("hyderabad", "حیدرآباد"), ("sukkur", "سکهر"), ("quetta", "کویته"), ("turmeric", "زردچوبه"),
    ("red chilli", "فلفل قرمز"), ("chilli", "فلفل قرمز"), ("ginger", "زنجبیل"), ("asta", "استاندارد آستا"),
    ("mandi", "منڈی (بازار عمده)"), ("dera ghazi khan", "دیره غازی خان"), ("faisalabad", "فیصل‌آباد"), ("rawalpindi", "راولپندی"), ("layyah", "لیه"),
    ("rahim yar khan", "رحیم‌یار خان"), ("bahawalnagar", "بهاولنگر"), ("g/l", "گرم بر لیتر"), ("ordinary", "معمولی"), ("dambulla", "دامبولا"), ("pettah", "پتاه"), ("kandy", "کندی"), ("keppetipola", "کپتیپولا"), ("peliyagoda", "پلیاگودا"), ("dried chillies", "فلفل قرمز خشک"), ("imported", "وارداتی"), ("whole", "کامل"), ("dry", "خشک"),
)


def ipc_fa(name: str) -> str:
    """'Viet Nam - Black Pepper 500 g/l' -> 'فلفل سیاه ویتنام | ۵۰۰ گرم بر لیتر' (digits stay ASCII here, notify.py/index.html localise them)."""
    low = name.lower()
    country = next((fa for en, fa in GLOSSARY if en in ("viet nam", "indonesia", "brazil", "malaysia") and en in low), "")
    spec = []
    if "asta" in low:
        spec.append("استاندارد آستا" + (f" {m.group(1)}" if (m := re.search(r"asta\s*(\d+)", low)) else ""))
    if m := re.search(r"(\d+)\s*g/l", low):
        spec.append(f"{m.group(1)} گرم بر لیتر")
    return f"فلفل {'سفید' if 'white' in low else 'سیاه'} {country}".strip() + "".join(f" | {x}" for x in spec)


def to_fa(text: str) -> str:
    """'Brazil Black - Pepper ASTA 570' -> Persian words where known; unknown words stay as published."""
    out = text
    for en, fa in GLOSSARY:
        out = re.sub(rf"(?<![A-Za-z]){re.escape(en)}(?![A-Za-z])", fa, out, flags=re.I)
    return out


# weekly PDF rows carry no spice column in the extracted text, only a market label: only unambiguous labels are mapped
WEEKLY_MARKETS = {"CHILLI GUNTUR": ("فلفل قرمز", "Guntur"), "ERODE": ("زردچوبه", "Erode"), "SALEM": ("زردچوبه", "Salem"),
                  "FINGER NIZAMABAD": ("زردچوبه", "Nizamabad (Finger)")}
WEEKLY_ROW = re.compile(r"^(" + "|".join(WEEKLY_MARKETS) + r")" + r"\s+(-|\d+(?:\.\d+)?)" * 4 + r"\s*$", re.I)


def parse_spices_weekly(text: str) -> dict | None:
    """Spices Board weekly PDF, INR/kg: 'ERODE 235.00 200.00 190.00 165.00' = this week, last week, last month, same week last year.
    shortcut: only the 4 labels in WEEKLY_MARKETS are read (the text has no spice column); add a label there to read more."""
    d = re.search(r"WEEK ENDING\s*(\d{2})\.(\d{2})\.(\d{4})", text, re.I)
    items = []
    for line in text.splitlines():
        m = WEEKLY_ROW.match(re.sub(r"\s+", " ", line.strip()))
        if not m:
            continue
        spice, market = WEEKLY_MARKETS[m.group(1).upper()]
        v = [None if x == "-" else float(x) for x in m.groups()[1:]]
        if v[0] is None:
            continue
        items.append({"spice": spice, "market": market, "market_fa": to_fa(market), "inr_kg": v[0],
                      "prev_week": v[1], "prev_month": v[2], "last_year": v[3]})
    if not d or not items:
        return None
    return {"date": f"{d.group(3)}-{d.group(2)}-{d.group(1)}", "items": items}


GOVI_URL = "https://govisaviya.lk/api/prices/homepage?more=1"  # robots.txt allows it; it is the JSON the site's own pages load
GOVI_ITEMS = {"Ginger": "زنجبیل", "Turmeric": "زردچوبه", "Dried Chillies (Imported)": "فلفل قرمز خشک (وارداتی)"}


def parse_govi(js: dict) -> dict | None:
    """-> items with lkr_kg (wholesale, Sri Lankan rupees per kg), min/max, previous day's price and the best market."""
    items = []
    for r in js.get("data", []):
        if r.get("item") in GOVI_ITEMS and r.get("latestPrice") and r.get("unit") == "kg":
            items.append({"spice": GOVI_ITEMS[r["item"]], "name": r["item"], "lkr_kg": r["latestPrice"], "min": r.get("latestMin"),
                          "max": r.get("latestMax"), "prev": r.get("previousPrice"), "market": r.get("bestMarket", ""),
                          "market_fa": to_fa(r.get("bestMarket", "")), "date": r["latestDate"]})
    return {"date": max(i["date"] for i in items), "items": items} if items else None


def read_manual(path: str = "manual_prices.json") -> dict | None:
    """Prices the owner types in from sources that forbid bots: {"items":[{"source","name","price","unit","date","note"}]}."""
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError):
        return None
    items = [{**i, "name_fa": to_fa(i["name"])} for i in items if i.get("name") and i.get("price") and i.get("date")]
    return {"date": max(i["date"] for i in items), "items": items} if items else None


def _pdf_day(url: str) -> date:
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", unquote(url))
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else date.min


def fetch_spices_weekly() -> dict | None:
    from pypdf import PdfReader  # imported here so the CI test job (no pypdf) can still import this module

    links = {urljoin(WEEKLY_PAGE, a["href"]) for a in BeautifulSoup(get(WEEKLY_PAGE), "html.parser").find_all("a", href=True)
             if a["href"].lower().endswith(".pdf")}
    for url in sorted(links, key=_pdf_day, reverse=True)[:3]:  # newest first; the first PDF that holds the table wins
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        got = parse_spices_weekly("\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(r.content)).pages))
        if got:
            return {**got, "url": url}
    return None


def fetch_arm() -> dict | None:
    items = []
    for name, url in ARM_PAGES:
        try:
            items += parse_arm(get(url), name)
        except Exception as e:  # one page down must not drop the other
            print(f"world arm {url}: {type(e).__name__}", file=sys.stderr)
    return {"date": max(i["date"] for i in items), "items": items} if items else None


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
        ("spices_weekly", "Spices Board (PDF هفتگی)", fetch_spices_weekly),
        ("arm_pk", "ARM پاکستان", fetch_arm),
        ("govi", "Govi Saviya سریلانکا", lambda: parse_govi(json.loads(get(GOVI_URL)))),
        ("manual", "قیمت‌های دستی", read_manual),
    ):
        try:
            got = fn()
        except Exception as e:
            print(f"world {key}: {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}", file=sys.stderr)
            got = None
        print(f"world {key}: {len(got['items']) if got else 'none'} items, date {got['date'] if got else '-'}")
        if key != "manual":  # a file typed by hand is not a source that can "go down"
            alerts += update_health(health, f"world|{key}", label, bool(got))  # health = the source answered, even if its prices are old
        if got and (got := fresh_only(got)):
            world[key] = got
    if world:
        data["world"] = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), **world}
    return alerts


MAX_AGE_DAYS = 3  # prices older than this are of no use to the owner: dropped (the source itself still counts as "up" in the health check)


def fresh_only(got: dict, today: date | None = None) -> dict | None:
    today = today or date.today()
    ok = lambda d: (today - date.fromisoformat(d)).days <= MAX_AGE_DAYS  # noqa: E731
    items = [i for i in got["items"] if ok(i.get("date") or got["date"])]
    return {**got, "items": items, "date": max(i.get("date") or got["date"] for i in items)} if items else None


def main() -> None:
    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    health = data.setdefault("fx_health", {})
    alerts = run(data, health)
    if alerts:  # transition events only (miss streak / recovery), same rule as fx_prices.py
        data["alerts"] = data.get("alerts", []) + alerts
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
