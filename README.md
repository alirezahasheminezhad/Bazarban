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
        ├─ fx_prices.py      ارز، طلا و نفت برنت از ~۴۵ منبع (API صرافی‌ها، tgju، nabzesh، گاردنت، پیشخوانک، یک‌ریال، حامد ثقفی…) → میانگین + درصد اطمینان
        ├─ world_prices.py   قیمت مرجع جهانی (رایگان): IPC فلفل + Spices Board هند → data.world
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
- اگر منبعی که قبلاً کار می‌کرده ۸ بار پیاپی (حدود ۲ ساعت) قیمت ندهد (یا دوباره جواب بدهد)، یک‌بار به گروه هشدار می‌رود. اگر قیمت ادویه ~۴۵ دقیقه به‌روز نشود هم هشدار می‌آید.

## فایل‌ها

| فایل | کار |
|---|---|
| `fetch_prices.py` | قیمت ادویه از NCDEX + نرخ دلار → `data.json` (با تاریخچه ۲۴ ساعته) |
| `fx_prices.py` | ارز/طلا/سکه از چند منبع → `data.fx` + پایش سلامت منابع (`fx_health`) |
| `world_prices.py` | قیمت مرجع جهانی از منابع رایگان (IPC، Spices Board) → `data.world`؛ هر منبع مستقل است و خرابی یکی دیگری را نمی‌اندازد |
| `notify.py` | ساخت و ارسال پیام‌ها به تلگرام/روبیکا (پیام‌های بلند خودکار تکه می‌شوند) |
| `index.html` | رابط وب (تک‌فایل، بدون build) |
| `worker/` | Cloudflare Worker تتر لایو از کانال تلگرام (بدون KV/اکانت) |
| `config.json` | تنظیمات ربات: تناژ، تعداد کانتینر، هزینه‌ها + منابع خاموش (`off`)؛ از دکمه «ذخیره برای ربات» در سایت (رمز مدیر؛ بخش «دروازهٔ امن» پایین) |
| `test_notify.py` | تست‌های پایتون (قیمت، ارز، پیام‌ها، هشدارها) |
| `worker/test.mjs` | تست پارسر Worker |
| `.github/workflows/` | به‌روزرسانی قیمت، ارسال دستی، تست خبری، تست‌های CI |

## منابع جهانی ادویه (فقط رایگان)

بخش «قیمت مرجع جهانی ادویه» در سایت دو قسمت دارد: قیمت‌های زنده‌ی IPC، Spices Board (روزانه و PDF هفتگی زردچوبه/زنجبیل/فلفل قرمز) و ARM پاکستان (از `data.world`) و یک فهرست ثابت از منابع رایگان دیگر (AGMARKNET، VPSA، سریلانکا، پاکستان، بنگلادش، Comex Stat، BPS، گمرک چین، Husfarm، Tridge) همراه با کدهای HS و عبارت‌های استعلام.

- سه قیمت جدا نگه داشته می‌شوند: بازار مبدأ، FOB صادراتی، ارزش واحد گمرکی؛ هرگز میانگین‌گیری مشترک نمی‌شوند.
- نفت برنت (دلار) از پیشخوانک، حامد ثقفی و Trading Economics؛ ویجت TradingView با نماد `TVC:UKOIL` برای مشاهده‌ی مستقیم در سایت نمایش داده می‌شود و پیوندش در گزارش ربات می‌آید، اما عدد داخل iframe به‌صورت خودکار خوانده یا وارد میانگین نمی‌شود (شرایط استفاده و محدودیت دسترسی iframe). Investing.com و صفحه‌ی فهرست ارز بیت‌پین نیز دسترسی خودکار را می‌بندند (403).
- برای فلفل ۳ میل، Broken، Dust، Husk و Screenings منبع رایگان روزانه‌ی قابل‌اتکا پیدا نشد؛ باید از صادرکننده استعلام گرفت.
- AGMARKNET (API data.gov.in کلید رایگان می‌خواهد)، VPSA (robots اجازه‌ی خواندن نمی‌دهد)، Govi Saviya (حالا خودکار از JSON خود سایت)  هنوز خودکار خوانده نمی‌شوند (لینک و توضیح در فهرست هستند). ARM نرخ قدیمی را هم نگه می‌دارد؛ در سایت ردیف‌های قدیمی‌تر از ۳ روز «قدیمی» و کم‌رنگ می‌شوند. PDF هفتگی با `pypdf` خوانده می‌شود و فقط خط‌هایی که نام کالا را تکرار می‌کنند خوانده می‌شوند. افزودن هر منبع جدید = یک تابع parse در `world_prices.py` + یک تست.

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

## دروازهٔ امن (`gate/`)

توکن گیت‌هاب در مرورگر یا کد نیست؛ در Worker کلودفلر به نام `github-gate` (https://github-gate.ezaccess14ea188.workers.dev/) می‌ماند و سایت‌های بازاربان و شرکت‌یاب فقط رمز مدیر را می‌فرستند.

1. داشبورد Cloudflare → Workers → Create → نام `github-gate` → Deploy، سپس Edit code و محتوای `gate/index.mjs` را جایگزین کنید.
2. Settings → Variables and Secrets، هر دو از نوع **Secret**:
   - `GH_TOKEN`: توکن Fine-grained فقط برای مخزن‌های `Bazarban` (Contents: Read and write) و `sherkatyab` (Actions: Read and write)
   - `ADMIN_PASSWORD`: رمز دلخواه (هرچه بلندتر امن‌تر)
   - binding از نوع Rate limit با نام `LIMIT` (۲۰ در ۶۰ ثانیه)؛ بدون آن Worker کاری انجام نمی‌دهد
3. اگر آدرس Worker فرق داشت، `GATE_URL` را در `index.html` هر دو سایت عوض کنید.

## اجرا و تست محلی

```bash
pip install -r requirements.txt
python -m playwright install chromium   # فقط برای رندر صفحات JS-heavy
python fetch_prices.py                  # نیاز به دسترسی به NCDEX/ارزدیجیتال
python fx_prices.py
python notify.py --dry                  # پیش‌نمایش پیام‌ها بدون ارسال
python test_notify.py                   # تست‌ها
node worker/test.mjs
node gate/test.mjs
```

`BAZARBAN_DEBUG=1` لاگ تشخیصی صفحه‌های رندرشده را چاپ می‌کند.

## نکته امنیتی

قفل رمز سایت فقط سمت مرورگر است (هش SHA-256 در کد) و `data.json` عمومی است; این قفل فقط برای جلوگیری از دید تصادفی است، نه امنیت واقعی. برای محافظت واقعی باید مخزن خصوصی یا سرور با احراز هویت داشت. توکن‌ها هرگز در مخزن نیستند و فقط از Secrets خوانده می‌شوند.

### قیمت‌های دستی (VPSA و منابعی که ربات را نمی‌پذیرند)
robots.txt سایت VPSA همهٔ ربات‌ها را ممنوع کرده، پس خودکار نمی‌خوانیمش. قیمت را از https://vpsaspice.org/market-price/vietnam/ خودتان بخوانید و در `manual_prices.json` (آرایهٔ `items`) بنویسید:
`{"source":"VPSA","name":"Viet Nam Black Pepper 500 g/l","price":6000,"unit":"USD/ton","date":"2026-11-20"}`
در اجرای بعدی (حداکثر ۱۵ دقیقه) در سایت و پیام تلگرام/روبیکا با معادل فارسی می‌آید.
فقط قیمت‌های تازه (حداکثر ۳ روز، `MAX_AGE_DAYS` در `world_prices.py`) نمایش داده می‌شوند و آمار سالانه (Comtrade) عمداً حذف شده است.
