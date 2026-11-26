// node gate/test.mjs  (بدون وابستگی)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import gate, { validConfig } from "./index.mjs";

let allow = true;
const env = { GH_TOKEN: "secret-token", ADMIN_PASSWORD: "p4ss", LIMIT: { limit: async () => ({ success: allow }) } };
let calls = [], status = 200;
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url, ...init });
  return new Response(status === 204 ? null : JSON.stringify({ sha: "abc", workflow_runs: [] }), { status });
};
const send = (body, pw = env.ADMIN_PASSWORD, e = env) =>
  gate.fetch(new Request("https://x/", { method: "POST", headers: { authorization: "Bearer " + pw }, body: JSON.stringify(body) }), e);

for (const pw of ["", "wrong", env.ADMIN_PASSWORD + "x"]) assert.equal((await send({ action: "runs" }, pw)).status, 401);
assert.equal((await send({ action: "runs" }, "", { ...env, ADMIN_PASSWORD: "" })).status, 500);
assert.equal((await send({ action: "runs" }, env.ADMIN_PASSWORD, { ...env, LIMIT: undefined })).status, 500);
allow = false;
assert.equal((await send({ action: "runs" })).status, 429); // بعد از سقف، رمز درست هم رد می‌شود
allow = true;
assert.equal(calls.length, 0); // بدون رمز درست هیچ تماسی با گیت‌هاب نیست

const config = JSON.parse(readFileSync(new URL("../config.json", import.meta.url)));
config.off = ["w|ipc"];
assert.ok(validConfig(config));
assert.ok(!validConfig({ ...config, products: { t: config.products.t } }));
assert.equal((await send({ action: "bazarban-config", config })).status, 200);
const put = calls.at(-1);
assert.equal(put.method, "PUT");
assert.ok(put.url.endsWith("/Bazarban/contents/config.json"));
assert.deepEqual(JSON.parse(Buffer.from(JSON.parse(put.body).content, "base64")), config);

status = 204;
const r = await send({ action: "search", workflow: "company", inputs: { keyword: "ذرت", pages: "3", evil: "x" } });
assert.equal(r.status, 204);
assert.deepEqual(JSON.parse(calls.at(-1).body), { ref: "main", inputs: { keyword: "ذرت", pages: "3" } });

calls = [];
for (const bad of [{ action: "search", workflow: "__proto__", inputs: { keyword: "a" } }, { action: "search", workflow: "company", inputs: {} },
  { action: "cancel", id: "1/../x" }, { action: "delete-repo" }, null]) assert.equal((await send(bad)).status, 400);
assert.equal(calls.length, 0);

status = 401;
const up = await send({ action: "runs" });
assert.equal(up.status, 502);
assert.ok(!(await up.text()).includes("secret-token"));
console.log("gate OK");
