#!/usr/bin/env python3
"""Send Bazarban prices (origin / with costs / detailed) to Telegram and Rubika.

Reads data.json (prices) + config.json (loads and costs; same model as index.html).
Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, RUBIKA_BOT_TOKEN, RUBIKA_CHAT_ID (each pair optional).
"""
from __future__ import annotations

import json
import os
import sys

import requests

PRICE_FIELD = {"t": "usd_per_ton", "b": "pepper_usd_per_ton"}
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(n: float, d: int = 0) -> str:
    return f"{n:,.{d}f}".replace(",", "٬").replace(".", "٫").translate(FA)


def model(p: dict, price: float, rate: float) -> dict:
    wt = p["tons"] * p["cnt"]
    goods = price * wt
    extra = goods * p["opct"] / 100 + p["ofix"] * p["cnt"] if p["oon"] else 0
    origin = goods + extra
    base = origin * rate
    parts = [("قیمت جهانی کالا", goods * rate), ("هزینه‌های مبدأ", extra * rate)]
    for c in p["costs"]:
        v = 0 if not c["on"] else (
            base * c["v"] / 100 if c["t"] == "pct" else
            c["v"] * p["cnt"] if c["t"] == "cont" else
            c["v"] * wt if c["t"] == "ton" else
            c["v"] * wt * 1000 if c["t"] == "kg" else c["v"])
        parts.append((c["n"] or "—", v))
    return {"wt": wt, "goods": goods, "origin": origin, "base": base, "parts": parts,
            "total": sum(v for _, v in parts)}


def build_messages(data: dict, cfg: dict) -> list[str]:
    rate = data["usd_toman"]
    origin, final, detail = [], [], []
    for k, p in cfg["products"].items():
        price = data.get(PRICE_FIELD[k])
        if not price:
            continue
        m = model(p, price, rate)
        kg = m["wt"] * 1000
        origin.append(
            f"{p['name']}\nقیمت جهانی: {fa(price)} دلار هر تن\n"
            f"قیمت مبدأ (با هزینه‌های مبدأ): {fa(m['origin'] / m['wt'])} دلار هر تن\n"
            f"= {fa(m['base'] / kg)} تومان هر کیلو")
        final.append(
            f"{p['name']}\nهر کیلو: {fa(m['total'] / kg)} تومان\nهر تن: {fa(m['total'] / m['wt'])} تومان\n"
            f"کل محموله ({fa(m['wt'])} تن): {fa(m['total'])} تومان\nافزایش نسبت به مبدأ: {fa((m['total'] / m['base'] - 1) * 100, 1)}٪")
        lines = [f"{n}\n{fa(v)} تومان · {fa(v / kg)} تومان هر کیلو · {fa(v / m['total'] * 100, 1 if v else 0)}٪"
                 for n, v in m["parts"]]
        detail.append(p["name"] + "\n\n" + "\n".join(lines))
    stamp = f"\n\nنرخ دلار: {fa(rate)} تومان · {data.get('updated', '')}"
    sep = "\n\n———\n\n"
    return [t + sep.join(b) + stamp for t, b in (
        ("📍 قیمت در مبدأ\n\n", origin), ("📦 قیمت با مصارف\n\n", final), ("🧾 ریز جزئیات\n\n", detail)) if b]


def send(text: str) -> None:
    jobs = []
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        jobs.append((f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                     {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}))
    if os.environ.get("RUBIKA_BOT_TOKEN"):
        jobs.append((f"https://botapi.rubika.ir/v3/{os.environ['RUBIKA_BOT_TOKEN']}/sendMessage",
                     {"chat_id": os.environ["RUBIKA_CHAT_ID"], "text": text}))
    if not jobs:
        raise SystemExit("No bot token set (TELEGRAM_BOT_TOKEN / RUBIKA_BOT_TOKEN)")
    failed = False
    for url, body in jobs:  # one platform failing must not block the other
        try:
            requests.post(url, json=body, timeout=30).raise_for_status()
        except Exception as e:
            failed = True
            print(f"send failed ({url.split('/')[2]}): {type(e).__name__}", file=sys.stderr)  # URL holds the token
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    data = json.load(open("data.json", encoding="utf-8"))
    cfg = json.load(open("config.json", encoding="utf-8"))
    msgs = build_messages(data, cfg)
    for m in msgs:
        print(m, "\n") if "--dry" in sys.argv else send(m)
