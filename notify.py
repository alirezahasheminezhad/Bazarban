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
from datetime import datetime
from zoneinfo import ZoneInfo

PRICE_FIELD = {"t": "usd_per_ton", "b": "pepper_usd_per_ton"}
MIN_CHANGE = 0.01  # send automatically only when a final price moves 1% or more
STATE = "last_sent.json"
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


def build_message(data: dict, cfg: dict) -> tuple[str, dict]:
    """One neat message for all products + {product: final toman/kg} used for change detection."""
    rate = data["usd_toman"]
    tehran = datetime.fromisoformat(data["updated"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Tehran"))
    blocks, kgs = [], {}
    for k, p in cfg["products"].items():
        price = data.get(PRICE_FIELD[k])
        if not price:
            continue
        m = model(p, price, rate)
        kg = m["wt"] * 1000
        kgs[k] = m["total"] / kg
        lines = [f"• {n}: {fa(v / kg)} ت/کیلو · {fa(v / m['total'] * 100, 1)}٪" for n, v in m["parts"] if v]
        blocks.append(
            f"{p['name']}\n\n"
            f"📍 قیمت در مبدأ\n{fa(m['origin'] / m['wt'])} دلار هر تن\n{fa(m['base'] / kg)} تومان هر کیلو\n\n"
            f"📦 قیمت با مصارف\n{fa(kgs[k])} تومان هر کیلو\n"
            f"{fa(m['total'] / m['wt'])} تومان هر تن\n"
            f"کل {fa(m['wt'])} تن: {fa(m['total'])} تومان\n\n"
            f"🧾 ریز جزئیات\n" + "\n".join(lines))
    head = f"🌿 بازاربان\n💵 دلار: {fa(rate)} تومان\n🕐 {tehran.strftime('%H:%M').translate(FA)} (تهران)"
    line = "\n\n━━━━━━━━━━━━━━\n\n"
    return head + line + line.join(blocks), kgs


def changed(kgs: dict, last: dict) -> bool:
    """True if any product's final price moved by MIN_CHANGE or more since the last send."""
    return any(k not in last or abs(v / last[k] - 1) >= MIN_CHANGE for k, v in kgs.items())


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
    text, kgs = build_message(data, cfg)
    if "--dry" in sys.argv:
        print(text)
        raise SystemExit
    try:
        last = json.load(open(STATE))
    except (OSError, ValueError):
        last = {}
    if "--force" not in sys.argv and not changed(kgs, last):
        print("no significant price change; nothing sent")
        raise SystemExit(0)
    send(text)
    json.dump(kgs, open(STATE, "w"))
