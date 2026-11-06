"""One-off test: spice news (Google News RSS) -> NVIDIA NIM models -> Persian analysis (log, job summary, bots).
Needs env NVIDIA_API_KEY. Models are matched by substring against /v1/models (override with FAST_MODEL / STRONG_MODEL)."""
import os, re, sys, time, xml.etree.ElementTree as ET
from urllib.parse import quote
import requests

BASE = "https://integrate.api.nvidia.com/v1"
PRODUCTS = {  # Persian name -> English news query
    "زردچوبه": "turmeric price India",
    "فلفل سیاه": "black pepper price Vietnam India Indonesia",
    "دارچین": "cinnamon price Sri Lanka Indonesia Vietnam",
    "فلفل قرمز پاکستانی (عنابی)": "Pakistan red chilli price Kunri Sindh",
    "زنجبیل": "ginger price China India",
}
MODELS = ("mistral-large-4", "gemma-4-31b-it", "deepseek-v4.1-flash")  # non-reasoning first (fast); first match wins


def headlines(q, n=8):
    r = requests.get(f"https://news.google.com/rss/search?q={quote(q + ' when:14d')}&hl=en&gl=US&ceid=US:en", timeout=30)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        t = re.sub(r"<[^>]+>", " ", it.findtext("title", "")).strip()
        out.append(f"- {t} ({it.findtext('pubDate', '')[:16]}) {it.findtext('link', '')}")
        if len(out) == n:
            break
    return out


def pick(ids, want):
    return os.environ.get("MODEL") or next((i for w in want for i in ids if w in i), None)


def chat(key, model, prompt, max_tokens=2500):
    for a in range(2):
        try:
            r = requests.post(f"{BASE}/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=120,
                              json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                    "temperature": 0.3, "max_tokens": max_tokens})
            r.raise_for_status()
            ch = r.json()["choices"][0]
            if not ch["message"].get("content"):  # reasoning models can spend the whole budget thinking
                raise RuntimeError(f"empty content, finish_reason={ch.get('finish_reason')}, keys={list(ch['message'])}")
            return re.sub(r"<think>.*?</think>", "", ch["message"]["content"], flags=re.S).strip()
        except Exception as e:
            print(f"chat {model} try {a + 1}: {e}")
            time.sleep(5 * (a + 1))
    raise SystemExit(f"model {model} failed")


def main():
    key = os.environ["NVIDIA_API_KEY"]
    r = requests.get(f"{BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    ids = [m["id"] for m in r.json()["data"]]
    model = pick(ids, MODELS)
    print("model:", model)
    if not model:
        raise SystemExit("model not found; candidates: " + ", ".join(i for i in ids if re.search("kimi|deepseek|mistral|gemma|nemotron", i)))
    blocks = []
    for fa_name, q in PRODUCTS.items():
        h = headlines(q)
        print(fa_name, len(h), "headlines")
        if h:
            blocks.append(f"## {fa_name}\n" + "\n".join(h))
    text = "📰 مرور خبر بازار ادویه (آزمایشی)\n\n" + chat(key, model, f"""تو تحلیلگر بازار ادویه برای یک واردکننده‌ی ایرانی هستی. تیترهای خبری ۲ هفته‌ی اخیر هر محصول:

{chr(10).join(blocks)}

فقط بر پایه‌ی همین تیترها و کوتاه، به فارسی بنویس. برای هر محصول یک خط:
«نام محصول — 🟢 مثبت / 🔴 منفی / ⚪ خنثی (برای قیمت‌ها: مثبت یعنی احتمال گرانی، منفی یعنی احتمال ارزانی؛ سیل و کمبود محصول = گرانی) — یک جمله دلیل — لینک مهم‌ترین خبر».
اگر شواهد کم است بنویس «شواهد کافی نیست». عدد قیمت نساز.
در پایان یک پاراگراف ۲ تا ۳ خطی «جمع‌بندی کلی» بنویس و بگو این مرور خبری است نه تضمین قیمت.""")
    print(text)
    open(os.environ.get("GITHUB_STEP_SUMMARY", "news_test.out.md"), "a", encoding="utf-8").write(text + "\n")
    if os.environ.get("SEND") == "true":
        from notify import send
        send(text[:3900])


if __name__ == "__main__":
    main()
