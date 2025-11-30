#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import json
import random
import requests
import pandas as pd
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import os
import re
import traceback

from playwright.sync_api import sync_playwright, TimeoutError as PlayTimeoutError


# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7711722254:AAFV4bj2aQtbVKpa1gkMUyqlhkCzytRoubg")
CHAT_ID = os.getenv("CHAT_ID", "-1002428790704")
TAG = os.getenv("AFFILIATE_TAG", "crt06f-21")

EXCEL_FILE = "products.xlsx"
LOG_FILE = "log.txt"

SENT_DIR = "sent"
SENT_HISTORY = "sent_history.json"
NO_REPEAT_DAYS = 15

KEYWORDS = [
    "Hogar", "ropa", "juguetes", "juegos", "bebé", "deporte"
]

MIN_DISCOUNT_PCT = 10
BLACK_FRIDAY_PCT = 30


# ---------------- LOGGING ----------------
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass


# ---------------- UTILS ----------------
def ensure_dirs():
    if not os.path.exists(SENT_DIR):
        os.makedirs(SENT_DIR, exist_ok=True)


def load_history():
    if not os.path.exists(SENT_HISTORY):
        return {}
    try:
        with open(SENT_HISTORY, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_history(hist):
    try:
        with open(SENT_HISTORY, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"Error saving history: {e}")


def was_sent_recently(asin, history):
    if asin not in history:
        return False
    try:
        dt = datetime.fromisoformat(history[asin])
        return datetime.now() - dt < timedelta(days=NO_REPEAT_DAYS)
    except:
        return False


def register_sent(asin, history):
    history[asin] = datetime.now().isoformat()
    save_history(history)


# ---------------- ASIN ----------------
def extract_asin(url):
    try:
        m = re.search(r"/dp/([A-Z0-9]{10})", url)
        if m: return m.group(1)
        m = re.search(r"/gp/product/([A-Z0-9]{10})", url)
        if m: return m.group(1)
        m = re.search(r"/([A-Z0-9]{10})(?:[/?]|$)", url)
        if m: return m.group(1)
    except:
        return None
    return None


def affiliate_url(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG}&linkCode=ogi&th=1&psc=1"


def clean_price(text):
    if not text:
        return None
    text = text.replace("€", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(re.findall(r"[0-9]+(?:\.[0-9]+)?", text)[0])
    except:
        return None


# ---------------- PLAYWRIGHT SCRAPER ----------------
def get_html_playwright(url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True,
                                        args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = browser.new_context(
                user_agent=random.choice([
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
                    "Mozilla/5.0 (X11; Linux x86_64)...",
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)...",
                ]),
                locale="es-ES",
                geolocation={"latitude": 40.4168, "longitude": -3.7038},
                permissions=["geolocation"]
            )
            page = context.new_page()
            page.set_default_navigation_timeout(30000)

            page.goto(url, wait_until="networkidle")
            html = page.content()

            browser.close()
            return html
    except PlayTimeoutError:
        log("Timeout loading page")
        return None
    except Exception as e:
        log(f"Playwright error: {e}")
        return None


# ---------------- PRICE PARSER ----------------
def extract_prices(soup):
    # Current price
    selectors_current = [
        ".aok-offscreen",
        ".a-price .a-offscreen",
        "#price_inside_buybox",
        "#priceblock_ourprice",
        "#priceblock_dealprice"
    ]

    price_current = None
    for sel in selectors_current:
        tag = soup.select_one(sel)
        if tag:
            price_current = clean_price(tag.get_text(strip=True))
            if price_current:
                break

    # Previous price
    price_prev = None
    prev_selectors = [
        ".a-price.a-text-price .a-offscreen",
        ".a-price.a-text-price.srpPriceBlockAUI .a-offscreen"
    ]
    for sel in prev_selectors:
        tag = soup.select_one(sel)
        if tag:
            price_prev = clean_price(tag.get_text(strip=True))
            if price_prev:
                break

    # Discount
    discount_tag = soup.select_one(".savingsPercentage")
    if discount_tag:
        discount = clean_price(discount_tag.get_text(strip=True))
    elif price_current and price_prev:
        discount = round((price_prev - price_current) / price_prev * 100)
    else:
        discount = 0

    return price_current, price_prev, discount


# ---------------- PRODUCT SCRAPER ----------------
def get_product_info(url):
    asin = extract_asin(url)
    if not asin:
        return None

    html = get_html_playwright(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.select_one("#productTitle") or soup.select_one("h1 span")
    title = title_tag.get_text(strip=True) if title_tag else "No title"

    image_tag = soup.select_one("#landingImage") or soup.select_one("img.s-image")
    image = image_tag.get("src") if image_tag else None

    price_current, price_prev, discount = extract_prices(soup)
    if not price_current:
        return None
    if discount < MIN_DESCUENTO_PCT:
        return None

    return {
        "asin": asin,
        "title": title,
        "image": image,
        "price_current": price_current,
        "price_prev": price_prev,
        "discount": discount,
        "url": affiliate_url(asin)
    }


# ---------------- TELEGRAM ----------------
def send_telegram(product):
    if not TELEGRAM_TOKEN:
        log("Telegram token missing.")
        return

    try:
        bf_msg = "🔥🔥🔥 BLACK FRIDAY 🔥🔥🔥\n\n" if product["discount"] > BLACK_FRIDAY_PCT else ""

        caption = (
            f"{bf_msg}"
            f"<b>{product['title']}</b>\n\n"
            f"<b>💰 Price:</b> {product['price_current']} €\n"
        )

        if product["price_prev"]:
            caption += f"<b>📉 Previous price:</b> {product['price_prev']} €\n"

        caption += f"<b>🔥 Discount:</b> -{product['discount']}%\n\n"
        caption += product["url"]

        # Download image
        img_bytes = requests.get(product["image"], timeout=10).content

        files = {"photo": ("image.jpg", img_bytes)}

        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files=files,
            timeout=15
        )

        if r.status_code == 200:
            log(f"Sent Telegram: {product['asin']}")
        else:
            log(f"Telegram error {r.status_code}: {r.text}")

    except Exception as e:
        log(f"Telegram send error: {e}")


# ---------------- MAIN LOOP ----------------
def main_loop():
    ensure_dirs()
    history = load_history()

    while True:
        try:
            keyword = random.choice(KEYWORDS)
            search_url = f"https://www.amazon.es/s?k={keyword}"

            log(f"Searching: {keyword}")

            html = get_html_playwright(search_url)
            if not html:
                time.sleep(5)
                continue

            soup = BeautifulSoup(html, "html.parser")
            links = soup.select("a.a-link-normal.s-no-outline, h2 a")

            urls = []
            for a in links:
                href = a.get("href", "")
                if "/dp/" in href:
                    urls.append("https://www.amazon.es" + href)

            urls = list(set(urls))
            log(f"Found {len(urls)} URLs")

            for url in urls:
                product = get_product_info(url)
                if product and not was_sent_recently(product["asin"], history):
                    send_telegram(product)
                    register_sent(product["asin"], history)
                    time.sleep(10)

            log("Waiting 5 minutes...")
            time.sleep(300)

        except Exception as e:
            log(f"Unexpected error: {e}")
            log(traceback.format_exc())
            time.sleep(10)


if __name__ == "__main__":
    log("🚀 Amazon Telegram Bot started (Playwright mode)")
    main_loop()








