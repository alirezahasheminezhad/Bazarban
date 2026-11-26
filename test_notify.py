import re
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from fetch_prices import trim_history, expiry_key, _candidate_rows
from fx_prices import aggregate, find_price, extract
from notify import build_alert_message, build_fx_message, build_message, stale_alert
from fx_prices import update_health
import fx_prices

cfg = json.load(open("config.json", encoding="utf-8"))["products"]["t"]
cfg.update(tons=25, cnt=1, oon=1, opct=10, ofix=0, costs=[])  # the site rewrites config.json, so pin the values
# rate 1000 makes the world price per ton exactly the owner's example: 761,899,030 toman
text = build_message({"usd_toman": 1000, "usd_per_ton": 761899.03}, {"products": {"t": cfg}})
for expected in ("۷۶۱٬۸۹۹٬۰۳۰ تومان", "۱۹٬۰۴۷٬۴۷۵٬۷۵۰ تومان", "با احتساب ۱۰٪ مصارف",
                 "۷۶٬۱۹۰ تومان", "۸۳۸٬۰۸۹ تومان", "۸۳۸٬۰۸۹٬۰۰۰ تومان", "۲۰٬۹۵۲٬۲۲۵٬۰۰۰ تومان", "۲۵ تنی"):
    assert expected in text, expected
cfg["opct"] = 15  # the percentage always follows the site setting
assert "با احتساب ۱۵٪ مصارف" in build_message({"usd_toman": 1000, "usd_per_ton": 761899.03}, {"products": {"t": cfg}})

now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
old, new = {"t": "2026-10-27T11:59:00Z"}, {"t": "2026-10-27T12:01:00Z"}
assert trim_history([old, new, {"x": 1}], now) == [new]  # >24h and malformed records are dropped
# fx: 4 of 5 sources agree on the dollar -> 80% confidence, the odd one is flagged and left out of the average
r = aggregate([("a", 275500), ("b", 275000), ("c", 276000), ("d", 275400), ("e", 262000)])
assert r["conf"] == 80 and r["k"] == 4 and r["sources"][4]["ok"] is False and r["avg"] == 272780  # all five count in the average
assert aggregate([("a", 1000)])["conf"] is None and aggregate([("a", 1000), ("b", 1200)])["conf"] == 50
# text extraction: toman page, rial page (/10), and a percent change that must not be picked
usd = 270500
assert extract("pkr", "alanchand.com", "قیمت فروش روپیه پاکستان 996 0.00%", usd) == 996
assert extract("pkr", "tgju.org", "روپیه پاکستان قیمت 9,960 ریال تغییر 1.2", usd) == 996
assert extract("aed", "ok-ex.io", "درهم امارات 75,010 تومان +1.34%", usd) == 75010
assert extract("gold", "tgju.org", "قیمت طلای 18 عیار 265,000,000 ریال", usd) == 26500000
assert find_price("دلار 1.32 درصد", r"دلار", 1e5, 1e6) is None
import fx_prices, notify
from unittest import mock
# new sources: snippets as the pages served them on 2026-11-26
pk = "دلار آمریکا (بازار آزاد) 294٬065 تومان +0.40% سکه امامی (طرح جدید) 264٬945٬000 تومان نفت خام برنت (Brent) 94٫67 دلار +0.17%"
pk = fx_prices.digits_to_ascii(pk)
assert extract("brent", "pishkhanak.com", pk, 294000) == 94.67 and extract("coin", "pishkhanak.com", pk, 294000) == 264945000
assert extract("brent", "tradingeconomics.com", "Brent oil - Price Summary Actual 95.107 Daily Change 0.50 0.52%", 294000) == 95.107
yr = "دلار آمریکا 294,300 ▲ +0.17% درهم امارات 80,150 ▲ افغانی افغانستان 4,525 ▲ +0.11% روپیه هند 3,180"
assert [extract(c, "yekrial.com", yr, 294000) for c in ("usd", "aed", "afn", "pkr")] == [294300, 80150, 4525, None]
hm = {"q": {"price_dollar_rl": {"v": 2938900}, "oil_brent": {"v": 95.001}, "price_pkr": {"v": 10542.0}}}
with mock.patch.object(fx_prices, "jget", return_value=hm):
    got = {e[0]: fx_prices.fetch_api(e, 294000) for e in fx_prices.API if e[1] == "hamedsaghafi.com"}
assert got["usd"] == 293890 and got["brent"] == 95.001 and got["pkr"] == 1054.2 and got["gold"] is None
oil = {"id": "brent", "name": "نفت برنت", "unit": "دلار", **aggregate([("a", 94.67), ("b", 95.001)], digits=2)}
oil_message = build_fx_message({"fx": {"items": [oil]}})
assert oil["avg"] == 94.84 and "میانگین: ۹۴٫۸۴ دلار" in oil_message
assert "مرجع نمودار زنده TradingView" in oil_message
assert "https://www.tradingview.com/symbols/UKOIL/" in oil_message
assert notify.apply_off({"fx": {"items": [oil]}}, ["fx|brent|b"])["fx"]["items"][0]["avg"] == 94.67
fx = build_fx_message({"fx": {"items": [{"id": "usd", "name": "دلار آمریکا", **r}]}})
assert "اطمینان: ۸۰٪ (۴ از ۵ منبع)" in fx and "⚠️ e: ۲۶۲٬۰۰۰" in fx and build_fx_message({}) is None
# nabzesh API: parses price.price; gold comes per milligram (x1000); stale prices are skipped
import fx_prices
from unittest import mock
def fake(price, stale=False):
    m = mock.Mock(); m.json.return_value = {"price": {"price": price, "isStale": stale}}; return m
with mock.patch.object(fx_prices.requests, "get", return_value=fake("275000")):
    assert fx_prices.fetch_nabzesh("usd", 270500) == 275000
with mock.patch.object(fx_prices.requests, "get", return_value=fake("26200")):
    assert fx_prices.fetch_nabzesh("gold", 270500) == 26200000
with mock.patch.object(fx_prices.requests, "get", return_value=fake("275000", stale=True)):
    assert fx_prices.fetch_nabzesh("usd", 270500) is None
with mock.patch.object(fx_prices.requests, "get", return_value=fake("999")):
    assert fx_prices.fetch_nabzesh("usd", 270500) is None  # implausible vs the USD rate
# health: a source that never worked stays quiet; one that worked alerts on the 3rd miss only, then on recovery
h = {}
assert [update_health(h, "a", "x", False) for _ in range(5)] == [[]] * 5
assert update_health(h, "b", "src", True) == []
N = fx_prices.FAIL_ALERT  # the alert fires on exactly the Nth consecutive miss
assert [bool(update_health(h, "b", "src", False)) for _ in range(N + 1)] == [False] * (N - 1) + [True, False]
assert update_health(h, "b", "src", True) == ["✅ src: دوباره جواب داد"]
assert build_alert_message({}) is None and "x" in build_alert_message({"alerts": ["x"]})
# stale alert fires only when the last good data is 40-55 min old
from datetime import datetime, timedelta, timezone
d = {"updated": "2026-10-31T08:00:00Z"}; t0 = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)
assert stale_alert(d, "none.log", t0 + timedelta(minutes=20)) is None and stale_alert(d, "none.log", t0 + timedelta(minutes=60)) is None
assert "۱۱:۳۰ تهران" in stale_alert(d, "none.log", t0 + timedelta(minutes=47))
# public-API parsers (response shapes taken from the iran-crypto-mcp / iran-gold-mcp sources)
fp = fx_prices
assert fp._wallex({"result": {"symbols": {"USDTTMN": {"baseAsset": "USDT", "quoteAsset": "TMN", "stats": {"lastPrice": "271900"}}}}}) == 271900
assert fp._exir({"usdt-irt": {"last": 271800}}) == 271800
assert fp._tabdeal({"currencies": {"usdt": {"IRT": {"price": "271700"}}}}) == 271700
assert fp._omp({"data": [{"baseCurrencyId": "USDT", "quoteCurrencyId": "IRR", "status": "LISTED", "price": 2719000}]}) == 2719000
assert fp._ramz({"data": [{"quote_currency_symbol": {"en": "irr"}, "base_currency_symbol": {"en": "USDT"}, "financial": {"last24h": {"close": 2718000}}}]}) == 2718000
assert fp._bitpin([{"symbol": "BTC_IRT", "price": "1"}, {"symbol": "USDT_IRT", "price": "271600"}]) == 271600
assert fp._okex([{"asset": "USDT", "buyAmt": 271000, "sellAmt": 272000}]) == 271500
assert fp._milli({"data": {"price18": "258190"}}) == 25819000
assert fp._wallgold({"result": [{"symbol": "GLD_18C_750TMN", "side": "buy", "price": 26000000}, {"symbol": "GLD_18C_750TMN", "side": "sell", "price": 25800000}]}) == 25900000
assert fp._melligold({"data": {"price_buy": 26000000, "price_sell": 25900000}}) == 25950000
assert fp._daric({"Data": {"BestSellPrice": 26100000, "BestBuyPrice": 25900000}}) == 26000000
# a rial feed is scaled to toman, an implausible value is dropped
with mock.patch.object(fp, "jget", return_value={"data": [{"baseCurrencyId": "USDT", "quoteCurrencyId": "IRR", "status": "LISTED", "price": 2719000}]}):
    assert fp.fetch_api(("usdt", "ompfinex.com", "u", None, None, fp._omp), 270500) == 271900
with mock.patch.object(fp, "jget", return_value={"data": [{"baseCurrencyId": "USDT", "quoteCurrencyId": "IRR", "status": "LISTED", "price": 5}]}):
    assert fp.fetch_api(("usdt", "ompfinex.com", "u", None, None, fp._omp), 270500) is None
with mock.patch.object(fp, "jget", side_effect=RuntimeError("blocked")):
    assert fp.fetch_api(("usdt", "x", "u", None, None, fp._omp), 270500) is None  # a dead source never breaks the run
assert fp.extract("coin", "tgju.org", "سکه امامی طرح جدید 2,950,000,000 ریال", 270500) == 295000000
# guardnet JSON (shape from /api/prices): currency sell price, 18k gold per gram, Emami coin, USDT toman
g = {"data": {"currency_prices": {"items": [{"code": "usd", "sell_price": {"value": 269300}}, {"code": "pkr", "sell_price": {"value": 990}}]},
              "gold_prices": {"items": [{"slug": "18ayar", "price": 25800000}, {"slug": "sekkeh", "price": 295000000}]},
              "crypto_prices": {"items": [{"symbol": "USDT", "price_toman": 270275}]}}}
assert fp._guard_cur("usd")(g) == 269300 and fp._guard_cur("pkr")(g) == 990 and fp._guard_cur("aed")(g) is None
assert fp._guard_gold("18ayar")(g) == 25800000 and fp._guard_gold("sekkeh")(g) == 295000000 and fp._guard_usdt(g) == 270275
# expiry: full dates keep the day; month-only labels do not
assert expiry_key("20-Nov-2026") == (2026, 11, 20)
assert expiry_key("18-Dec-2026") == (2026, 12, 18)
assert expiry_key("Nov26") == (2026, 11, None)
assert expiry_key("Dec 2026") == (2026, 12, None)
assert expiry_key("26,600.00") is None
# an expired contract is rolled over on its expiry DAY, not at month end
from bs4 import BeautifulSoup
tbl = ('<table><tr><th>Contract</th><th>Expiry</th><th>LTP</th></tr>'
       '<tr><td>Pepper</td><td>20-Nov-2026</td><td>780</td></tr>'
       '<tr><td>Pepper</td><td>18-Dec-2026</td><td>790</td></tr></table>')
soup = BeautifulSoup(tbl, "html.parser")
assert _candidate_rows(soup, "pepper", today=date(2026, 11, 19))[0]["price"] == 780
assert _candidate_rows(soup, "pepper", today=date(2026, 11, 21))[0]["price"] == 790  # Nov contract expired on the 20th
assert _candidate_rows(soup, "pepper", today=date(2026, 12, 17))[0]["price"] == 790
assert _candidate_rows(soup, "pepper", today=date(2026, 12, 19)) == []  # Dec contract expired on the 18th
# turmeric: Farmer Polished is preferred over the plain contract at the same expiry
tbl2 = ('<table><tr><th>Contract</th><th>Expiry</th><th>LTP</th></tr>'
        '<tr><td>Turmeric</td><td>18-Dec-2026</td><td>26500</td></tr>'
        '<tr><td>Turmeric (Farmer Polished)</td><td>18-Dec-2026</td><td>26600</td></tr></table>')
assert _candidate_rows(BeautifulSoup(tbl2, "html.parser"), "turmeric", today=date(2026, 11, 14))[0]["price"] == 26600
# fx alerts are transition events: a run with no new alerts must clear the old ones (no repeated bot messages)
import fx_prices
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "data.json"
    p.write_text(json.dumps({"usd_toman": 270500, "alerts": ["old"]}), encoding="utf-8")
    with mock.patch.object(fx_prices, "OUTPUT", p), mock.patch.object(fx_prices, "run", return_value=[]):
        fx_prices.main()
    assert "alerts" not in json.loads(p.read_text(encoding="utf-8"))
    p.write_text(json.dumps({"usd_toman": 270500}), encoding="utf-8")
    with mock.patch.object(fx_prices, "OUTPUT", p), mock.patch.object(fx_prices, "run", return_value=["new"]):
        fx_prices.main()
    assert json.loads(p.read_text(encoding="utf-8"))["alerts"] == ["new"]
# notify: long messages are split below the Telegram limit, on line boundaries
import notify
long = "\n".join(f"line {i} " + "x" * 100 for i in range(60))
with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}, clear=True):
    with mock.patch.object(notify.requests, "post") as post:
        post.return_value = mock.Mock(raise_for_status=lambda: None, json=lambda: {"ok": True})
        notify.send(long)
        assert post.call_count >= 2
        sent = [c.kwargs["json"]["text"] for c in post.call_args_list]
        assert all(len(t) <= notify.MSG_LIMIT for t in sent)
        assert "\n".join(sent).replace("\u200f", "") == long
# world prices: IPC daily table (the % change cell must not be taken as the price)
import world_prices
ipc = ('<h3>DAILY PRICES 20-11-2026</h3><table>'
       '<tr><td>Indonesia - Black Pepper</td><td>6,748</td><td>-0.04%</td></tr>'
       '<tr><td>Brazil Black - Pepper ASTA 570</td><td>5,400</td><td>-2.78%</td></tr>'
       '<tr><td>Viet Nam - Black Pepper 500 g/l</td><td>5,700</td><td>-</td><td>0.00%</td></tr>'
       '<tr><td>Some other spice</td><td>1,000</td></tr></table>')
w = world_prices.parse_ipc(ipc)
assert w["date"] == "2026-11-20" and [i["usd_ton"] for i in w["items"]] == [6748, 5400, 5700]
assert world_prices.parse_ipc("<p>no table</p>") is None
# Spices Board: blank Min/Max -> the average column is used; INR/kg -> USD/t with the INR rate
sb = ('<table><tr><th>Date</th><th>Spice</th><th>Market Centre</th><th>State</th><th>Grade</th><th>Source</th><th>Min</th><th>Max</th><th>Avg</th></tr>'
      '<tr><td>18-Nov-2026</td><td>Pepper</td><td>Cochin</td><td>KERALA</td><td>Garbled</td><td>IPSTA</td><td>-</td><td>-</td><td>774.00</td></tr>'
      '<tr><td>18-Nov-2026</td><td>Cardamom</td><td>X</td><td>Y</td><td>Z</td><td>W</td><td>-</td><td>-</td><td>2000</td></tr></table>')
s = world_prices.parse_spices_board(sb, 0.0105)
assert s["date"] == "2026-11-18" and len(s["items"]) == 1 and s["items"][0]["spice"] == "فلفل سیاه" and s["items"][0]["usd_ton"] == 8127.0
# ARM Pakistan: every row has its own date; "Per kg300 – 325" range, single value, and the * outlier flag
arm = ('<table><tr><th>Mandi</th><th>Date</th><th>Per 100 kg</th><th>Per 40 kg (mun)</th><th>Per kg</th></tr>'
       '<tr><td>Multan</td><td>17 November 2026</td><td>Per 100 kg30,000 – 32,500</td><td>Per 40 kg (mun)12,000 – 13,000</td><td>Per kg300 – 325</td></tr>'
       '<tr><td>Rawalpindi *</td><td>17 November 2026</td><td>x</td><td>y</td><td>Per kg350 – 575</td></tr>'
       '<tr><td>Layyah</td><td>16 November 2026</td><td>x</td><td>y</td><td>Per kg90</td></tr></table>')
a = world_prices.parse_arm(arm, "c")
assert [(i["market"], i["date"], i["kg_min"], i["kg_max"], i["outlier"]) for i in a] == [
    ("Multan", "2026-11-17", 300, 325, False), ("Rawalpindi", "2026-11-17", 350, 575, True), ("Layyah", "2026-11-16", 90, 90, False)]
assert world_prices.parse_arm("<p>nothing</p>", "c") == []
# Spices Board weekly PDF text (real layout): this week, last week, last month, last year; only labelled markets are read
pdf = ("AVERAGE DOMESTIC PRICES OF MAJOR SPICES FOR THE WEEK ENDING 07.11.2026 (PRICE IN RS./KG.)\n"
       "CHILLI GUNTUR 202.50 205.00 205.00 127.00\nERODE 235.00 200.00 190.00 165.00\nSALEM 290.00 225.00 192.50 175.00\n"
       "FINGER NIZAMABAD 194.50 202.50 151.00 120.50\nNO.1 CHENNAI 372.50 370.00 360.00 300.00\nSALEM - 1 2 3\n")
wk = world_prices.parse_spices_weekly(pdf)
assert wk["date"] == "2026-11-07" and len(wk["items"]) == 4 and wk["items"][0]["spice"] == "فلفل قرمز"
assert wk["items"][1]["inr_kg"] == 235 and wk["items"][1]["prev_week"] == 200 and wk["items"][1]["last_year"] == 165 and wk["items"][1]["market_fa"] == "ایرود"
assert world_prices.to_fa("Cochin Garbled") == "کوچین پاک‌شده (گاربلد)" and world_prices.to_fa("Zzz") == "Zzz"
assert world_prices.parse_spices_weekly("no table here") is None
assert world_prices._pdf_day("https://x/07.11.2026%20dmfmt.pdf") > world_prices._pdf_day("https://x/17.10.2026%20Spicemarket.pdf")
# one source down must not drop the other, and a never-working source stays quiet
with mock.patch.object(world_prices, "get", side_effect=lambda u: ipc if "ipcnet" in u else (_ for _ in ()).throw(OSError("down"))), \
        mock.patch.object(world_prices, "fetch_spices_weekly", side_effect=OSError("down")):
    d = {"inr_to_usd": 0.0105}
    with mock.patch.object(world_prices, "MAX_AGE_DAYS", 10**6):  # the fixture's date must not age out of the test
        assert world_prices.run(d, {}) == []
    assert list(d["world"]) == ["updated", "ipc"]
# world messages: owner's report format per item, Persian equivalent, notes last; every line right-aligned on send
import json as _json, notify
cfg = _json.load(open("config.json", encoding="utf-8"))
wm = notify.build_world_messages({"usd_toman": 268000, "world": {"ipc": {"date": "2026-11-20", "items": [
    {"name": "Brazil Black - Pepper ASTA 570", "name_fa": "برزیل", "usd_ton": 5000}]}}}, cfg)
assert len(wm) == 2 and "برزیل" in wm[0] and "۱٬۳۴۰٬۰۰۰ تومان" in wm[0] and "درصد مصارف" in wm[0] and "نکته" in wm[1]
assert all(len(c) <= notify.MSG_LIMIT for m in wm for c in notify.chunks(m)) and notify.build_world_messages({"usd_toman": 1}, cfg) == []

# Govi Saviya / manual prices / freshness / Persian names
g = world_prices.parse_govi({"data": [{"item": "Ginger", "latestDate": "2026-11-20", "latestPrice": 205, "latestMin": 200, "latestMax": 210,
                                       "previousPrice": 190, "bestMarket": "Dambulla", "unit": "kg"}, {"item": "Beans", "latestPrice": 5, "unit": "kg"}]})
assert g["date"] == "2026-11-20" and len(g["items"]) == 1 and g["items"][0]["market_fa"] == "دامبولا" and world_prices.parse_govi({"data": []}) is None
import tempfile, pathlib
mp = pathlib.Path(tempfile.mkdtemp()) / "m.json"
mp.write_text('{"items":[{"source":"VPSA","name":"Viet Nam Black Pepper","price":6000,"unit":"USD/ton","date":"2026-11-20"},{"name":"x"}]}', encoding="utf-8")
assert len(world_prices.read_manual(str(mp))["items"]) == 1 and world_prices.read_manual("/nonexistent.json") is None
wm = "\n".join(notify.build_world_messages({"usd_toman": 268000, "world": {
    "govi": g, "manual": {"date": "2026-11-20", "items": [{"source": "VPSA", "name": "Pepper", "name_fa": "فلفل", "price": 6000, "unit": "USD/ton", "date": "2026-11-20"}]}}}, cfg))
assert "دامبولا" in wm and "VPSA" in wm and "قیمت نهایی" in wm
# only fresh prices survive; the Persian names carry no Latin letters
fr = world_prices.fresh_only({"date": "2026-11-20", "items": [{"date": "2026-11-20"}, {"date": "2026-11-10"}]}, date(2026, 11, 22))
assert len(fr["items"]) == 1 and world_prices.fresh_only({"date": "2026-11-01", "items": [{}]}, date(2026, 11, 22)) is None
for en in ("Viet Nam - Black Pepper 500 g/l", "Brazil Black - Pepper ASTA 570", "Malaysia - White Pepper ASTA", "Indonesia - Black Pepper"):
    assert not re.search("[A-Za-z]", world_prices.ipc_fa(en)), en
assert world_prices.ipc_fa("Viet Nam - Black Pepper 500 g/l") == "فلفل سیاه ویتنام | 500 گرم بر لیتر"
assert not re.search("[A-Za-z]", world_prices.to_fa("Dera Ghazi Khan")) and not re.search("[A-Za-z]", world_prices.to_fa("Cochin Ungarbled"))
assert notify.to_jalali(date(2026, 3, 21)) == "1405/01/01" and notify.to_jalali(date(2026, 11, 23)) == "1405/09/02"
assert notify.when("2026-11-20", date(2026, 11, 23)) == "۱۴۰۵/۰۸/۲۹ (۳ روز قبل)" and "دیروز" in notify.when("2026-11-22", date(2026, 11, 23))
assert all(c.count("\n") < 128 and len(c) <= notify.MSG_LIMIT for c in notify.chunks("\n".join(["x"] * 300)))  # Rubika: max 128 lines
# sources switched off on the site are left out of the bot messages and the fx average
od = {"fx": {"items": [{"id": "usd", "name": "d", **aggregate([("a", 100), ("b", 200)])}]},
      "world": {"updated": "x", "ipc": {"date": "2026-11-20", "items": [{"name": "Indonesia - Black Pepper"}, {"name": "V"}]}}}
r = notify.apply_off(od, ["fx|usd|b", "w|ipc|Indonesia - Black Pepper"])
assert r["fx"]["items"][0]["avg"] == 100 and r["fx"]["items"][0]["n"] == 1 and r["world"]["ipc"]["items"] == [{"name": "V"}]
r = notify.apply_off(od, ["fx|usd|a", "fx|usd|b", "w|ipc"])
assert r["fx"]["items"] == [] and r["world"] is None and len(od["world"]["ipc"]["items"]) == 2
print("ok")
