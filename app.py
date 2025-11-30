#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Amazon scraper using Playwright (synchronous) that extracts prices reliably (buybox, coupons,
recommended/list price, 30-day low) and sends Telegram messages with affiliate link.
ASINs are validated to start with "B0" only.

Requirements:
    pip install playwright requests beautifulsoup4 pandas
    playwright install --with-deps

Set environment variables:
    TELEGRAM_TOKEN, CHAT_ID, AFFILIATE_TAG (optional, defaults to crt06f-21)
"""

import os
import re
import json
import time
import random
import traceback
import requests
import pandas as pd
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

from playwright.sync_api import sync_playwright, TimeoutError as PlayTimeoutError

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7711722254:AAFV4bj2aQtbVKpa1gkMUyqlhkCzytRoubg")
CHAT_ID = os.getenv("CHAT_ID", "-1002428790704")
TAG = os.getenv("AFFILIATE_TAG", "crt06f-21")

EXCEL_FILE = "productos.xlsx"
LOG_FILE = "log.txt"
ENVIADOS_DIR = "enviados"
HISTORIAL_FILE = "enviados_historial.json"
NO_REPEAT_DAYS = int(os.getenv("NO_REPEAT_DAYS", "15"))

KEYWORDS = ["Hogar", "ropa", "juguetes", "juegos", "bebé", "deporte"]

MIN_DISCOUNT_PCT = int(os.getenv("MIN_DISCOUNT_PCT", "10"))
BLACK_FRIDAY_PCT = int(os.getenv("BLACK_FRIDAY_PCT", "30"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
]

# ---------------- HELPERS ----------------
def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def ensure_dirs():
    os.makedirs(ENVIADOS_DIR, exist_ok=True)

def load_history():
    if not os.path.exists(HISTORIAL_FILE):
        return {}
    try:
        with open(HISTORIAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_history(hist):
    try:
        with open(HISTORIAL_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"Error saving history: {e}")

def was_sent_recently(asin, history):
    if asin not in history:
        return False
    try:
        d = datetime.fromisoformat(history[asin])
        return datetime.now() - d < timedelta(days=NO_REPEAT_DAYS)
    except:
        return False

def register_sent(asin, history):
    history[asin] = datetime.now().isoformat()
    save_history(history)

# ---------------- ASIN / URL ----------------
def extract_asin(url: str):
    """
    Return ASIN only if it starts with B0 and matches 10-char Amazon ASIN pattern.
    """
    if not url:
        return None
    try:
        patterns = [
            r"/dp/(B0[A-Z0-9]{8})",
            r"/gp/product/(B0[A-Z0-9]{8})",
            r"/(B0[A-Z0-9]{8})(?:[/?]|$)"
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                asin = m.group(1)
                if asin and asin.startswith("B0") and len(asin) == 10:
                    return asin
    except:
        return None
    return None

def affiliate_url(asin: str):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG}&linkCode=ogi&th=1&psc=1"

def scrape_url_for_asin_only(href: str):
    """
    Normalize relative Amazon hrefs to absolute dp urls for scraping.
    """
    if href.startswith("/"):
        return "https://www.amazon.es" + href
    if href.startswith("http"):
        return href
    return None

# ---------------- PLAYWRIGHT PAGE FETCH ----------------
def fetch_rendered_html(url: str, timeout: int = 30000, retries: int = 2):
    """
    Use Playwright to get rendered HTML. Launch/close browser each call (safer in
    ephemeral environments like Railway). Returns HTML string or None.
    """
    attempt = 0
    while attempt <= retries:
        attempt += 1
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
                context = browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    locale="es-ES",
                    timezone_id="Europe/Madrid",
                    viewport={"width": 1200, "height": 800}
                )
                page = context.new_page()
                page.set_default_navigation_timeout(timeout)
                page.goto(url, wait_until="networkidle")
                # give some time for dynamic modules (coupons, savings) to appear
                time.sleep(random.uniform(0.5, 1.5))
                html = page.content()
                try:
                    context.close()
                except:
                    pass
                try:
                    browser.close()
                except:
                    pass
                return html
        except PlayTimeoutError as e:
            log(f"Playwright timeout (attempt {attempt}) for {url}: {e}")
            if attempt > retries:
                return None
            time.sleep(1 + attempt)
        except Exception as e:
            log(f"Playwright error (attempt {attempt}) for {url}: {e}")
            if attempt > retries:
                return None
            time.sleep(1 + attempt)
    return None

# ---------------- PRICE PARSING ----------------
def parse_number_like_amazon(text: str):
    if not text:
        return None
    t = text.replace("\xa0", "").replace("\u202f", "").replace("€", "").strip()
    # Replace comma decimal
    t = t.replace(",", ".")
    m = re.findall(r"[\d]+(?:\.[\d]+)?", t)
    if not m:
        return None
    try:
        return float(m[0])
    except:
        return None

def extract_prices_from_soup(soup: BeautifulSoup):
    """
    Robust extraction:
      - current price: .aok-offscreen OR a-price-whole + fraction OR buybox selectors
      - coupon price: priceBlockSavingsString or price inside #priceblock_ourprice when coupon applied
      - previous price: .a-price.a-text-price .a-offscreen OR srpPriceBlockAUI OR highest reasonable strike
      - discount: savings selector or computed
    Returns (precio_actual: float, precio_anterior: float|None, descuento:int)
    """
    precio_actual = None
    precio_anterior = None
    descuento = 0

    # 1) Try .aok-offscreen (common)
    tag = soup.select_one(".aok-offscreen")
    if tag:
        precio_actual = parse_number_like_amazon(tag.get_text(" ", strip=True))

    # 2) a-price-whole + fraction (visual)
    if not precio_actual:
        whole = soup.select_one("span.a-price > span.a-price-whole")
        frac = soup.select_one("span.a-price > span.a-price-fraction")
        if whole:
            w = whole.get_text("", strip=True).replace(".", "").replace("\xa0", "")
            f = frac.get_text("", strip=True) if frac else "00"
            try:
                precio_actual = float(w + "." + f)
            except:
                precio_actual = None

    # 3) fallback buybox selectors
    if not precio_actual:
        for sel in [
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "#price_inside_buybox",
            "#newBuyBoxPrice",
            ".a-price .a-offscreen"
        ]:
            t = soup.select_one(sel)
            if t:
                precio_actual = parse_number_like_amazon(t.get_text(" ", strip=True))
                if precio_actual:
                    break

    # 4) Coupon-specific selector (override if appears)
    cup_tag = soup.select_one("#priceBlockSavingsString, #priceBlockSavingsString_feature_div")
    if cup_tag:
        val = parse_number_like_amazon(cup_tag.get_text(" ", strip=True))
        if val and val > 0.0:
            precio_actual = val

    # PREVIOUS / RECOMMENDED PRICE
    # Primary: list price / strike price
    pa_tag = soup.select_one(".a-price.a-text-price .a-offscreen, #priceblock_listprice .a-offscreen, .priceBlockStrikePriceString")
    if pa_tag:
        precio_anterior = parse_number_like_amazon(pa_tag.get_text(" ", strip=True))
    else:
        # fallback: 30-day low / srp block
        pa_tag2 = soup.select_one(".a-price.a-text-price.srpPriceBlockAUI .a-offscreen")
        if pa_tag2:
            precio_anterior = parse_number_like_amazon(pa_tag2.get_text(" ", strip=True))
        else:
            # fallback: search for reasonable struck prices
            candidates = []
            for t in soup.select(".a-text-price .a-offscreen, span[data-a-strike='true'] .a-offscreen, .priceBlockStrikePriceString"):
                v = parse_number_like_amazon(t.get_text(" ", strip=True))
                if v and precio_actual and v > precio_actual and v < precio_actual * 5:
                    candidates.append(v)
            if candidates:
                precio_anterior = max(candidates)

    # DISCOUNT
    desc_tag = soup.select_one(".savingPriceOverride.aok-align-center.reinventPriceSavingsPercentageMargin.savingsPercentage, .savingsPercentage")
    if desc_tag:
        dval = parse_number_like_amazon(desc_tag.get_text(" ", strip=True))
        if dval:
            descuento = int(round(dval))
    elif precio_actual and precio_anterior and precio_anterior > 0:
        descuento = int(round((precio_anterior - precio_actual) / precio_anterior * 100))

    if not precio_actual:
        return None, None, 0
    return precio_actual, precio_anterior, descuento

# ---------------- SEARCH / PRODUCT HANDLERS ----------------
def extract_product_urls_from_search_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.a-link-normal.s-no-hover.s-underline-text.s-underline-link-text, a.a-link-normal.s-no-outline, h2 a.a-link-normal")
    urls = set()
    for a in anchors:
        href = a.get("href", "")
        if not href:
            continue
        normalized = scrape_url_for_asin_only(href)
        if not normalized:
            continue
        asin = extract_asin(normalized)
        if asin:
            # use scrape dp url (no affiliate tag for scraping)
            urls.add(f"https://www.amazon.es/dp/{asin}")
    return sorted(list(urls))

def get_product_info_playwright(url: str):
    """
    Fetch product page via Playwright and parse title, image, prices.
    Returns product dict or None.
    """
    asin = extract_asin(url)
    if not asin:
        return None
    html = fetch_rendered_html(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.select_one("#productTitle") or soup.select_one("h1 span")
    title = title_tag.get_text(" ", strip=True) if title_tag else "No title"

    image_tag = soup.select_one("#landingImage") or soup.select_one("img.s-image") or soup.select_one("img#imgBlkFront")
    image = image_tag.get("src") if image_tag else None

    precio_actual, precio_anterior, descuento = extract_prices_from_soup(soup)
    if not precio_actual:
        return None
    if descuento < MIN_DISCOUNT_PCT:
        return None

    product = {
        "asin": asin,
        "title": title,
        "image": image,
        "precio_actual": precio_actual,
        "precio_anterior": precio_anterior,
        "descuento": descuento,
        "url_scrape": url,
        "url": affiliate_url(asin),
    }
    log(f"Product OK: {asin} | -{descuento}% | {formatear_euro(precio_actual)} (before {formatear_euro(precio_anterior)})")
    return product

# ---------------- FORMATTING ----------------
def formatear_euro(valor):
    if valor is None:
        return "No disponible"
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"

# ---------------- TELEGRAM ----------------
def send_telegram(product: dict):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("Missing TELEGRAM_TOKEN or CHAT_ID.")
        return
    try:
        bf = "🔥🔥🔥 <b>BLACK FRIDAY</b> 🔥🔥🔥\n\n" if product.get("descuento", 0) > BLACK_FRIDAY_PCT else ""
        caption = f"{bf}<b>{product.get('title')}</b>\n\n"
        caption += f"<b>💰 Price:</b> {formatear_euro(product.get('precio_actual'))}\n"
        if product.get("precio_anterior"):
            caption += f"<b>📉 Recommended:</b> {formatear_euro(product.get('precio_anterior'))}\n"
        if product.get("descuento"):
            caption += f"<b>🔥 -{product.get('descuento')}% off</b>\n\n"
        # Only show affiliate link (not a textual "Buy" label)
        caption += product.get("url")

        files = None
        if product.get("image"):
            try:
                r = requests.get(product.get("image"), timeout=20)
                r.raise_for_status()
                files = {"photo": ("image.jpg", r.content)}
            except Exception as e:
                log(f"Failed to download image: {e}")
                files = None

        data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML", "disable_web_page_preview": "false"}
        if files:
            resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=data, files=files, timeout=30)
        else:
            # fallback to sendMessage if image not available
            resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"}, timeout=30)

        if resp.status_code == 200:
            log(f"Sent Telegram: {product.get('asin')}")
        else:
            log(f"Telegram error {resp.status_code}: {resp.text}")
    except Exception as e:
        log(f"Error sending Telegram: {e}")

# ---------------- SAVE / DEDUP ----------------
def dedupe_and_save(products: list):
    asin_map = {p["asin"]: p for p in products}
    lst = list(asin_map.values())
    if not lst:
        return
    df = pd.DataFrame(lst)
    df["precio_actual"] = df["precio_actual"].apply(lambda x: formatear_euro(x))
    df["precio_anterior"] = df["precio_anterior"].apply(lambda x: formatear_euro(x))
    try:
        df.to_excel(EXCEL_FILE, index=False)
    except Exception as e:
        log(f"Error saving excel: {e}")

# ---------------- MAIN LOOP ----------------
def main_loop():
    ensure_dirs()
    history = load_history()
    while True:
        try:
            keyword = random.choice(KEYWORDS)
            page = random.randint(1, 3)
            search_url = f"https://www.amazon.es/s?k={requests.utils.requote_uri(keyword)}&page={page}"
            log(f"Searching '{keyword}' page {page}...")

            html_search = fetch_rendered_html(search_url)
            if not html_search:
                log("No search HTML (playwright). Retrying soon...")
                time.sleep(10)
                continue

            urls = extract_product_urls_from_search_html(html_search)
            log(f"Found {len(urls)} product urls.")
            if not urls:
                time.sleep(10)
                continue

            found_products = []
            for url in urls:
                # ensure ASIN starts with B0 before processing
                asin = extract_asin(url)
                if not asin:
                    continue
                # fetch product info (rendered)
                product = get_product_info_playwright(url)
                if product and not was_sent_recently(product["asin"], history):
                    send_telegram(product)
                    register_sent(product["asin"], history)
                    found_products.append(product)
                    log("Waiting 10 minutes before next send...")
                    time.sleep(10 * 60)

            if found_products:
                dedupe_and_save(found_products)

            log("Cycle finished. Waiting 10 minutes before next keyword...\n")
            time.sleep(10 * 60)

        except KeyboardInterrupt:
            log("Keyboard interrupt, exiting.")
            break
        except Exception as e:
            log(f"Unexpected error in main loop: {e}")
            log(traceback.format_exc())
            time.sleep(30)

if __name__ == "__main__":
    # small quick-check for required env
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("⚠️ TELEGRAM_TOKEN or CHAT_ID not configured.")
    log("🚀 Amazon scraper (Playwright) started.")
    main_loop()
