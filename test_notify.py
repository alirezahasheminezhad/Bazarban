import json
from datetime import datetime, timezone

from fetch_prices import trim_history
from notify import build_message

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
print("ok")
