#!/usr/bin/env python3
"""Fetch USD, USDT, gold, AFN, PKR, AED prices (toman) from several sources and add
data["fx"] to data.json: per currency the average of the sources that agree, plus a
confidence % (agreeing sources / all sources). Runs in the same job as fetch_prices.py.
Each source is independent: one that fails or changes layout is just skipped.
"""
from __future__ import annotations

import json
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
OK, KIF, NAB = "https://ok-ex.io/live/currency/", "https://kifpool.me/live/currency", "https://nabzesh.ir/"
TGJU, ALAN, ARZ = "https://www.tgju.org/profile/", "https://alanchand.com/currencies-price/", "https://arzdigital.com/"
# (currency, source label, url). The owner's references for PKR/AFN come first; the rest are best guesses
# for the same sites. Wrong or blocked ones just log "none".
PLAN = [
    ("usd", "ok-ex.io", OK), ("usd", "kifpool.me", KIF), ("usd", "nabzesh.ir", NAB),
    ("usd", "tgju.org", TGJU + "price_dollar_rl"), ("usd", "alanchand.com", ALAN + "usd"),
    ("usdt", "arzdigital.com", ARZ + "coins/tether/"), ("usdt", "kifpool.me", KIF), ("usdt", "nabzesh.ir", NAB),
    ("gold", "tgju.org", TGJU + "geram18"), ("gold", "nabzesh.ir", NAB), ("gold", "kifpool.me", KIF),
    ("afn", "tgju.org", TGJU + "price_afn"), ("afn", "bitpin.ir", "https://bitpin.ir/academy/live/currency/afn/"),
    ("afn", "tabdeal.org", "https://tabdeal.org/convert/afn-irr"), ("afn", "alanchand.com", ALAN + "afn"),
    ("pkr", "tgju.org", TGJU + "price_pkr"), ("pkr", "alanchand.com", ALAN + "pkr"),
    ("pkr", "arzdigital.com", ARZ + "convert/pakistani-rupee/ir-toman/"),
    ("aed", "ok-ex.io", OK), ("aed", "kifpool.me", KIF), ("aed", "nabzesh.ir", NAB),
    ("aed", "tgju.org", TGJU + "price_aed"), ("aed", "alanchand.com", ALAN + "aed"),
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
    """vals: [(source, toman)]. Average of the largest group of agreeing sources + confidence %."""
    n = len(vals)
    best = max(([w for _, w in vals if abs(w / v - 1) <= tol] for _, v in vals), key=len)
    if n == 1:
        avg, conf, ok = vals[0][1], None, [True]
    elif len(best) == 1:  # nobody agrees: plain average, low confidence
        avg, conf, ok = statistics.mean(w for _, w in vals), round(100 / n), [True] * n
    else:
        med = statistics.median(best)
        avg, conf = statistics.mean(best), round(100 * len(best) / n)
        ok = [abs(w / med - 1) <= tol for _, w in vals]
    return {"avg": round(avg), "conf": conf, "n": n, "k": sum(ok),
            "sources": [{"s": s, "v": round(w), "ok": o} for (s, w), o in zip(vals, ok)]}


def bounds(cur: str, usd: float):
    _, _, exp, tol = SPEC[cur]
    return (exp(usd) * (1 - tol), exp(usd) * (1 + tol)) if exp else (1e6, 1e9)  # gold: wide


def extract(cur: str, label: str, text: str, usd: float) -> float | None:
    lo, hi = bounds(cur, usd)
    div = (10,) if cur == "gold" and label == "tgju.org" else (1,) if cur == "gold" else (1, 10)
    return find_price(text, SPEC[cur][1], lo, hi, div)


def fetch_plain(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        return to_text(r.text)
    except Exception as e:
        print(f"fx fetch {url}: {type(e).__name__}", file=sys.stderr)
        return ""


def main() -> None:
    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    usd = float(data["usd_toman"])
    urls = sorted({u for _, _, u in PLAN})
    with ThreadPoolExecutor(8) as ex:
        texts = dict(zip(urls, ex.map(fetch_plain, urls)))
    found: dict[tuple[str, str], float] = {}
    for pass_no in (1, 2):
        missing = [(c, s, u) for c, s, u in PLAN if (c, s) not in found]
        if pass_no == 2:  # JS-rendered pages: retry the failed ones in a real browser, within a time budget
            start = time.time()
            for u in sorted({u for _, _, u in missing}):
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
    items = []
    for cur, (name, *_rest) in SPEC.items():
        vals = ([("arzdigital.com", usd)] if cur == "usd" else []) + [(s, found[(c, s)]) for c, s, _ in PLAN if c == cur and (c, s) in found]
        for c, s, _ in PLAN:
            if c == cur:
                print(f"fx {cur} {s}: {found.get((c, s), 'none')}")
        if vals:
            items.append({"id": cur, "name": name, **aggregate(vals)})
    data["fx"] = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "items": items}
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data["fx"], ensure_ascii=False))


if __name__ == "__main__":
    main()
