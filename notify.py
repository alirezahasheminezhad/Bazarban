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
from datetime import date, datetime
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


LINE = "━" * 20


def sections(p: dict, price: float, rate: float) -> list[str]:
    """The owner's two-part report body: world price, then final price with costs (shared by every report)."""
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
    return [
        "🌍 ۱. قیمت جهانی | بدون احتساب مصارف",
        f"💵 قیمت هر تن: {fa(price)} دلار",
        f"⚖️ قیمت هر کیلو: {fa(world_kg)} تومان",
        f"💰 قیمت هر تن: {fa(world_ton)} تومان",
        f"📦 ارزش محموله {w}",
        f"🔹 وزن کل: {fa(kg)} کیلوگرم",
        f"💰 مبلغ کل: {fa(world_total)} تومان", LINE,
        f"📦 ۲. قیمت نهایی | با احتساب {num(pct, 1)}٪ مصارف",
        f"📊 درصد مصارف: {num(pct, 1)}٪",
        f"➕ هزینه مصارف هر کیلو: {fa(cost_kg)} تومان",
        f"⚖️ قیمت نهایی هر کیلو: {fa(final_kg)} تومان",
        f"💰 قیمت نهایی هر تن: {fa(final_kg * 1000)} تومان",
        f"🚛 ارزش محموله {w} با احتساب مصارف",
        f"🔹 وزن کل: {fa(kg)} کیلوگرم",
        f"💰 مبلغ کل: {fa(final_kg * kg)} تومان", LINE]


def build_message(data: dict, cfg: dict) -> str:
    """One message, one block per product, in the format the owner specified."""
    rate = data["usd_toman"]
    blocks = []
    for k, p in cfg["products"].items():
        price = data.get(PRICE_FIELD[k])
        if not price:
            continue
        blocks.append("\n".join([f"🌿 گزارش قیمت {p['name']} هندی", LINE, *sections(p, price, rate)]))
    return "\n\n".join(blocks)


FX_EMOJI = {"usd": "💵", "usdt": "🪙", "gold": "🥇", "coin": "🏅", "afn": "🇦🇫", "pkr": "🇵🇰", "aed": "🇦🇪"}


def build_fx_message(data: dict) -> str | None:
    """Second message: currency/gold prices with the sources and the agreement confidence."""
    items = data.get("fx", {}).get("items")
    if not items:
        return None
    line = "━" * 20
    out = ["💱 گزارش نرخ ارز و طلا", line]
    for it in items:
        conf = f"{fa(it['conf'])}٪ ({fa(it['k'])} از {fa(it['n'])} منبع)" if it["conf"] is not None else "فقط یک منبع"
        out += [f"{FX_EMOJI.get(it['id'], '•')} {it['name']}",
                f"💰 میانگین: {fa(it['avg'])} تومان",
                f"🎯 اطمینان: {conf}",
                *[f"{'📌' if x['ok'] else '⚠️'} {x['s']}: {fa(x['v'])}" for x in it["sources"]], line]
    return "\n".join(out)


def build_alert_message(data: dict) -> str | None:
    """Third message, only when something needs the owner's attention."""
    alerts = data.get("alerts")
    return "🔧 هشدار فنی بازاربان\n" + "\n".join(alerts) if alerts else None


SPICE_KEY = {"فلفل سیاه": "b", "زردچوبه": "t"}  # which configured product (cost model) a world price belongs to


def fad(text: str) -> str:
    return str(text).translate(FA)


def build_world_messages(data: dict, cfg: dict) -> list[str]:
    """Free world spice prices, one message per source, each item in the owner's report format (world price, then price with costs)."""
    w = data.get("world")
    if not w:
        return []
    rate = data.get("usd_toman") or 0
    inr = float(data.get("inr_to_usd") or 0)
    pkr = next((i["avg"] for i in data.get("fx", {}).get("items", []) if i["id"] == "pkr"), 0)
    prods = cfg.get("products", {})

    def usd_block(title: str, spice: str, usd_ton: float, extra: list[str]) -> str:
        p = prods.get(SPICE_KEY.get(spice, ""))
        body = sections(p, usd_ton, rate) if p and rate else [
            "🌍 قیمت جهانی | بدون احتساب مصارف", f"💵 قیمت هر تن: {fa(usd_ton)} دلار",
            f"⚖️ قیمت هر کیلو: {fa(usd_ton * rate / 1000)} تومان", f"💰 قیمت هر تن: {fa(usd_ton * rate)} تومان", LINE]
        return "\n".join([f"🌿 {title}", LINE, *extra, *body])

    msgs = []
    if ipc := w.get("ipc"):
        msgs.append("\n\n".join(usd_block(f"گزارش قیمت {i.get('name_fa') or i['name']} | IPC", "فلفل سیاه", i["usd_ton"],
                                          [f"🏷️ {i['name']}", f"📅 تاریخ: {fad(ipc['date'])}", "🧭 نوع قیمت: صادراتی (FOB) | منبع: شورای بین‌المللی فلفل", LINE])
                                for i in ipc["items"] if rate))
    if sb := w.get("spices_board"):
        msgs.append("\n\n".join(usd_block(f"گزارش قیمت {i['spice']} | بازار {i.get('market_fa') or i['market']}", i["spice"], i["usd_ton"],
                                          [f"🏷️ {i['market']} | درجه: {i.get('grade_fa') or i['grade']}", f"📅 تاریخ: {fad(sb['date'])}",
                                           f"🇮🇳 قیمت بازار داخلی هند: {num(i['inr_kg'])} روپیه/کیلو", "🧭 نوع قیمت: بازار مبدأ (نه FOB) | منبع: Spices Board", LINE])
                                for i in sb["items"] if rate))
    if wk := w.get("spices_weekly"):
        blocks = []
        for i in wk["items"]:
            if not (rate and inr):
                continue
            blocks.append(usd_block(f"گزارش قیمت {i['spice']} | بازار {i['market_fa']}", i["spice"], i["inr_kg"] * inr * 1000,
                                    [f"🏷️ {i['market']}", f"📅 هفتهٔ منتهی به: {fad(wk['date'])}",
                                     f"🇮🇳 این هفته: {num(i['inr_kg'])} | هفتهٔ قبل: {num(i['prev_week'])} | ماه قبل: {num(i['prev_month'])} | پارسال: {num(i['last_year'])} روپیه/کیلو",
                                     "🧭 نوع قیمت: میانگین هفتگی بازار مبدأ (نه FOB) | منبع: Spices Board", LINE]))
        msgs.append("\n\n".join(blocks))
    if gv := w.get("govi"):
        msgs.append("\n\n".join("\n".join([
            f"🌿 گزارش قیمت {i['spice']} | سری‌لانکا", LINE, f"🏷️ {i['name']}", f"📅 تاریخ: {fad(i['date'])}",
            f"🇱🇰 قیمت عمده: {num(i['lkr_kg'])} روپیه سری‌لانکا/کیلو (بازه {num(i['min'] or 0)}–{num(i['max'] or 0)})",
            f"↩️ روز قبل: {num(i['prev'] or 0)} | بهترین بازار: {i['market_fa']}", "🧭 نوع قیمت: بازار مبدأ | منبع: Govi Saviya", LINE]) for i in gv["items"]))
    if ct := w.get("comtrade"):
        blocks = []
        for i in ct["items"]:
            if not rate:
                continue
            blocks.append(usd_block(f"گزارش ارزش گمرکی {i['product_fa']} | {i['reporter_fa']}", i["product_fa"], i["usd_kg"] * 1000,
                                    [f"🏷️ {i['reporter']} | {i['flow_fa']}", f"📅 سال: {fad(i['period'])}",
                                     "🧭 نوع قیمت: ارزش واحد گمرکی سالانه (نه قیمت روز و نه FOB) | منبع: Comtrade", LINE]))
        msgs.append("\n\n".join(blocks))
    if mn := w.get("manual"):
        blocks = []
        for i in mn["items"]:
            usd = i["price"] if "ton" in i.get("unit", "").lower() and "usd" in i.get("unit", "").lower() else None
            extra = [f"🏷️ {i['name']}", f"📅 تاریخ: {fad(i['date'])}", f"✍️ واردشده دستی از {i.get('source', '')}: {num(i['price'])} {i.get('unit', '')}"] + ([f"📝 {i['note']}"] if i.get("note") else []) + [LINE]
            spice = "فلفل سیاه" if "pepper" in i["name"].lower() else "زردچوبه" if "turmeric" in i["name"].lower() else ""
            blocks.append(usd_block(f"گزارش قیمت {i.get('name_fa') or i['name']}", spice, usd, extra) if usd and rate else "\n".join([f"🌿 گزارش قیمت {i.get('name_fa') or i['name']}", LINE, *extra]))
        msgs.append("\n\n".join(blocks))
    if arm := w.get("arm_pk"):
        blocks = []
        for com in dict.fromkeys(i["commodity"] for i in arm["items"]):
            rows = [LINE]
            for i in sorted((x for x in arm["items"] if x["commodity"] == com), key=lambda x: x["date"], reverse=True)[:6]:
                flag = (" ⚠️ پرت" if i["outlier"] else "") + (" 🕓 قدیمی" if (date.fromisoformat(arm["date"]) - date.fromisoformat(i["date"])).days > 3 else "")
                mid = (i["kg_min"] + i["kg_max"]) / 2
                rows += [f"🏷️ {i.get('market_fa') or i['market']} ({i['market']}) | 📅 {fad(i['date'])}{flag}",
                         f"🇵🇰 قیمت: {num(i['kg_min'])}–{num(i['kg_max'])} روپیه پاکستان/کیلو" + (f" ≈ {fa(mid * pkr)} تومان/کیلو" if pkr else ""), LINE]
            blocks.append("\n".join([f"🌿 گزارش قیمت {com} | منڈی‌های پاکستان", *rows]))
        msgs.append("\n\n".join(blocks))
    notes = ["📝 نکته‌ها", LINE,
             "• سه قیمت را هرگز با هم قاطی نکنید: قیمت بازار مبدأ (داخلی) ≠ قیمت FOB صادراتی ≠ ارزش گمرکی واردات.",
             "• قیمت‌های هند، پاکستان و سری‌لانکا قیمت بازار داخلی‌اند؛ برای قیمت تمام‌شده، بسته‌بندی، حمل و سود صادرکننده را هم باید اضافه کرد.",
             "• درصد مصارف و وزن محموله همان تنظیمات سایت است و برای قیمت داخلی هم صرفاً برای مقایسه محاسبه شده.",
             "• فلفل: ۵۰۰ گرم/لیتر با ۵۵۰ گرم/لیتر و ASTA فرق دارد؛ درجه را حتماً مقایسه کنید.",
             "• گاربلد = فلفل تمیزشده؛ آن‌گاربلد = تمیزنشده و ارزان‌تر.",
             "• ⚠️ پرت = منبع آن ردیف را غیرعادی علامت زده؛ 🕓 قدیمی = تاریخ آن ردیف از بقیه عقب‌تر است. ARM دستی و غیررسمی است.",
             "• ارزش گمرکی Comtrade سالانه و چند ماه عقب است؛ فقط برای سنجش سطح قیمت.",
             "• برای فلفل ۳ میلی‌متری، شکسته، گرد، پوسته و سبک منبع رایگان روزانه نداریم؛ از تأمین‌کننده استعلام بگیرید.",
             "• معادل تومانی با نرخ دلار و ارز همین گزارش محاسبه شده و تقریبی است."]
    return [m for m in msgs if m] + ["\n".join(notes)]


def stale_alert(data: dict, log_path: str, now: datetime | None = None) -> str | None:
    """Run after a failed price update. Fires in one run only (data 40-55 min old, runs are 15 min apart)."""
    updated = datetime.fromisoformat(data["updated"].replace("Z", "+00:00"))
    age = ((now or datetime.now(updated.tzinfo)) - updated).total_seconds() / 60
    if not 40 <= age < 55:
        return None
    try:
        lines = [l for l in open(log_path, encoding="utf-8", errors="replace").read().splitlines() if l.strip()]
    except OSError:
        lines = []
    err = next((l for l in reversed(lines) if "fail" in l.lower() or "error" in l.lower()), lines[-1] if lines else "")
    at = updated.astimezone(ZoneInfo("Asia/Tehran")).strftime("%H:%M").translate(FA)
    return (f"🔧 هشدار فنی بازاربان\nقیمت ادویه‌جات حدود {fa(age)} دقیقه است به‌روز نشده (آخرین قیمت موفق: {at} تهران).\n"
            f"خطا: {err[:300]}")


MSG_LIMIT = 3900  # Telegram caps a message at 4096 chars; stay below it


def chunks(text: str) -> list[str]:
    """Split a long message on line boundaries so no part exceeds MSG_LIMIT."""
    if len(text) <= MSG_LIMIT:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > MSG_LIMIT:  # a single huge line: hard split
            parts.append(line[:MSG_LIMIT])
            line = line[MSG_LIMIT:]
        if cur and len(cur) + len(line) + 1 > MSG_LIMIT:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


def send(text: str) -> None:
    jobs = []
    for part in chunks(text):
        part = "\n".join("\u200f" + l if l.strip() else l for l in part.split("\n"))
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            jobs.append((f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                         {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": part}))
        if os.environ.get("RUBIKA_BOT_TOKEN"):
            jobs.append((f"https://botapi.rubika.ir/v3/{os.environ['RUBIKA_BOT_TOKEN']}/sendMessage",
                         {"chat_id": os.environ["RUBIKA_CHAT_ID"], "text": part}))
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
    if "--alert-stale" in sys.argv:  # after a failed update: tell the group, once per outage
        msg = stale_alert(data, sys.argv[sys.argv.index("--alert-stale") + 1])
        if msg:
            send(msg)
        raise SystemExit(0)
    cfg = json.load(open("config.json", encoding="utf-8"))
    texts = [t for t in (build_message(data, cfg), build_fx_message(data), *build_world_messages(data, cfg), build_alert_message(data)) if t]  # separate messages
    if "--dry" in sys.argv:
        print("\n\n=====\n\n".join(texts))
        raise SystemExit
    failed = False
    for t in texts:  # a failing platform must not stop the other message
        try:
            send(t)
        except SystemExit:
            failed = True
    raise SystemExit(1 if failed else 0)
