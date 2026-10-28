import json
from notify import build_message, changed, model

cfg = json.load(open("config.json", encoding="utf-8"))["products"]["t"]
# 10% origin cost -> goods are 10/11 of the total
m = model(cfg, 2783.7701, 283000)
assert abs(m["parts"][0][1] / m["total"] - 10 / 11) < 1e-9
text, kgs = build_message(json.load(open("data.json", encoding="utf-8")), {"products": {"t": cfg}})
assert "۹۰٫۹٪" in text and "حمل داخلی" not in text  # zero-cost lines are hidden
assert changed(kgs, {}) and not changed(kgs, kgs) and changed(kgs, {"t": kgs["t"] * 1.02})
print("ok")
