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


EXCEL_FILE = "productos.xlsx"
LOG_FILE = "log.txt"

ENVIADOS_DIR = "enviados"
HISTORIAL_FILE = "enviados_historial.json"
NO_REPEAT_DAYS = 15  # NO repetir el mismo producto en 15 días

PALABRAS_CLAVE = [
    "Hogar",
    "ropa",
    "juguetes",
    "juegos",
    "bebé",
    "deporte"
]

HEADERS_ROTATIVOS = [
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/121.0"},
    {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15"},
]

MIN_DESCUENTO_PCT = 10
BLACK_FRIDAY_PCT = 30

# ----------------- UTILIDADES -----------------
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
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
        fecha_envio = datetime.fromisoformat(historial[asin])
        return datetime.now() - fecha_envio < timedelta(days=NO_REPEAT_DAYS)
    except:
        return False

def registrar_envio(asin, historial):
    historial[asin] = datetime.now().isoformat()
    guardar_historial(historial)

# ----------------- ASIN & URL -----------------
def extract_asin(url):
    try:
        m = re.search(r"/dp/([A-Z0-9]{10})", url)
        if m:
            return m.group(1)
        m = re.search(r"/gp/product/([A-Z0-9]{10})", url)
        if m:
            return m.group(1)
        m = re.search(r"/([A-Z0-9]{10})(?:[/?]|$)", url)
        if m:
            return m.group(1)
    except:
        return None
    return None

def crear_url_afiliado(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG}&linkCode=ogi&th=1&psc=1"

def crear_url_scrape(asin):
    return f"https://www.amazon.es/dp/{asin}"

# ----------------- HTTP -----------------
def scraperapi_get(url):
    headers = random.choice(HEADERS_ROTATIVOS)
    try:
        time.sleep(random.uniform(1.5, 3.0))
        r = requests.get(url, headers=headers, timeout=25)
        if r.status_code == 200:
            return r.text
        if r.status_code in (403, 503):
            log(f"Amazon bloqueó ({r.status_code}). Reintentando con otro header...")
            time.sleep(random.uniform(2, 5))
            headers = random.choice(HEADERS_ROTATIVOS)
            r = requests.get(url, headers=headers, timeout=25)
            if r.status_code == 200:
                return r.text
        log(f"Error HTTP {r.status_code} para {url}")
        return None
    except Exception as e:
        log(f"Error GET {url}: {e}")
        return None

# ----------------- PARSERS -----------------
def parse_number_like_amazon(text):
    if not text:
        return None
    text = text.replace("\xa0", "").replace("\u202f", "").strip()
    patterns = [
        r"\d{1,3}(?:[.\s]\d{3})*,\d{1,2}",
        r"\d+(?:,\d{1,2})",
        r"\d{1,3}(?:[.\s]\d{3})+",
        r"\d+"
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            raw = m.group(0).replace(" ", "").replace(".", "").replace(",", ".")
            try:
                return float(raw)
            except:
                continue
    return None

def extraer_precios(soup):
    precio_actual = None
    precio_anterior = None

    selectores_actual = [
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#corePrice_feature_div .a-price .a-offscreen",
        "span[data-a-color='price'] span.a-offscreen",
        "#price_inside_buybox",
        "#newBuyBoxPrice"
    ]
    for sel in selectores_actual:
        tag = soup.select_one(sel)
        if tag:
            precio_actual = parse_number_like_amazon(tag.get_text(" ", strip=True))
            if precio_actual:
                break
    if not precio_actual:
        candidatos = []
        for t in soup.select(".a-price .a-offscreen, #price_inside_buybox, #newBuyBoxPrice, .a-offscreen"):
            val = parse_number_like_amazon(t.get_text(" ", strip=True))
            if val:
                candidatos.append(val)
        if candidatos:
            precio_actual = candidatos[0]

    selectores_antes = [
        "#corePrice_desktop .a-text-price .a-offscreen",
        "#corePrice_feature_div .a-text-price .a-offscreen",
        "#priceblock_listprice .a-offscreen",
        "span[data-a-strike='true'] .a-offscreen",
        ".priceBlockStrikePriceString"
    ]
    for sel in selectores_antes:
        tag = soup.select_one(sel)
        if tag:
            precio_anterior = parse_number_like_amazon(tag.get_text(" ", strip=True))
            if precio_anterior and precio_actual and precio_anterior > precio_actual:
                break
    if precio_actual and not precio_anterior:
        posibles = []
        for t in soup.select(".a-text-price .a-offscreen, span[data-a-strike='true'] .a-offscreen, .priceBlockStrikePriceString"):
            val = parse_number_like_amazon(t.get_text(" ", strip=True))
            if val and val > precio_actual:
                posibles.append(val)
        if posibles:
            precio_anterior = max(posibles)

    if not precio_actual:
        return None, None
    if not precio_anterior or precio_anterior <= precio_actual:
        return precio_actual, None
    return precio_actual, precio_anterior

def formatear_precio_europeo(valor):
    if valor is None:
        return "No disponible"
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"

# ----------------- BÚSQUEDA -----------------
def buscar_productos(keyword=None):
    if not keyword:
        keyword = random.choice(PALABRAS_CLAVE)
    pagina = random.randint(1, 3)
    log(f"🔎 Buscando '{keyword}' página {pagina}...")
    search_url = f"https://www.amazon.es/s?k={requests.utils.requote_uri(keyword)}&page={pagina}"
    html = scraperapi_get(search_url)
    if not html:
        log("Sin HTML de búsqueda")
        return []
    soup = BeautifulSoup(html, "html.parser")
    enlaces = soup.select("a.a-link-normal.s-no-hover.s-underline-text.s-underline-link-text, a.a-link-normal.s-no-outline, h2 a.a-link-normal")
    urls = set()
    for a in enlaces:
        href = a.get("href", "")
        if not href:
            continue
        if href.startswith("/"):
            href = "https://www.amazon.es" + href
        if "/dp/" in href or "/gp/product/" in href:
            asin = extract_asin(href)
            if asin:
                urls.add(crear_url_scrape(asin))
    urls = sorted(list(urls))
    log(f"URLs encontradas: {len(urls)}")
    return urls

# ----------------- INFO PRODUCTO -----------------
def get_product_info(url):
    asin = extract_asin(url)
    if not asin:
        return None
    html = scraperapi_get(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    titulo_tag = (soup.select_one("#productTitle")
                  or soup.select_one("span.a-size-large.product-title-word-break")
                  or soup.select_one("span.a-size-medium.a-color-base.a-text-normal")
                  or soup.select_one("h1 span"))
    titulo = titulo_tag.get_text(" ", strip=True) if titulo_tag else "Sin título"
    imagen_tag = (soup.select_one("#landingImage")
                  or soup.select_one("img#imgBlkFront")
                  or soup.select_one("img.s-image")
                  or soup.select_one("div#imgTagWrapperId img"))
    imagen = None
    if imagen_tag:
        imagen = imagen_tag.get("src") or imagen_tag.get("data-src")
    precio_actual, precio_anterior = extraer_precios(soup)
    if not precio_actual or not precio_anterior or precio_anterior <= precio_actual:
        return None
    descuento = round((precio_anterior - precio_actual) / precio_anterior * 100)
    if descuento < MIN_DESCUENTO_PCT:
        return None
    if not imagen:
        return None
    producto = {
        "asin": asin,
        "titulo": titulo,
        "imagen": imagen,
        "precio_actual": precio_actual,
        "precio_anterior": precio_anterior,
        "descuento": descuento,
        "url_scrape": url,
        "url": crear_url_afiliado(asin)
    }
    log(f"Producto OK: {asin} | -{descuento}% | {formatear_precio_europeo(precio_actual)} (antes {formatear_precio_europeo(precio_anterior)})")
    return producto

# ----------------- TELEGRAM -----------------
def enviar_telegram(producto):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("TOKEN o CHAT_ID no configurado. Saltando envío Telegram.")
        return
    try:
        bf_msg = "🔥🔥🔥 <b>BLACK FRIDAY</b> 🔥🔥🔥\n\n" if producto['descuento'] > BLACK_FRIDAY_PCT else ""
        caption = (
            f"{bf_msg}<b>{producto['titulo']}</b>\n\n"
            f"<b>💰 Precio actual:</b> {formatear_precio_europeo(producto['precio_actual'])}\n"
            f"<b>📉 Precio anterior:</b> {formatear_precio_europeo(producto['precio_anterior'])}\n"
            f"<b>🔥 -{producto['descuento']}% de descuento</b>\n\n"
            f"🛒 <a href=\"{producto['url']}\">{producto['url']}</a>"
        )
        img_resp = requests.get(producto['imagen'], timeout=20)
        img_resp.raise_for_status()
        files = {"photo": ("image.jpg", img_resp.content)}
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML", "disable_web_page_preview": "false"},
            files=files,
            timeout=30
        )
        if r.status_code == 200:
            log(f"Enviado Telegram: {producto['asin']}")
        else:
            log(f"Error Telegram {r.status_code}: {r.text}")
    except Exception as e:
        log(f"ERROR enviando Telegram {producto.get('asin','?')}: {e}")

# ----------------- GUARDADO -----------------
def deduplicar_y_guardar(productos):
    asin_map = {p["asin"]: p for p in productos}
    lista = list(asin_map.values())
    if not lista:
        return
    df = pd.DataFrame(lista)
    df['precio_actual'] = df['precio_actual'].apply(lambda x: formatear_precio_europeo(x))
    df['precio_anterior'] = df['precio_anterior'].apply(lambda x: formatear_precio_europeo(x))
    try:
        df.to_excel(EXCEL_FILE, index=False)
    except Exception as e:
        log(f"Error guardando Excel: {e}")

# ----------------- BUCLE PRINCIPAL CON REINTENTOS -----------------
def main_loop():
    ensure_dirs()
    historial = cargar_historial()
    while True:
        try:
            keyword = random.choice(PALABRAS_CLAVE)
            intentos = 0
            urls = []

            # Reintentos si no hay URLs
            while intentos < 3 and not urls:
                log(f"🔎 Buscando '{keyword}' (intento {intentos+1}/3)...")
                urls = buscar_productos(keyword)
                if urls:
                    break
                intentos += 1
                time.sleep(5)

            if not urls:
                log(f"⚠️ No se encontraron URLs para '{keyword}' tras {intentos} intentos. Probando otra keyword en 15 segundos...")
                time.sleep(15)
                continue

            productos_encontrados = []
            for url in urls:
                p = get_product_info(url)
                if p and not fue_enviado_recientemente(p["asin"], historial):
                    enviar_telegram(p)
                    registrar_envio(p["asin"], historial)
                    productos_encontrados.append(p)
                    log("⏳ Esperando 10 minutos antes del siguiente envío...")
                    time.sleep(10 * 60)

            if productos_encontrados:
                deduplicar_y_guardar(productos_encontrados)

            log("⏳ Ciclo terminado. Esperando 10 minutos antes de la siguiente palabra clave...\n")
            time.sleep(10 * 60)

        except KeyboardInterrupt:
            log("Interrupción por teclado")
            break
        except Exception as e:
            log(f"ERROR inesperado: {e}")
            log(traceback.format_exc())
            time.sleep(60)

if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("⚠️ Atención: TELEGRAM_TOKEN o CHAT_ID no configurado.")
    log("🚀 Sistema Amazon iniciado (precios corregidos).")
    main_loop()
