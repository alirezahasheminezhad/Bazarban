import json
from datetime import datetime, timezone

from fetch_prices import trim_history
from fx_prices import aggregate, find_price, extract
from notify import build_alert_message, build_fx_message, build_message, stale_alert
from fx_prices import update_health

cfg = json.load(open("config.json", encoding="utf-8"))["products"]["t"]  # 25 t, 10% origin cost
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
assert [bool(update_health(h, "b", "src", False)) for _ in range(4)] == [False, False, True, False]
assert update_health(h, "b", "src", True) == ["✅ src: دوباره جواب داد"]
assert build_alert_message({}) is None and "x" in build_alert_message({"alerts": ["x"]})
# stale alert fires only when the last good data is 40-55 min old
from datetime import datetime, timedelta, timezone
d = {"updated": "2026-10-31T08:00:00Z"}; t0 = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)
assert stale_alert(d, "none.log", t0 + timedelta(minutes=20)) is None and stale_alert(d, "none.log", t0 + timedelta(minutes=60)) is None
assert "۱۱:۳۰ تهران" in stale_alert(d, "none.log", t0 + timedelta(minutes=47))
print("ok")
