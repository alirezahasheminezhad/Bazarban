#!/usr/bin/env python3
"""Send Bazarban prices (origin / with costs / detailed) to Telegram and Rubika.

Reads data.json (prices) + config.json (loads and costs; same model as index.html).
Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, RUBIKA_BOT_TOKEN, RUBIKA_CHAT_ID (each pair optional).
"""
from __future__ import annotations

import json
import os
import time
import sys

import requests
from datetime import datetime
from zoneinfo import ZoneInfo

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


def num(n: float, d: int = 2) -> str:
    """Persian number with up to d decimals, trailing zeros trimmed (25.0 -> ۲۵)."""
    t = f"{n:,.{d}f}"
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t.replace(",", "٬").replace(".", "٫").translate(FA)


def build_message(data: dict, cfg: dict) -> str:
    """One message, one block per product, in the format the owner specified."""
    rate = data["usd_toman"]
    line = "━" * 20
    blocks = []
    for k, p in cfg["products"].items():
        price = data.get(PRICE_FIELD[k])
        if not price:
            continue
        m = model(p, price, rate)
        kg = m["wt"] * 1000
        world_ton = price * rate
        world_total = m["goods"] * rate
        costs = m["total"] - world_total  # every enabled cost, as on the site
        pct = costs / world_total * 100 if world_total else 0
        world_kg = round(world_ton / 1000)
        # final prices derive from the rounded per-kg figures, like the owner's example
        cost_kg = round(costs / kg)
        final_kg = world_kg + cost_kg
        w = f"{num(m['wt'])} تنی"
        blocks.append("\n".join([
            f"🌿 گزارش قیمت {p['name']} هندی", line,
            "🌍 ۱. قیمت جهانی | بدون احتساب مصارف",
            f"💵 قیمت هر تن: {fa(price)} دلار",
            f"⚖️ قیمت هر کیلو: {fa(world_kg)} تومان",
            f"💰 قیمت هر تن: {fa(world_ton)} تومان",
            f"📦 ارزش محموله {w}",
            f"🔹 وزن کل: {fa(kg)} کیلوگرم",
            f"💰 مبلغ کل: {fa(world_total)} تومان", line,
            f"📦 ۲. قیمت نهایی | با احتساب {num(pct, 1)}٪ مصارف",
            f"📊 درصد مصارف: {num(pct, 1)}٪",
            f"➕ هزینه مصارف هر کیلو: {fa(cost_kg)} تومان",
            f"⚖️ قیمت نهایی هر کیلو: {fa(final_kg)} تومان",
            f"💰 قیمت نهایی هر تن: {fa(final_kg * 1000)} تومان",
            f"🚛 ارزش محموله {w} با احتساب مصارف",
            f"🔹 وزن کل: {fa(kg)} کیلوگرم",
            f"💰 مبلغ کل: {fa(final_kg * kg)} تومان", line]))
    return "\n\n".join(blocks)


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
        host = url.split("/")[2]  # the URL holds the token, never print it
        for attempt in range(3):  # Rubika sometimes resets the connection
            try:
                r = requests.post(url, json=body, timeout=30)
                r.raise_for_status()
                # Rubika answers HTTP 200 even on errors, e.g. {"status":"INVALID_INPUT"}
                if "rubika" in host and r.json().get("status") != "OK":
                    raise RuntimeError(r.text[:200])
                print(f"sent to {host}")
                break
            except Exception as e:
                print(f"send failed ({host}, try {attempt + 1}): {type(e).__name__}: {e if 'rubika' in host else ''}", file=sys.stderr)
                time.sleep(3)
        else:
            failed = True
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    data = json.load(open("data.json", encoding="utf-8"))
    cfg = json.load(open("config.json", encoding="utf-8"))
    text = build_message(data, cfg)
    if "--dry" in sys.argv:
        print(text)
        raise SystemExit
    send(text)
