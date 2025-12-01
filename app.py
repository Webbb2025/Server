#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import random
import time
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import os
import json
import traceback
import re

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = "7711722254:AAFV4bj2aQtbVKpa1gkMUyqlhkCzytRoubg"
CHAT_ID = "-1002428790704"
TAG = "crt06f-21"
ZENROWS_API_KEY = "f0835c15823974a7f89cccf8f927d523436cd104"

EXCEL_FILE = "productos.xlsx"
LOG_FILE = "log.txt"
ENVIADOS_DIR = "enviados"
HISTORIAL_FILE = "enviados_historial.json"
NO_REPEAT_DAYS = 15

PALABRAS_CLAVE = [
    "Hogar", "ropa", "juguetes", "juegos", "bebé", "deporte"
]

MIN_DESCUENTO_PCT = 10

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0 Safari/537.36",
]

# ---------------- UTILIDADES ----------------
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def ensure_dirs():
    if not os.path.exists(ENVIADOS_DIR):
        os.makedirs(ENVIADOS_DIR, exist_ok=True)

def cargar_historial():
    if not os.path.exists(HISTORIAL_FILE):
        return {}
    try:
        with open(HISTORIAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def guardar_historial(hist):
    try:
        with open(HISTORIAL_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"Error guardando historial: {e}")

def fue_enviado_recientemente(asin, historial):
    if asin not in historial:
        return False
    try:
        fecha = datetime.fromisoformat(historial[asin])
        return datetime.now() - fecha < timedelta(days=NO_REPEAT_DAYS)
    except:
        return False

def registrar_envio(asin, historial):
    historial[asin] = datetime.now().isoformat()
    guardar_historial(hist)


# ---------------- SCRAPING CON ZENROWS ----------------
def scraperapi_get(url):
    """Scraping antibloqueo usando ZenRows."""

    headers = {"User-Agent": random.choice(USER_AGENTS)}

    api_url = (
        f"https://api.zenrows.com/v1/?apikey={ZENROWS_API_KEY}"
        f"&url={requests.utils.quote(url)}"
        f"&antibot=true&premium_proxy=true"
    )

    try:
        r = requests.get(api_url, headers=headers, timeout=60)
        if r.status_code == 200:
            return r.text

        log(f"ZenRows error {r.status_code}: {r.text}")
        return None

    except Exception as e:
        log(f"Error ZenRows GET {url}: {e}")
        return None


# ---------------- PARSEO ----------------
def parse_number_like_amazon(text):
    if not text:
        return None
    text = text.replace("\xa0", "").replace("\u202f", "").replace("€", "").strip()
    text = text.replace(",", ".")
    try:
        return float(re.findall(r"[\d\.]+", text)[0])
    except:
        return None

def extraer_precios(soup):
    precio_actual_tag = soup.select_one(".a-offscreen")
    precio_actual = parse_number_like_amazon(precio_actual_tag.get_text(strip=True)) if precio_actual_tag else None

    precio_anterior_tag = soup.select_one(".a-text-price .a-offscreen")
    precio_anterior = parse_number_like_amazon(precio_anterior_tag.get_text(strip=True)) if precio_anterior_tag else None

    if precio_actual and precio_anterior:
        descuento = round((precio_anterior - precio_actual) / precio_anterior * 100)
    else:
        descuento = 0

    return precio_actual, precio_anterior, descuento

def extract_asin(url):
    try:
        m = re.search(r"/dp/([A-Z0-9]{10})", url)
        if m: return m.group(1)
        m = re.search(r"/gp/product/([A-Z0-9]{10})", url)
        if m: return m.group(1)
    except:
        return None
    return None

def crear_url_afiliado(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG}&psc=1"


# ---------------- BUSCAR PRODUCTOS ----------------
def buscar_productos():
    keyword = random.choice(PALABRAS_CLAVE)
    pagina = random.randint(1, 3)
    log(f"🔎 Buscando '{keyword}' página {pagina}...")

    search_url = f"https://www.amazon.es/s?k={requests.utils.requote_uri(keyword)}&page={pagina}"

    html = scraperapi_get(search_url)
    if not html:
        log("Sin HTML en búsqueda")
        return []

    soup = BeautifulSoup(html, "html.parser")

    enlaces = soup.select("a.a-link-normal.s-no-hover, h2 a.a-link-normal")
    urls = set()

    for a in enlaces:
        href = a.get("href", "")
        if "/dp/" in href:
            asin = extract_asin(href)
            if asin:
                urls.add("https://www.amazon.es" + href)

    urls = sorted(urls)
    log(f"URLs encontradas: {len(urls)}")

    return urls


# ---------------- INFO PRODUCTO ----------------
def get_product_info(url):
    asin = extract_asin(url)
    if not asin:
        return None

    html = scraperapi_get(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    titulo_tag = soup.select_one("#productTitle")
    titulo = titulo_tag.get_text(" ", strip=True) if titulo_tag else "Sin título"

    imagen_tag = soup.select_one("#landingImage")
    imagen = imagen_tag.get("src") if imagen_tag else None

    precio_actual, precio_anterior, descuento = extraer_precios(soup)

    if not precio_actual:
        return None
    if descuento < MIN_DESCUENTO_PCT:
        return None

    return {
        "asin": asin,
        "titulo": titulo,
        "imagen": imagen,
        "precio_actual": precio_actual,
        "precio_anterior": precio_anterior,
        "descuento": descuento,
        "url": crear_url_afiliado(asin),
    }


# ---------------- TELEGRAM ----------------
def enviar_telegram(producto):
    try:
        caption = (
            f"<b>{producto['titulo']}</b>\n\n"
            f"<b>💰 Precio:</b> {producto['precio_actual']}€\n"
            f"<b>🔥 Descuento:</b> -{producto['descuento']}%\n\n"
            f"{producto['url']}"
        )

        img = requests.get(producto["imagen"], timeout=20).content

        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("img.jpg", img)},
        )

        if r.status_code == 200:
            log(f"Enviado Telegram: {producto['asin']}")
        else:
            log(f"Error Telegram {r.status_code}: {r.text}")

    except Exception as e:
        log(f"ERROR Telegram {producto['asin']}: {e}")


# ---------------- BUCLE PRINCIPAL ----------------
def main_loop():
    ensure_dirs()
    historial = cargar_historial()

    while True:
        try:
            urls = buscar_productos()
            if not urls:
                time.sleep(10)
                continue

            for url in urls:
                p = get_product_info(url)

                if p and not fue_enviado_recientemente(p["asin"], historial):
                    enviar_telegram(p)
                    registrar_envio(p["asin"], historial)
                    log("⏳ Esperando 10 minutos...")
                    time.sleep(600)

            log("🔁 Ciclo completo. Pausa 10 min...\n")
            time.sleep(600)

        except Exception as e:
            log(f"ERROR inesperado: {e}")
            log(traceback.format_exc())
            time.sleep(30)


if __name__ == "__main__":
    log("🚀 Sistema Amazon iniciado con ZenRows (antibot activado).")
    main_loop()
