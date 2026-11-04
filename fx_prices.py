#!/usr/bin/env python3
"""Fetch USD, USDT, gold, AFN, PKR, AED prices (toman) from several sources and add
data["fx"] to data.json: per currency the average of the sources that agree, plus a
confidence % (agreeing sources / all sources). Runs in the same job as fetch_prices.py.
Each source is independent: one that fails or changes layout is just skipped.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:  # Chrome TLS fingerprint: several Iranian exchanges reject plain python-requests (the reference MCPs do the same)
    from curl_cffi import requests as cffi
except ImportError:
    cffi = None

from fetch_prices import HEADERS, digits_to_ascii, render_html

OUTPUT = Path("data.json")
TOL = 0.02  # sources within 2% of each other "agree"
FAIL_ALERT = 3  # consecutive misses of a source that used to work before the group is told
RENDER_BUDGET_S = 180  # total time allowed for slow browser fallbacks

# currency: (name, keyword regex, expected toman given the USD rate, tolerance of that expectation)
SPEC = {
    "usd": ("دلار آمریکا", r"دلار", lambda u: u, 0.06),
    "usdt": ("تتر (USDT)", r"تتر|USDT", lambda u: u, 0.08),
    "gold": ("طلا ۱۸ عیار (هر گرم)", r"طلای?\s*18\s*عیار|گرم\s*طلای?\s*18", None, None),
    "coin": ("سکه امامی (تمام)", r"سکه", lambda u: None, None),
    "afn": ("افغانی افغانستان", r"افغانی", lambda u: u / 70, 0.25),
    "pkr": ("روپیه پاکستان", r"روپیه", lambda u: u / 280, 0.15),
    "aed": ("درهم امارات", r"درهم", lambda u: u / 3.6725, 0.08),
}
OK, KIF = "https://ok-ex.io/live/currency/", "https://kifpool.me/live/currency"
BIT, TAB = "https://bitpin.ir/academy/live/currency/", "https://tabdeal.org/convert/"
TGJU, ALAN, ARZ = "https://www.tgju.org/profile/", "https://alanchand.com/currencies-price/", "https://arzdigital.com/"
# (currency, source label, url). The owner's references for PKR/AFN come first; the rest are best guesses
# for the same sites. Wrong or blocked ones just log "none".
PLAN = [
    ("usd", "ok-ex.io", OK), ("usd", "kifpool.me", KIF),
    ("usd", "tgju.org", TGJU + "price_dollar_rl"), ("usd", "alanchand.com", ALAN + "usd"),
    ("usdt", "arzdigital.com", ARZ + "coins/tether/"), ("usdt", "kifpool.me", KIF),
    ("gold", "tgju.org", TGJU + "geram18"), ("gold", "kifpool.me", KIF),
    ("coin", "tgju.org", TGJU + "sekee"),
    ("afn", "tgju.org", TGJU + "price_afn"), ("afn", "bitpin.ir", BIT + "afn/"),
    ("afn", "tabdeal.org", TAB + "afn-irr"), ("afn", "alanchand.com", ALAN + "afn"),
    ("pkr", "tgju.org", TGJU + "price_pkr"), ("pkr", "alanchand.com", ALAN + "pkr"),
    ("pkr", "arzdigital.com", ARZ + "convert/pakistani-rupee/ir-toman/"),
    ("aed", "ok-ex.io", OK), ("aed", "kifpool.me", KIF),
    ("aed", "tgju.org", TGJU + "price_aed"), ("aed", "alanchand.com", ALAN + "aed"),
    # same URL patterns as the owner's AFN/PKR references (guesses; a 404 is just skipped)
    ("usd", "bitpin.ir", BIT + "usd/"), ("aed", "bitpin.ir", BIT + "aed/"), ("pkr", "bitpin.ir", BIT + "pkr/"),
    ("usd", "tabdeal.org", TAB + "usd-irr"), ("aed", "tabdeal.org", TAB + "aed-irr"),
    ("pkr", "tabdeal.org", TAB + "pkr-irr"),
    ("usd", "arzdigital.com", ARZ + "convert/united-states-dollar/ir-toman/"),
    ("afn", "arzdigital.com", ARZ + "convert/afghan-afghani/ir-toman/"),
    ("aed", "arzdigital.com", ARZ + "convert/united-arab-emirates-dirham/ir-toman/"),
]
NUM = re.compile(r"\d[\d,٬]*(?:[.٫]\d+)?")


def to_text(html: str) -> str:
    return re.sub(r"\s+", " ", digits_to_ascii(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)))


def find_price(text: str, kw: str, lo: float, hi: float, divisors=(1, 10)) -> float | None:
    """First number after the keyword that lands in [lo, hi] as toman (a rial figure is /10)."""
    for m in re.finditer(kw, text):
        for n in NUM.findall(text[m.end():m.end() + 150]):
            v = float(n.replace(",", "").replace("٬", "").replace("٫", "."))
            for d in divisors:
                if lo <= v / d <= hi:
                    return v / d
    return None


def aggregate(vals: list[tuple[str, float]], tol: float = TOL) -> dict:
    """vals: [(source, toman)]. Average of all sources; confidence % = share of sources agreeing within tol."""
    n = len(vals)
    best = max(([w for _, w in vals if abs(w / v - 1) <= tol] for _, v in vals), key=len)  # largest agreeing group
    if n == 1:
        conf, ok = None, [True]
    elif len(best) == 1:  # nobody agrees
        conf, ok = round(100 / n), [True] * n
    else:
        med = statistics.median(best)
        conf, ok = round(100 * len(best) / n), [abs(w / med - 1) <= tol for _, w in vals]
    return {"avg": round(statistics.mean(w for _, w in vals)), "conf": conf, "n": n, "k": sum(ok),
            "sources": [{"s": s, "v": round(w), "ok": o} for (s, w), o in zip(vals, ok)]}


def bounds(cur: str, usd: float):
    _, _, exp, tol = SPEC[cur]
    if cur == "coin":
        return 5e7, 1e10  # a full Emami coin is ~10x a gram of 18k gold plus the market bubble
    return (exp(usd) * (1 - tol), exp(usd) * (1 + tol)) if exp else (1e6, 1e9)  # gold: wide


def extract(cur: str, label: str, text: str, usd: float) -> float | None:
    lo, hi = bounds(cur, usd)
    wide = cur in ("gold", "coin")  # bounds too wide to tell rial from toman, so the unit is fixed per site
    div = (10,) if wide and label == "tgju.org" else (1,) if wide else (1, 10)
    return find_price(text, SPEC[cur][1], lo, hi, div)


NABZESH = {"usd": "USD", "usdt": "USDT", "afn": "AFN", "pkr": "PKR", "aed": "AED", "gold": "GOLD18_MG", "coin": None}


def fetch_nabzesh(cur: str, usd: float) -> float | None:
    """nabzesh.ir public API (docs: api.nabzesh.ir); the key is optional. GOLD18_MG is per milligram."""
    key = os.environ.get("NABZESH_API_KEY")
    ticker = NABZESH.get(cur)
    try:
        if cur == "coin":  # find the Emami coin ticker by its Persian name
            rows = requests.get("https://api.nabzesh.ir/v1/currencies", params={"search": "امامی"}, headers=HEADERS, timeout=20).json()["data"]
            ticker = next((c["ticker"] for c in rows if "امامی" in c["name"]["fa"]
                           and not any(w in c["name"]["fa"] for w in ("نیم", "ربع", "گرمی"))), None)
            if not ticker:
                return None
        r = requests.get(f"https://api.nabzesh.ir/v1/currencies/{ticker}", params={"quote": "IRT"},
                         headers={**HEADERS, **({"x-api-key": key} if key else {})}, timeout=20)
        r.raise_for_status()
        p = r.json()["price"]
        if p.get("isStale"):
            return None
        v = float(p["price"])
    except Exception as e:
        print(f"fx nabzesh {cur}: {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}", file=sys.stderr)
        return None
    lo, hi = bounds(cur, usd)
    return next((x for x in ((v, v * 1000) if cur == "gold" else (v,)) if lo <= x <= hi), None)



_JSON: dict = {}


def jget(url: str, site: str | None = None, params: dict | None = None):
    key = (url, json.dumps(params, sort_keys=True))
    if key not in _JSON:
        _JSON[key] = _jget(url, site, params)
    return _JSON[key]


def _jget(url: str, site: str | None, params: dict | None):
    headers = {"Accept": "application/json", **({"Referer": site + "/"} if site else {})}
    if cffi:
        r = cffi.get(url, params=params, headers=headers, impersonate="chrome", timeout=20)
    else:
        r = requests.get(url, params=params, headers={**HEADERS, **headers}, timeout=20)
    r.raise_for_status()
    return r.json()


def _num(v) -> float | None:
    try:
        n = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _mid(a, b) -> float | None:
    a, b = _num(a), _num(b)
    return (a + b) / 2 if a and b else a or b


def _wallex(js):
    return next((_num(m["stats"]["lastPrice"]) for m in js["result"]["symbols"].values()
                 if m.get("baseAsset") == "USDT" and m.get("quoteAsset") == "TMN"), None)


def _exir(js):
    return _num(js["usdt-irt"]["last"])


def _tabdeal(js):
    return next((_num(q["IRT"]["price"]) for c, q in js["currencies"].items() if c.upper() == "USDT" and q.get("IRT")), None)


def _omp(js):  # Rial
    return next((_num(m["price"]) for m in js["data"] if m.get("baseCurrencyId", "").upper() == "USDT"
                 and m.get("quoteCurrencyId") == "IRR" and m.get("status") == "LISTED"), None)


def _ramz(js):  # Rial
    return next((_num(((p.get("financial") or {}).get("last24h") or {}).get("close")) for p in js["data"]
                 if p["quote_currency_symbol"]["en"] == "irr" and p["base_currency_symbol"]["en"].split()[0].upper() == "USDT"), None)


def _bitpin(js):
    return next((_num(t["price"]) for t in js if t.get("symbol") == "USDT_IRT"), None)


def _okex(js):
    return next((_mid(t.get("buyAmt"), t.get("sellAmt")) for t in js if t.get("asset") == "USDT"), None)


def _milli(js):  # Rial per milligram -> toman per gram
    p = _num(js["data"]["price18"])
    return p * 100 if p else None


def _wallgold(js):
    side = {r.get("side"): r.get("price") for r in js["result"] if r.get("symbol") == "GLD_18C_750TMN"}
    return _mid(side.get("buy"), side.get("sell"))


def _melligold(js):
    return _mid(js["data"]["price_buy"], js["data"]["price_sell"])


def _daric(js):
    return _mid(js["Data"]["BestSellPrice"], js["Data"]["BestBuyPrice"])


def _guard_cur(code):
    return lambda js: next((_num(i["sell_price"]["value"]) for i in js["data"]["currency_prices"]["items"] if i.get("code") == code), None)


def _guard_gold(slug):
    return lambda js: next((_num(i["price"]) for i in js["data"]["gold_prices"]["items"] if i.get("slug") == slug), None)


def _guard_usdt(js):
    return next((_num(i["price_toman"]) for i in js["data"]["crypto_prices"]["items"] if i.get("symbol") == "USDT"), None)


GUARD = "https://app.guardnet.qzz.io/api/prices"  # the JSON behind app.guardnet.qzz.io (toman)
# (currency, label, url, site, params, parser): public endpoints taken from the iran-crypto-mcp / iran-gold-mcp sources
API = [
    ("usdt", "wallex.ir", "https://api.wallex.ir/v1/markets", None, None, _wallex),
    ("usdt", "exir.io", "https://api.exir.io/v2/ticker/all", None, None, _exir),
    ("usdt", "tabdeal.org", "https://api-web.tabdeal.org/r/plots/currencies/dynamic-info/", None, None, _tabdeal),
    ("usdt", "ompfinex.com", "https://api.ompfinex.com/v2/market", None, None, _omp),
    ("usdt", "ramzinex.com", "https://api.ramzinex.ir/exchange/api/v1.0/exchange/pairs", None, None, _ramz),
    ("usdt", "bitpin.ir", "https://api.bitpin.ir/api/v1/mkt/tickers/", None, None, _bitpin),
    ("usdt", "ok-ex.io", "https://azapi.ok-ex.io/api/v1/asset/otc/tickers", None, None, _okex),
    *[(c, "guardnet.qzz.io", GUARD, None, None, _guard_cur(c)) for c in ("usd", "aed", "afn", "pkr")],
    ("usdt", "guardnet.qzz.io", GUARD, None, None, _guard_usdt),
    ("gold", "guardnet.qzz.io", GUARD, None, None, _guard_gold("18ayar")),
    ("coin", "guardnet.qzz.io", GUARD, None, None, _guard_gold("sekkeh")),
    ("gold", "milli.gold", "https://milli.gold/api/v1/public/milli-price/external", "https://milli.gold", None, _milli),
    ("gold", "wallgold.ir", "https://api.wallgold.ir/api/v1/prices", "https://wallgold.ir", None, _wallgold),
    ("gold", "melligold.com", "https://melligold.com/api/v1/exchange/buy-sell-price/", "https://melligold.com",
     {"symbol": "XAU18", "format": "json"}, _melligold),
    ("gold", "daric.gold", "https://apisc.daric.gold/loan/api/v1/User/Collateral/GetGoldlPrice", "https://daric.gold", None, _daric),
]


def fetch_api(entry, usd: float) -> float | None:
    cur, label, url, site, params, parse = entry
    try:
        v = parse(jget(url, site, params))
    except Exception as e:
        print(f"fx api {cur} {label}: {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}", file=sys.stderr)
        return None
    lo, hi = bounds(cur, usd)
    return next((v / d for d in ((1,) if cur in ("gold", "coin") else (1, 10)) if v and lo <= v / d <= hi), None)  # USDT: rial feeds /10


DEAD: set[str] = set()  # urls answering 404: no point rendering them in a browser


def fetch_plain(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        return to_text(r.text)
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", "")
        if status == 404:
            DEAD.add(url)
        print(f"fx fetch {url}: {type(e).__name__} {status}", file=sys.stderr)
        return ""


def update_health(health: dict, key: str, text: str, got: bool) -> list[str]:
    """Track consecutive misses. Only a source that has worked before can raise an alert (guessed URLs stay quiet)."""
    h = health.setdefault(key, {"worked": False, "fails": 0})
    alerts = []
    if got:
        if h["fails"] >= FAIL_ALERT:
            alerts.append(f"✅ {text}: دوباره جواب داد")
        h.update(worked=True, fails=0)
    else:
        h["fails"] += 1
        if h["worked"] and h["fails"] == FAIL_ALERT:
            alerts.append(f"⚠️ {text}: چند بار پیاپی قیمت نداد (دسترسی قطع شده یا ظاهر صفحه عوض شده)")
    return alerts


def main() -> None:
    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    health = data.setdefault("fx_health", {})
    try:
        alerts = run(data, health)
    except Exception as e:  # keep the spice update; tell the group after repeated crashes
        print(f"fx crashed: {type(e).__name__}: {e}", file=sys.stderr)
        health.setdefault("_crash", {"worked": True, "fails": 0})
        alerts = update_health(health, "_crash", "بخش ارز و طلا (خطای برنامه)", False)
        data["alerts"] = alerts
        OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
    alerts += update_health(health, "_crash", "بخش ارز و طلا", True)
    if alerts:
        data["alerts"] = alerts
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(data: dict, health: dict) -> list[str]:
    usd = float(data["usd_toman"])
    urls = sorted({u for _, _, u in PLAN})
    with ThreadPoolExecutor(8) as ex:
        texts = dict(zip(urls, ex.map(fetch_plain, urls)))
    found: dict[tuple[str, str], float] = {}
    for pass_no in (1, 2):
        missing = [(c, s, u) for c, s, u in PLAN if (c, s) not in found]
        if pass_no == 2:  # JS-rendered pages: retry the failed ones in a real browser, within a time budget
            start = time.time()
            for u in sorted({u for _, _, u in missing} - DEAD):
                if time.time() - start > RENDER_BUDGET_S:
                    break
                try:
                    texts[u] = to_text(render_html(u))
                except Exception as e:
                    print(f"fx render {u}: {type(e).__name__}", file=sys.stderr)
        for c, s, u in missing:
            v = extract(c, s, texts.get(u, ""), usd)
            if v:
                found[(c, s)] = v
    try:
        jget(GUARD)  # one shared reply for 7 entries: fetch it once before the parallel calls
    except Exception:
        pass  # each entry reports its own failure below
    with ThreadPoolExecutor(8) as ex:
        api_vals = list(ex.map(lambda e: fetch_api(e, usd), API))
    api_found = {(e[0], e[1]): v for e, v in zip(API, api_vals) if v}
    for e, v in zip(API, api_vals):
        print(f"fx {e[0]} {e[1]}: {v or 'none'}")
    items, alerts = [], []
    for cur, (name, *_rest) in SPEC.items():
        nab = fetch_nabzesh(cur, usd)
        print(f"fx {cur} nabzesh.ir: {nab or 'none'}")
        vals = ([("arzdigital.com", usd)] if cur == "usd" else []) + [(s, found[(c, s)]) for c, s, _ in PLAN if c == cur and (c, s) in found]
        vals += [(lbl, v) for (c, lbl), v in api_found.items() if c == cur]
        if nab:
            vals.append(("nabzesh.ir", nab))
        for e in API:
            if e[0] == cur:
                alerts += update_health(health, f"{cur}|{e[1]}", f"{e[1]} برای {name}", (e[0], e[1]) in api_found)
        alerts += update_health(health, f"{cur}|nabzesh.ir", f"nabzesh.ir برای {name}", bool(nab))
        for c, s_, _ in PLAN:
            if c == cur:
                alerts += update_health(health, f"{cur}|{s_}", f"{s_} برای {name}", (c, s_) in found)
        for c, s, _ in PLAN:
            if c == cur:
                print(f"fx {cur} {s}: {found.get((c, s), 'none')}")
        if vals:
            items.append({"id": cur, "name": name, **aggregate(vals)})
    data["fx"] = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "items": items}
    print(json.dumps(data["fx"], ensure_ascii=False))
    return alerts


if __name__ == "__main__":
    main()
