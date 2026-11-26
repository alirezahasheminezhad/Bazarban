import json, re, requests
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from fx_prices import to_text
from fetch_prices import HEADERS
PAGES = ["https://bitpin.ir/academy/live/currency/", "https://pishkhanak.com/economy", "https://market.hamedsaghafi.com/", "https://yekrial.com/",
         "https://www.tradingview.com/symbols/ICEEUR-BRN1!/", "https://www.tradingview.com/symbols/UKOIL/",
         "https://tradingeconomics.com/commodity/brent-crude-oil", "https://www.investing.com/commodities/brent-oil"]
API = [f"https://scanner.tradingview.com/symbol?symbol={s}&fields=close,change,currency,update_mode&no_404=true" for s in ("ICEEUR%3ABRN1!", "TVC%3AUKOIL")]
KW = r"دلار|تتر|طلای? ?18|گرم طلا|سکه|افغانی|روپیه پاکستان|درهم|برنت|Brent"
def show(text):
    hits = [m.start() for m in re.finditer(KW, text)]
    last = -999
    n = 0
    for i in hits:
        if i - last > 160 and n < 14:
            print("   >", text[max(0, i-40):i+170]); last = i; n += 1
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    br = p.chromium.launch()
    for u in PAGES + API:
        print("\n=====", u)
        parts = urlsplit(u)
        try:
            rr = requests.get(f"{parts.scheme}://{parts.netloc}/robots.txt", headers=HEADERS, timeout=20)
            rp = RobotFileParser(); rp.parse(rr.text.splitlines())
            print("robots", rr.status_code, "allowed:", rp.can_fetch("*", u) if rr.ok else "?")
        except Exception as e: print("robots", type(e).__name__)
        try:
            r = requests.get(u, headers=HEADERS, timeout=30)
            print("plain", r.status_code, len(r.text))
            if u in API: print(r.text[:400]); continue
            if r.ok: show(to_text(r.text))
        except Exception as e: print("plain", type(e).__name__)
        if u in API: continue
        pg = br.new_page(user_agent=HEADERS["User-Agent"]); xhr = []
        pg.on("response", lambda x: xhr.append(x.url) if "json" in x.headers.get("content-type", "") else None)
        try:
            pg.goto(u, wait_until="domcontentloaded", timeout=45000); pg.wait_for_timeout(8000)
            t = to_text(pg.content()); print("rendered", len(t), pg.title()); show(t)
        except Exception as e: print("render", type(e).__name__)
        print("json xhr:", [x[:160] for x in xhr][:15]); pg.close()
