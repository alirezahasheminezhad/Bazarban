import json
from notify import build_messages, model

cfg = json.load(open("config.json", encoding="utf-8"))["products"]["t"]
# example from the owner: 25 t, 10% origin cost -> goods 90.9% / origin costs 9.1% of total
m = model(cfg, 2783.7701, 283000)
assert abs(m["parts"][0][1] / m["total"] - 10 / 11) < 1e-9
assert abs(m["total"] - m["goods"] * 1.1 * 283000) < 1
msgs = build_messages(json.load(open("data.json", encoding="utf-8")), {"products": {"t": cfg}})
assert len(msgs) == 3 and "۹۰٫۹٪" in msgs[2]
print("ok")
