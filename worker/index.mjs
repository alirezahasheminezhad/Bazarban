// قیمت تتر از کانال تلگرام tetherpriceFa (صفحهٔ عمومی t.me/s/...) -> JSON با CORS، بدون اکانت و بدون KV.
// خروجی: {price, time, age_s, stale, id}؛ stale یعنی آخرین پست قدیمی‌تر از ۱۵ دقیقه است (کانال هر دقیقه می‌زند).
const CHANNEL = "tetherpriceFa";
const STALE_S = 900;

const toAscii = (s) => s.replace(/[۰-۹]/g, (d) => "۰۱۲۳۴۵۶۷۸۹".indexOf(d)).replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/٬/g, ",").replace(/٫/g, ".");

export function parse(html, now = Date.now()) {
  for (const p of html.split('data-post="').slice(1).reverse()) { // newest post last on the page
    const text = (p.match(/js-message_text[^>]*>([\s\S]*?)<\/div>/) || [])[1] || "";
    const m = toAscii(text.replace(/<[^>]+>/g, " ")).match(/(\d[\d,]*(?:\.\d+)?)\s*IRT\b/);
    if (!m) continue; // an ad or announcement: look at the post before it
    const time = (p.match(/<time[^>]*datetime="([^"]+)"/) || [])[1];
    const t = Date.parse(time);
    const age_s = Number.isNaN(t) ? null : Math.round((now - t) / 1000);
    return { price: Number(m[1].replace(/,/g, "")), time: time || null, age_s, stale: age_s === null || age_s > STALE_S, id: p.split('"')[0] };
  }
  return null;
}

export default {
  async fetch() {
    let body, status = 200;
    try {
      const r = await fetch(`https://t.me/s/${CHANNEL}`, { headers: { "user-agent": "Mozilla/5.0" }, cf: { cacheTtl: 20, cacheEverything: true } });
      if (!r.ok) throw new Error(`t.me ${r.status}`);
      body = parse(await r.text());
      if (!body) throw new Error("no price post found (layout changed or preview disabled)");
    } catch (e) {
      body = { error: String(e.message || e) };
      status = 502;
    }
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*", "cache-control": "public, max-age=15" },
    });
  },
};
