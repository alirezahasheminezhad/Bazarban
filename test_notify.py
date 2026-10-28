import json
from notify import build_message, model

cfg = json.load(open("config.json", encoding="utf-8"))["products"]["t"]
# 10% origin cost -> goods are 10/11 of the total
m = model(cfg, 2783.7701, 283000)
assert abs(m["parts"][0][1] / m["total"] - 10 / 11) < 1e-9
text = build_message(json.load(open("data.json", encoding="utf-8")), {"products": {"t": cfg}})
assert "۹۰٫۹٪" in text and "حمل داخلی" not in text  # zero-cost lines are hidden
assert "برابر" in text and "تومان هر تن · " in text  # raw world price, no costs
from datetime import datetime, timezone
from fetch_prices import trim_history
now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
old, new = {"t": "2026-10-27T11:59:00Z"}, {"t": "2026-10-27T12:01:00Z"}
assert trim_history([old, new, {"x": 1}], now) == [new]  # >24h and malformed records are dropped
print("ok")
