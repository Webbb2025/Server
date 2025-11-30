#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Scraper Amazon + Playwright -> Telegram (afiliado).
Reemplaza el requests-only scraper por Playwright para render JS y obtener precios reales.
"""

import os
import re
import json
import time
import random
import traceback
import pandas as pd
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
import requests

# Playwright (sincrónico)
from playwright.sync_api import sync_playwright, TimeoutError as PlayTimeoutError

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7711722254:AAFV4bj2aQtbVKpa1gkMUyqlhkCzytRoubg")
CHAT_ID = os.getenv("CHAT_ID", "-1002428790704")
TAG = os.getenv("AFFILIATE_TAG", "crt06f-21")

EXCEL_FILE = "productos.xlsx"
LOG_FILE = "log.txt"
ENVIADOS_DIR = "enviados"
HISTORIAL_FILE = "enviados_historial.json"
NO_REPEAT_DAYS = 15

PALABRAS_CLAVE = ["Hogar", "ropa", "juguetes", "juegos", "bebé", "deporte"]

MIN_DESCUENTO_PCT = 10
BLACK_FRIDAY_PCT = 30

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
]

# ---------------- UTILIDADES ----------------
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def ensure_dirs():
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

# ---------------- ASIN / URL ----------------
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

def crear_url_afiliado(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG}&linkCode=ogi&th=1&psc=1"

def crear_url_scrape(asin):
    return f"https://www.amazon.es/dp/{asin}"

# ---------------- Parse helpers ----------------
def parse_number_like_amazon(text):
    if not text:
        return None
    t = text.replace("\xa0", "").replace("\u202f", "").replace("€", "").strip()
    # Normalize decimal comma
    t = t.replace(",", ".")
    m = re.findall(r"[\d\.]+", t)
    if not m:
        return None
    try:
        return float(m[0])
    except:
        return None

def formatear_precio_europeo(valor):
    if valor is None:
        return "No disponible"
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"

# ---------------- Playwright fetch (rendered HTML) ----------------
def fetch_page_with_playwright(url, timeout=30000):
    """Devuelve HTML renderizado con Playwright (sincrónico)."""
    # lanzar y cerrar en cada llamada para evitar problemas en ambientes serverless
    with sync_playwright() as p:
        # usar chromium sin sandbox en plataformas gestionadas
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        # elegir user agent aleatorio y locale/es-ES
        ua = random.choice(USER_AGENTS)
        context = browser.new_context(user_agent=ua, locale="es-ES", timezone_id="Europe/Madrid", viewport={"width":1280,"height":800})
        page = context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout)
            # esperar un poco para que aparezcan módulos dinámicos
            time.sleep(random.uniform(0.5, 1.5))
            html = page.content()
            return html
        except PlayTimeoutError as e:
            log(f"Playwright timeout para {url}: {e}")
            return None
        except Exception as e:
            log(f"Playwright error para {url}: {e}")
            return None
        finally:
            try:
                context.close()
            except:
                pass
            try:
                browser.close()
            except:
                pass

# ---------------- Extraer precios (robusto) ----------------
def extraer_precios_de_soup(soup):
    """
    Lógica robusta:
     - precio actual: .aok-offscreen o .a-price-whole+fraction o selectores buybox
     - precio anterior: .a-price.a-text-price .a-offscreen o srpPriceBlockAUI
     - descuento: savings selector o calculado
    """
    precio_actual = None
    precio_anterior = None
    descuento = 0

    # 1) intentar .aok-offscreen (selector que pediste)
    tag = soup.select_one(".aok-offscreen")
    if tag:
        precio_actual = parse_number_like_amazon(tag.get_text(" ", strip=True))

    # 2) si no hay, intentar a-price-whole + fraction (visual)
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

    # 3) fallback: otros selectores de buybox
    if not precio_actual:
        for sel in [".a-price .a-offscreen", "#priceblock_ourprice", "#priceblock_dealprice", "#price_inside_buybox", "#newBuyBoxPrice"]:
            t = soup.select_one(sel)
            if t:
                precio_actual = parse_number_like_amazon(t.get_text(" ", strip=True))
                if precio_actual:
                    break

    # PRECIO ANTERIOR (recomendado / tachado)
    pa_tag = soup.select_one(".a-price.a-text-price .a-offscreen")
    if pa_tag:
        precio_anterior = parse_number_like_amazon(pa_tag.get_text(" ", strip=True))
    else:
        pa_tag = soup.select_one(".a-price.a-text-price.srpPriceBlockAUI .a-offscreen")
        if pa_tag:
            precio_anterior = parse_number_like_amazon(pa_tag.get_text(" ", strip=True))
        else:
            # fallback: tomar el mayor valor tachado visible (pero razonable)
            candidatos = []
            for t in soup.select(".a-text-price .a-offscreen, .priceBlockStrikePriceString"):
                v = parse_number_like_amazon(t.get_text(" ", strip=True))
                if v and precio_actual and v > precio_actual and v < precio_actual * 5:
                    candidatos.append(v)
            if candidatos:
                precio_anterior = max(candidatos)

    # DESCUENTO: primero selector explícito
    desc_tag = soup.select_one(".savingPriceOverride.aok-align-center.reinventPriceSavingsPercentageMargin.savingsPercentage")
    if desc_tag:
        descuento = parse_number_like_amazon(desc_tag.get_text(" ", strip=True)) or 0
    elif precio_anterior and precio_actual and precio_anterior > 0:
        descuento = round((precio_anterior - precio_actual) / precio_anterior * 100)

    return precio_actual, precio_anterior, int(descuento or 0)

# ---------------- Buscar productos en search results ----------------
def buscar_productos_html(html):
    """Extrae URLs de la página de búsqueda (HTML renderizado)."""
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
    return sorted(list(urls))

# ---------------- Información producto ----------------
def get_product_info_playwright(url):
    html = fetch_page_with_playwright(url)
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
    imagen = imagen_tag.get("src") or imagen_tag.get("data-src") if imagen_tag else None

    precio_actual, precio_anterior, descuento = extraer_precios_de_soup(soup)
    if not precio_actual:
        return None
    if descuento < MIN_DESCUENTO_PCT:
        return None

    asin = extract_asin(url) or "UNKNOWN"
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

# ---------------- TELEGRAM ----------------
def enviar_telegram(producto):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("TOKEN o CHAT_ID no configurado. Saltando envío Telegram.")
        return
    try:
        bf_msg = "🔥🔥🔥 <b>BLACK FRIDAY</b> 🔥🔥🔥\n\n" if producto['descuento'] > BLACK_FRIDAY_PCT else ""
        caption = f"{bf_msg}<b>{producto['titulo']}</b>\n\n"
        caption += f"<b>💰 Precio actual:</b> {formatear_precio_europeo(producto['precio_actual'])}\n"
        if producto.get('precio_anterior'):
            caption += f"<b>📉 Precio recomendado:</b> {formatear_precio_europeo(producto['precio_anterior'])}\n"
        if producto.get('descuento'):
            caption += f"<b>🔥 -{producto['descuento']}% de descuento</b>\n\n"
        # mostrar solo link de afiliado
        caption += producto['url']

        # enviar imagen como bytes
        if producto.get('imagen'):
            try:
                img_resp = requests.get(producto['imagen'], timeout=20)
                img_resp.raise_for_status()
                img_bytes = img_resp.content
                files = {"photo": ("image.jpg", img_bytes)}
            except Exception as e:
                log(f"No se pudo descargar imagen {producto.get('imagen')}: {e}")
                files = None
        else:
            files = None

        data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML", "disable_web_page_preview": "false"}
        if files:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", data=data, files=files, timeout=30)
        else:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=data, timeout=30)

        if r.status_code == 200:
            log(f"Enviado Telegram: {producto['asin']}")
        else:
            log(f"Error Telegram {r.status_code}: {r.text}")
    except Exception as e:
        log(f"ERROR enviando Telegram {producto.get('asin','?')}: {e}")

# ---------------- Guardado ----------------
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

# ---------------- Bucle principal ----------------
def main_loop():
    ensure_dirs()
    historial = cargar_historial()
    while True:
        try:
            # buscar en Amazon (render search page con Playwright)
            keyword = random.choice(PALABRAS_CLAVE)
            pagina = random.randint(1, 3)
            search_url = f"https://www.amazon.es/s?k={requests.utils.requote_uri(keyword)}&page={pagina}"
            log(f"🔎 Buscando '{keyword}' página {pagina}...")
            html_search = fetch_page_with_playwright(search_url)
            if not html_search:
                log("Sin HTML de búsqueda (Playwright). Reintentando pronto...")
                time.sleep(10)
                continue

            urls = buscar_productos_html(html_search)
            log(f"URLs encontradas: {len(urls)}")
            if not urls:
                time.sleep(10)
                continue

            productos_encontrados = []
            for url in urls:
                # obtener info renderizada por Playwright
                p = get_product_info_playwright(url)
                if p and not fue_enviado_recientemente(p["asin"], historial):
                    enviar_telegram(p)
                    registrar_envio(p["asin"], historial)
                    productos_encontrados.append(p)
                    log("⏳ Esperando 10 minutos antes del siguiente envío...")
                    time.sleep(10 * 60)

            if productos_encontrados:
                deduplicar_y_guardar(productos_encontrados)

            log("⏳ Ciclo terminado. Esperando 10 minutos...\n")
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
    log("🚀 Sistema Amazon iniciado (Playwright renderer).")
    main_loop()
