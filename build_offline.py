#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ساخت نسخه‌ی آفلاین تک‌فایلی از index.html (فقط کتابخانه‌ی استاندارد پایتون).

css/js/فونت/تصویرهای محلی و CDN (کتابخانه‌ها، Tailwind، Google Fonts) را داخل
یک فایل HTML جاسازی می‌کند تا با دو بار کلیک و بدون اینترنت اجرا شود.

    python build_offline.py                 # index.html -> dist/<نام پوشه>-offline.html
    python build_offline.py in.html out.html
"""
import base64
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"  # بدون UA مدرن، گوگل فونت woff2 نمی‌دهد
MIME = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp", ".ico": "image/x-icon"}
_cache: dict[str, bytes] = {}


# ───────── ۱) خواندن فایل محلی یا آدرس اینترنتی ─────────
def load(ref: str, base):
    """base: پوشه‌ی محلی (Path) یا آدرس اینترنتی (str). خروجی: (بایت‌ها، مکان نهایی)."""
    if ref.startswith("//"):
        ref = "https:" + ref
    if ref.startswith("http") or isinstance(base, str):
        url = ref if ref.startswith("http") else urljoin(base, ref)
        if url not in _cache:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                _cache[url] = r.read()
        return _cache[url], url
    p = (base / re.split(r"[?#]", ref)[0]).resolve()
    return p.read_bytes(), p


def where(loc):
    return loc if isinstance(loc, str) else loc.parent


def data_uri(data: bytes, ref: str) -> str:
    ext = Path(re.split(r"[?#]", ref)[0]).suffix.lower()
    return f"data:{MIME.get(ext, 'application/octet-stream')};base64,{base64.b64encode(data).decode()}"


def attr(tag: str, name: str):
    m = re.search(rf"""\b{name}\s*=\s*["']([^"']*)["']""", tag, re.I)
    return m.group(1) if m else None


# ───────── ۲) جاسازی url(...) داخل css ─────────
def css_inline(css: str, base) -> str:
    def imp(m):  # @import (مثل گوگل فونت) خودش بازگشتی جاسازی می‌شود
        data, loc = load(m.group(2), base)
        return css_inline(data.decode("utf-8"), where(loc))
    css = re.sub(r"""@import\s+(?:url\(\s*)?(['"])([^'"]+)\1\s*\)?\s*;""", imp, css)

    def sub(m):
        ref = m.group(2).strip()
        if ref.startswith(("data:", "#")):
            return m.group(0)
        data, _ = load(ref, base)
        return f'url("{data_uri(data, ref)}")'
    return re.sub(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", sub, css)


# ───────── ۳) تبدیل تگ‌های link / script / img ─────────
def assets_in_text(text: str) -> str:
    """مسیرهای رشته‌ایِ assets/... داخل js یا html (مثل مهر پیش‌فرض) به data URI تبدیل می‌شوند."""
    def sub(m):
        p = ROOT / m.group(2)
        return f'{m.group(1)}{data_uri(p.read_bytes(), m.group(2))}{m.group(1)}' if p.is_file() else m.group(0)
    return re.sub(r"""(["'])(assets/[\w./-]+)\1""", sub, text)


def link_tag(m):
    tag = m.group(0)
    rel, href = (attr(tag, "rel") or "").lower(), attr(tag, "href")
    if not href:
        return tag
    if "stylesheet" in rel:
        data, loc = load(href, ROOT)
        return f"<style>\n{css_inline(data.decode('utf-8'), where(loc))}\n</style>"
    if "icon" in rel:
        return f'<link rel="icon" href="{data_uri(load(href, ROOT)[0], href)}">'
    return ""  # preconnect / preload / manifest در نسخه‌ی آفلاین بی‌معنی‌اند


def script_tag(m):
    src = attr(m.group(1), "src")
    code = load(src, ROOT)[0].decode("utf-8")
    kind = ' type="module"' if attr(m.group(1), "type") == "module" else ""
    return f"<script{kind}>\n{assets_in_text(code).replace('</script', '<\\/script')}\n</script>"


def img_tag(m):
    ref = m.group(3)
    if ref.startswith(("data:", "http", "//", "$")) or not (ROOT / ref).is_file():  # $ = قالب js
        return m.group(0)
    return f"{m.group(1)}{m.group(2)}{data_uri(load(ref, ROOT)[0], ref)}{m.group(2)}"


def build(src: Path, out: Path) -> None:
    html = src.read_text(encoding="utf-8")
    html = re.sub(r"<link\b[^>]*>", link_tag, html, flags=re.I)
    html = re.sub(r"<script\b([^>]*\bsrc\s*=[^>]*)>\s*</script>", script_tag, html, flags=re.I)
    html = re.sub(r"""(<img\b[^>]*?\bsrc=)(["'])([^"']+)\2""", img_tag, html, flags=re.I)
    html = assets_in_text(html)
    # بررسی نهایی: هر چیزی که هنوز به بیرون وصل است یعنی آفلاین کامل نشده
    left = re.findall(r"""<(?:script|link|img)\b[^>]*\b(?:src|href)\s*=\s*["'](?!data:|#|\$)[^"']+""", html, re.I)
    for css in re.findall(r"<style[^>]*>(.*?)</style>", html, re.S):
        left += re.findall(r"""url\(\s*(?!['"]?(?:data:|#))\S+?\)|@import""", css)
    if left:
        sys.exit(f"آفلاین کامل نشد، موارد باقی‌مانده: {left[:5]}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"{out}  ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    a = sys.argv[1:]
    build(Path(a[0]) if a else ROOT / "index.html", Path(a[1]) if len(a) > 1 else ROOT / "dist" / f"{ROOT.name}-offline.html")
