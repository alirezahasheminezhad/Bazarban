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
    ("afn", "tgju.org", TGJU + "price_afn"), ("afn", "bitpin.ir", BIT + "afn/"),
    ("afn", "tabdeal.org", TAB + "afn-irr"), ("afn", "alanchand.com", ALAN + "afn"),
    ("pkr", "tgju.org", TGJU + "price_pkr"), ("pkr", "alanchand.com", ALAN + "pkr"),
    ("pkr", "arzdigital.com", ARZ + "convert/pakistani-rupee/ir-toman/"),
    ("aed", "ok-ex.io", OK), ("aed", "kifpool.me", KIF),
    ("aed", "tgju.org", TGJU + "price_aed"), ("aed", "alanchand.com", ALAN + "aed"),
    # same URL patterns as the owner's AFN/PKR references (guesses; a 404 is just skipped)
    ("usd", "bitpin.ir", BIT + "usd/"), ("aed", "bitpin.ir", BIT + "aed/"), ("pkr", "bitpin.ir", BIT + "pkr/"),
    ("usd", "tabdeal.org", TAB + "usd-irr"), ("aed", "tabdeal.org", TAB + "aed-irr"),
    ("pkr", "tabdeal.org", TAB + "pkr-irr"), ("usdt", "tabdeal.org", TAB + "usdt-irr"),
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
    return (exp(usd) * (1 - tol), exp(usd) * (1 + tol)) if exp else (1e6, 1e9)  # gold: wide


def extract(cur: str, label: str, text: str, usd: float) -> float | None:
    lo, hi = bounds(cur, usd)
    div = (10,) if cur == "gold" and label == "tgju.org" else (1,) if cur == "gold" else (1, 10)
    return find_price(text, SPEC[cur][1], lo, hi, div)


NABZESH = {"usd": "USD", "usdt": "USDT", "afn": "AFN", "pkr": "PKR", "aed": "AED", "gold": "GOLD18_MG"}


def fetch_nabzesh(cur: str, usd: float) -> float | None:
    """nabzesh.ir public API (docs: api.nabzesh.ir); the key is optional. GOLD18_MG is per milligram."""
    key = os.environ.get("NABZESH_API_KEY")
    try:
        r = requests.get(f"https://api.nabzesh.ir/v1/currencies/{NABZESH[cur]}", params={"quote": "IRT"},
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
    items, alerts = [], []
    for cur, (name, *_rest) in SPEC.items():
        nab = fetch_nabzesh(cur, usd)
        print(f"fx {cur} nabzesh.ir: {nab or 'none'}")
        vals = ([("arzdigital.com", usd)] if cur == "usd" else []) + [(s, found[(c, s)]) for c, s, _ in PLAN if c == cur and (c, s) in found]
        if nab:
            vals.append(("nabzesh.ir", nab))
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
