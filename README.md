# بازاربان (Bazarban)

محاسبه **هزینه تمام‌شده بار وارداتی** (زردچوبه قلم و فلفل سیاه نکوبیده) با قیمت زنده بازار هند و نرخ ارز/طلای ایران — به‌همراه گزارش خودکار به تلگرام و روبیکا.

- نسخه آنلاین: <https://alirezahasheminezhad.github.io/Bazarban/>
- داده زنده: شاخه [`data`](https://github.com/alirezahasheminezhad/Bazarban/tree/data) (فایل `data.json`)
- تتر لایو: Cloudflare Worker روی کانال [@tetherpriceFa](https://t.me/tetherpriceFa)

## چطور کار می‌کند

```
cron-job.org (هر ۱۵ دقیقه)
   └─ GitHub Actions: update-prices.yml
        ├─ fetch_prices.py   قیمت آتی زردچوبه/فلفل از NCDEX + INR→USD + دلار→تومان (ارزدیجیتال)
        ├─ fx_prices.py      ارز و طلا از ~۳۰ منبع (API صرافی‌ها، tgju، nabzesh، گاردنت…) → میانگین + درصد اطمینان
        ├─ انتشار data.json در شاخه data (تک‌کامیت، بدون رشد تاریخچه)
        └─ notify.py         گزارش قیمت + گزارش ارز/طلا + هشدار فنی → تلگرام و روبیکا
index.html (GitHub Pages)
   ├─ خواندن data.json هر ۵ دقیقه
   ├─ محاسبه هزینه تمام‌شده با هزینه‌های قابل‌ویرایش (ذخیره در مرورگر)
   └─ تتر لایو از Worker (هر ۳۰ ثانیه)
```

- قیمت NCDEX: زردچوبه **روپیه per 100kg** (×۱۰ برای تن)، فلفل **روپیه per kg** (×۱۰۰۰ برای تن)؛ سپس × نرخ INR→USD.
- قرارداد نزدیک‌ترین سررسید انتخاب می‌شود؛ اگر روز سررسید گذشته باشد، قرارداد بعدی جایگزین می‌شود. برای زردچوبه، قرارداد «Farmer Polished» در اولویت است.
- `data.json` فقط وقتی نوشته می‌شود که همه منابع اجباری معتبر باشند؛ خرابی یک منبع ارز/طلا فقط همان منبع را حذف می‌کند.
- اگر منبعی که قبلاً کار می‌کرده ۳ بار پیاپی قیمت ندهد (یا دوباره جواب بدهد)، یک‌بار به گروه هشدار می‌رود. اگر قیمت ادویه ~۴۵ دقیقه به‌روز نشود هم هشدار می‌آید.

## فایل‌ها

| فایل | کار |
|---|---|
| `fetch_prices.py` | قیمت ادویه از NCDEX + نرخ دلار → `data.json` (با تاریخچه ۲۴ ساعته) |
| `fx_prices.py` | ارز/طلا/سکه از چند منبع → `data.fx` + پایش سلامت منابع (`fx_health`) |
| `notify.py` | ساخت و ارسال پیام‌ها به تلگرام/روبیکا (پیام‌های بلند خودکار تکه می‌شوند) |
| `index.html` | رابط وب (تک‌فایل، بدون build) |
| `worker/` | Cloudflare Worker تتر لایو از کانال تلگرام (بدون KV/اکانت) |
| `config.json` | تنظیمات ربات: تناژ، تعداد کانتینر، هزینه‌ها (از دکمه «کپی تنظیمات ربات» در سایت) |
| `test_notify.py` | تست‌های پایتون (قیمت، ارز، پیام‌ها، هشدارها) |
| `worker/test.mjs` | تست پارسر Worker |
| `.github/workflows/` | به‌روزرسانی قیمت، ارسال دستی، تست خبری، تست‌های CI |

## راه‌اندازی

1. **Secrets** (Settings → Secrets and variables → Actions):
   - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
   - `RUBIKA_BOT_TOKEN` / `RUBIKA_CHAT_ID` (اختیاری)
   - `NABZESH_API_KEY` (اختیاری — منبع nabzesh بدون کلید هم کار می‌کند)
   - `NVIDIA_API_KEY` (فقط برای workflow تست خبری)
2. **GitHub Pages**: Settings → Pages → Deploy from branch → `main` / root.
3. **cron-job.org**: یک cron هر ۱۵ دقیقه که `workflow_dispatch` را صدا بزند:
   `POST https://api.github.com/repos/<owner>/Bazarban/actions/workflows/update-prices.yml/dispatches` با هدر `Authorization: Bearer <PAT>` و بدنه `{"ref":"main"}`.
4. **Worker** (اختیاری، برای تتر لایو):
   ```bash
   cd worker && npx wrangler deploy
   ```
   آدرس Worker را در `index.html` جلوی `LIVE_USDT_URL` بگذارید.

## اجرا و تست محلی

```bash
pip install -r requirements.txt
python -m playwright install chromium   # فقط برای رندر صفحات JS-heavy
python fetch_prices.py                  # نیاز به دسترسی به NCDEX/ارزدیجیتال
python fx_prices.py
python notify.py --dry                  # پیش‌نمایش پیام‌ها بدون ارسال
python test_notify.py                   # تست‌ها
node worker/test.mjs
```

`BAZARBAN_DEBUG=1` لاگ تشخیصی صفحه‌های رندرشده را چاپ می‌کند.

## نکته امنیتی

قفل رمز سایت فقط سمت مرورگر است (هش SHA-256 در کد) و `data.json` عمومی است; این قفل فقط برای جلوگیری از دید تصادفی است، نه امنیت واقعی. برای محافظت واقعی باید مخزن خصوصی یا سرور با احراز هویت داشت. توکن‌ها هرگز در مخزن نیستند و فقط از Secrets خوانده می‌شوند.