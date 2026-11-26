// دروازهٔ امن گیت‌هاب برای بازاربان و شرکت‌یاب. توکن فقط در Secretهای Cloudflare است.
// Secrets: GH_TOKEN و ADMIN_PASSWORD. binding از نوع ratelimit با نام LIMIT (۲۰ درخواست در دقیقه برای هر IP) جلوی حدس زدن رمز را می‌گیرد.
// فقط همین عملیات مجاز است؛ هیچ آدرس یا مسیر دلخواهی از کاربر به گیت‌هاب نمی‌رود.
const ORIGIN = "https://alirezahasheminezhad.github.io";
const GH = "https://api.github.com/repos/alirezahasheminezhad/";
const WORKFLOWS = {
  company: { file: "company-search.yml", inputs: ["keyword", "refresh", "delete", "pages"] },
  foodkeys: { file: "foodkeys-search.yml", inputs: ["keyword", "refresh", "delete", "banks", "pages", "details"] },
};

const reply = (status, body) => new Response(status === 204 ? null : JSON.stringify(body ?? {}), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8", "cache-control": "no-store",
    "access-control-allow-origin": ORIGIN, "access-control-allow-headers": "authorization, content-type",
    "access-control-allow-methods": "POST",
  },
});

const sha = async (t) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(t)));
async function same(a, b) { // مقایسهٔ زمان‌ثابت
  const [x, y] = await Promise.all([sha(a), sha(b)]);
  return x.reduce((d, v, i) => d | (v ^ y[i]), 0) === 0;
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0;
const isCost = (c) => c && typeof c.n === "string" && ["pct", "cont", "ton", "kg", "all"].includes(c.t) && isNum(c.v) && [0, 1].includes(c.on);
// notify.py با config خراب از کار می‌افتد، پس پیش از نوشتن بررسی می‌شود
export const validConfig = (c) =>
  !!c && Array.isArray(c.off) && c.off.every((s) => typeof s === "string") &&
  ["t", "b"].every((k) => {
    const p = c.products?.[k];
    return p && typeof p.name === "string" && ["tons", "cnt", "opct", "ofix"].every((f) => isNum(p[f])) &&
      [0, 1].includes(p.oon) && Array.isArray(p.costs) && p.costs.every(isCost);
  }) && JSON.stringify(c).length < 50000;

async function handle(req, env) {
  if (req.method === "OPTIONS") return reply(204);
  if (req.method !== "POST") return reply(405, { error: "POST only" });
  if (!env.GH_TOKEN || !env.ADMIN_PASSWORD || !env.LIMIT) return reply(500, { error: "Worker secrets or LIMIT binding missing" });
  // پیش از بررسی رمز شمرده می‌شود، وگرنه حدس درست بعد از سقف هم رد می‌شد
  if (!(await env.LIMIT.limit({ key: req.headers.get("cf-connecting-ip") || "?" })).success) return reply(429, { error: "too many attempts, wait a minute" });
  const given = (req.headers.get("authorization") || "").replace(/^Bearer /, "");
  if (!(await same(given, env.ADMIN_PASSWORD))) return reply(401, { error: "wrong password" });

  let b;
  try { b = await req.json(); } catch { return reply(400, { error: "bad json" }); }
  const gh = (path, init = {}) => fetch(GH + path, {
    ...init,
    headers: { authorization: `Bearer ${env.GH_TOKEN}`, accept: "application/vnd.github+json", "user-agent": "github-gate" },
  });
  // خطای گیت‌هاب 502 می‌شود تا سایت آن را با «رمز اشتباه» قاطی نکند
  const upstream = (r) => reply(502, { error: "github " + r.status });

  if (b?.action === "bazarban-config") {
    if (!validConfig(b.config)) return reply(400, { error: "invalid config" });
    const url = "Bazarban/contents/config.json";
    const cur = await gh(url + "?ref=main");
    if (!cur.ok) return upstream(cur);
    const bytes = new TextEncoder().encode(JSON.stringify(b.config, null, 2) + "\n");
    const content = btoa(String.fromCharCode(...bytes));
    const r = await gh(url, { method: "PUT", body: JSON.stringify({ message: "Bot settings from the site", content, sha: (await cur.json()).sha, branch: "main" }) });
    return r.ok ? reply(200, { ok: true }) : upstream(r);
  }
  if (b?.action === "search") {
    const w = Object.hasOwn(WORKFLOWS, b.workflow) ? WORKFLOWS[b.workflow] : null;
    const kw = b.inputs?.keyword;
    if (!w || typeof kw !== "string" || !kw.trim() || kw.length > 100) return reply(400, { error: "invalid search" });
    const inputs = Object.fromEntries(w.inputs.filter((k) => typeof b.inputs[k] === "string").map((k) => [k, b.inputs[k].slice(0, 200)]));
    const r = await gh(`sherkatyab/actions/workflows/${w.file}/dispatches`, { method: "POST", body: JSON.stringify({ ref: "main", inputs }) });
    return r.ok ? reply(204) : upstream(r);
  }
  if (b?.action === "runs") {
    const r = await gh("sherkatyab/actions/runs?event=workflow_dispatch&per_page=10");
    return r.ok ? reply(200, await r.json()) : upstream(r);
  }
  if (b?.action === "cancel" && Number.isSafeInteger(b.id) && b.id > 0) {
    const r = await gh(`sherkatyab/actions/runs/${b.id}/cancel`, { method: "POST" });
    return r.ok ? reply(200, { ok: true }) : upstream(r);
  }
  return reply(400, { error: "unknown action" });
}

export default {
  fetch: (req, env) => handle(req, env).catch(() => reply(502, { error: "github unreachable" })),
};
