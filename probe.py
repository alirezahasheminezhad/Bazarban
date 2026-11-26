import json, re, requests
from urllib.robotparser import RobotFileParser
from fx_prices import to_text, extract, SPEC
from fetch_prices import HEADERS
usd = float(json.load(open("data.json"))["usd_toman"]) if True else 0
try: usd = float(requests.get("https://raw.githubusercontent.com/alirezahasheminezhad/Bazarban/data/data.json", timeout=20).json()["usd_toman"])
except Exception as e: print(e)
print("usd", usd)
for u in ["https://pishkhanak.com/economy", "https://yekrial.com/", "https://market.hamedsaghafi.com/"]:
    t = to_text(requests.get(u, headers=HEADERS, timeout=30).text)
    print("=====", u)
    for c in SPEC:
        v = extract(c, "x", t, usd)
        m = re.search(SPEC[c][1], t)
        print(c, v)
        for m in list(re.finditer(SPEC[c][1], t))[:6]: print("    ", t[m.start()-30:m.end()+80])
    for k in ("برنت", "روپیه پاکستان", "PKR"):
        for m in list(re.finditer(k, t))[:4]: print("  ?", k, "|", t[m.start()-60:m.end()+100])
rp = RobotFileParser(); r = requests.get("https://market.hamedsaghafi.com/robots.txt", headers=HEADERS, timeout=20); print("ROBOTS\n", r.text[:1500]); rp.parse(r.text.splitlines())
for f in ("market", "servix", "bourse", "retail", "crypto"):
    u = f"https://market.hamedsaghafi.com/{f}.json"
    print("=====", u, "allowed", rp.can_fetch("*", u))
    if not rp.can_fetch("*", u): continue
    r = requests.get(u, headers=HEADERS, timeout=30); print(r.status_code, len(r.text)); print(r.text[:2500])
    try:
        def walk(o, p=""):
            if isinstance(o, dict):
                if re.search(r"dol|usd|aed|afn|pkr|brent|geram|18|emami|sek|usdt|oil", p, re.I) and len(json.dumps(o)) < 400: print(" ", p, json.dumps(o, ensure_ascii=False)); return
                for k, v in o.items(): walk(v, f"{p}.{k}")
            elif isinstance(o, list):
                for i, v in enumerate(o[:200]): walk(v, f"{p}[{v.get('symbol') or v.get('key') or v.get('id') or i}]" if isinstance(v, dict) else f"{p}[{i}]")
        walk(r.json())
    except Exception as e: print(type(e).__name__, e)
t = requests.get("https://tradingeconomics.com/commodity/brent-crude-oil", headers=HEADERS, timeout=30)
print("===== TE", t.status_code)
x = to_text(t.text)
for m in list(re.finditer(r"Brent (rose|fell|traded|remained|stayed|decreased|increased)|Actual", x))[:6]: print("  ", x[m.start()-40:m.end()+150])
r = requests.get("https://tradingeconomics.com/terms.aspx", headers=HEADERS, timeout=30); x = to_text(r.text); print("TERMS", r.status_code, len(x))
for m in list(re.finditer(r"(?i)scrap|robot|automat|crawl|spider", x))[:8]: print("  ", x[max(0,m.start()-250):m.end()+200])
