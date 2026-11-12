// node worker/test.mjs  (بدون وابستگی)
import assert from "node:assert/strict";
import { parse } from "./index.mjs";

const post = (n, text, ts) =>
  `<div class="tgme_widget_message_wrap"><div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="tetherpriceFa/${n}">` +
  `<div class="tgme_widget_message_text js-message_text" dir="auto">${text}</div>` +
  `<a class="tgme_widget_message_date" href="x"><time datetime="${ts}" class="time">x</time></a></div></div>`;

const now = Date.parse("2026-11-12T08:51:30+00:00");
const iso = (s) => new Date(now - s * 1000).toISOString();
const html =
  post(1210175, "USDT Price - قیمت تتر<br/>267,228 IRT<br/><a>$USDT</a>", iso(90)) +
  post(1210176, "USDT Price - قیمت تتر<br/>267,321 IRT<br/><a>$USDT</a>", iso(30)) +
  post(1210177, "Trade 150% CashBack (ad, no price)", iso(5));

let r = parse(html, now);
assert.equal(r.price, 267321);                 // newest post with a price; the ad is skipped
assert.equal(r.age_s, 30);
assert.equal(r.stale, false);
assert.equal(r.id, "tetherpriceFa/1210176");
assert.equal(parse(post(1, "۲۶۷٬۴۵۰ IRT", iso(10)), now).price, 267450);   // Persian digits
assert.equal(parse(post(1, "267,450 IRT", iso(1200)), now).stale, true);   // channel stopped
assert.equal(parse(post(1, "267,450 IRT", "garbage"), now).stale, true);   // unreadable time
assert.equal(parse("<html></html>", now), null);                            // layout changed
console.log("worker parse OK");
