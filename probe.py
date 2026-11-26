import json, requests, fx_prices, notify
d = requests.get("https://raw.githubusercontent.com/alirezahasheminezhad/Bazarban/data/data.json", timeout=20).json()
open("data.json", "w").write(json.dumps(d))
fx_prices.main()
d = json.load(open("data.json"))
print("-----MESSAGE-----"); print(notify.build_fx_message(d))
print("-----JSON-----"); print(json.dumps(d, ensure_ascii=False))
