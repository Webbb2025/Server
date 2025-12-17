#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import random
import time
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import os
import json

# ==================================================
# =================== MODOS ========================
# ==================================================
MODO = "ULTRA"

MODOS_CONFIG = {
    "ULTRA": {"min_intervalo": 3600, "max_intervalo": 7200, "min_descuento": 7, "max_envios_dia": 10},
    "SAFE": {"min_intervalo": 2700, "max_intervalo": 5400, "min_descuento": 5, "max_envios_dia": 20},
    "NORMAL": {"min_intervalo": 1800, "max_intervalo": 3600, "min_descuento": 3, "max_envios_dia": 35}
}

CFG = MODOS_CONFIG[MODO]

# ==================================================
# ================= CONFIG =========================
# ==================================================
CATEGORIAS = {
    "hogar": ["🏠 Oferta en hogar:", "✨ Productos para tu casa:", "💡 Ideas de hogar:"],
    "electronica": ["💻 Electrónica destacada:", "📱 Gadgets con descuento:", "🎧 Tech en oferta:"],
    "deporte": ["🏃‍♂️ Deportes y fitness:", "⚽ Ofertas deportivas:", "🏋️ Equipo de entrenamiento:"],
    "cocina": ["🍳 Cocina en oferta:", "🥘 Utensilios rebajados:", "🍴 Oferta culinaria:"],
    "bricolaje": ["🔨 Bricolaje y herramientas:", "🛠️ Herramientas en oferta:", "🏗️ DIY con descuento:"],
    "oficina": ["📎 Oficina y papelería:", "🖋️ Productos de oficina rebajados:", "🗂️ Oferta para tu escritorio:"]
}

PALABRAS_CLAVE = list(CATEGORIAS.keys())

HEADERS = [
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/121.0"},
    {"User-Agent": "Mozilla/5.0 (Macintosh) Safari/605.1.15"},
]

TAG_AFILIADO = "crt06f-21"
HORA_INICIO = 7
HORA_FIN = 23

HISTORIAL_FILE = "enviados_historial.json"
ENVIO_DIARIO_FILE = "envios_diarios.json"

TELEGRAM_TOKEN = "7711722254:AAFAscovZ44PJpbYuJHKVgFevSNy-himSc4"
CHAT_ID = "@Milofertazos"

# ==================================================
# ================= UTILIDADES =====================
# ==================================================
def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def horario_permitido():
    h = datetime.now().hour
    return HORA_INICIO <= h < HORA_FIN

def cargar_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def guardar_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ==================================================
# ============ CONTROL DE RIESGO ===================
# ==================================================
def evaluar_riesgo():
    envios = cargar_json(ENVIO_DIARIO_FILE, {})
    hoy = datetime.now().strftime("%Y-%m-%d")
    enviados_hoy = envios.get(hoy, 0)
    if enviados_hoy >= CFG["max_envios_dia"]:
        log("RIESGO: límite diario alcanzado → esperando hasta mañana")
        return False
    return True

def registrar_envio():
    envios = cargar_json(ENVIO_DIARIO_FILE, {})
    hoy = datetime.now().strftime("%Y-%m-%d")
    envios[hoy] = envios.get(hoy, 0) + 1
    guardar_json(ENVIO_DIARIO_FILE, envios)

# ==================================================
# ============== AMAZON ============================
# ==================================================
def extract_asin(url):
    m = re.search(r"/dp/([A-Z0-9]{10})", url)
    return m.group(1) if m else None

def crear_url(asin):
    return f"https://www.amazon.es/dp/{asin}?tag={TAG_AFILIADO}"

def get_html(url):
    try:
        time.sleep(random.uniform(1.5, 3))
        r = requests.get(url, headers=random.choice(HEADERS), timeout=20)
        return r.text if r.status_code == 200 else None
    except:
        return None

def parse_precio(txt):
    if not txt:
        return None
    txt = txt.replace("€", "").replace(",", ".")
    try:
        return float(re.findall(r"[\d\.]+", txt)[0])
    except:
        return None

def extraer_precios(soup):
    act = soup.select_one(".aok-offscreen")
    ant = soup.select_one(".a-price.a-text-price .a-offscreen")
    p_act = parse_precio(act.text) if act else None
    p_ant = parse_precio(ant.text) if ant else None
    desc = round((p_ant - p_act) / p_ant * 100) if p_act and p_ant else 0
    return p_act, p_ant, desc

# ==================================================
# ============== ROTACION DE TEXTOS =================
# ==================================================
def generar_mensaje_rotado(p, categoria):
    hora = datetime.now().hour
    intro_variants = CATEGORIAS.get(categoria, ["🔹 Oferta destacada:"])
    if 9 <= hora < 12:
        intro = random.choice(intro_variants) + " "
    elif 12 <= hora < 18:
        intro = random.choice(intro_variants) + " "
    else:
        intro = random.choice(intro_variants) + " "

    price_variants = [
        f"💰 Precio ahora: {p['precio']} €",
        f"💸 Se queda en {p['precio']} €",
        f"💶 Disponible por {p['precio']} €"
    ]
    discount_variants = [
        f"🔥 Descuento del {p['descuento']}%",
        f"📉 Rebaja de {p['descuento']}%",
        ""
    ]
    cta_variants = ["👉 Ver en Amazon", "🛒 Comprar ahora", "🔗 Enlace directo"]

    bloques = [
        intro,
        f"{p['titulo']}",
        random.choice(price_variants) + " " + random.choice(discount_variants),
        random.choice(cta_variants) + f"\n{p['url']}"
    ]
    bloques = [b for b in bloques if b.strip()]
    random.shuffle(bloques)
    return "\n\n".join(bloques)

# ==================================================
# ============== TELEGRAM ==========================
# ==================================================
def enviar_telegram(p, categoria):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        log("TOKEN o CHAT_ID no configurado. Saltando envío Telegram.")
        return
    try:
        img_resp = requests.get(p["imagen"], timeout=20)
        img_resp.raise_for_status()
        img_bytes = img_resp.content
        files = {"photo": ("image.jpg", img_bytes)}

        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": generar_mensaje_rotado(p, categoria)},
            files=files,
            timeout=30
        )

        if r.status_code == 200:
            log(f"Mensaje enviado correctamente | ASIN {p['asin']}")
            registrar_envio()
        else:
            log(f"Error Telegram {r.status_code}: {r.text}")

    except Exception as e:
        log(f"ERROR enviando Telegram {p.get('asin','?')}: {e}")

# ==================================================
# ============== BUSQUEDA ==========================
# ==================================================
def buscar_productos():
    categoria = random.choice(PALABRAS_CLAVE)
    urls = set()
    for page in range(1, 4):
        html = get_html(f"https://www.amazon.es/s?k={categoria}&page={page}")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='/dp/']"):
            asin = extract_asin(a.get("href", ""))
            if asin:
                urls.add((f"https://www.amazon.es/dp/{asin}", categoria))
    return list(urls)

def obtener_producto(url, categoria, historial):
    asin = extract_asin(url)
    if not asin or asin in historial:
        return None
    html = get_html(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    precio, _, desc = extraer_precios(soup)
    if not precio or desc < CFG["min_descuento"]:
        return None
    titulo = soup.select_one("#productTitle")
    imagen = soup.select_one("#landingImage")
    historial.add(asin)
    guardar_json(HISTORIAL_FILE, list(historial))
    return {
        "asin": asin,
        "titulo": titulo.text.strip() if titulo else "Producto Amazon",
        "precio": precio,
        "descuento": desc,
        "imagen": imagen["src"] if imagen else None,
        "url": crear_url(asin),
        "categoria": categoria
    }

# ==================================================
# ================= MAIN CONTINUO =================
# ==================================================
def main_loop():
    historial = set(cargar_json(HISTORIAL_FILE, []))
    log(f"==============================================")
    log(f"Iniciando bucle continuo seguro (modo CRON SAFE)")

    while True:
        if not horario_permitido():
            log("Fuera de horario permitido. Esperando 10 min...")
            time.sleep(600)
            continue

        if not evaluar_riesgo():
            log("Límite diario alcanzado. Esperando 30 min...")
            time.sleep(1800)
            continue

        urls_categorias = buscar_productos()
        if not urls_categorias:
            log("No se encontraron productos. Esperando 10 min...")
            time.sleep(600)
            continue

        for url, categoria in urls_categorias:
            p = obtener_producto(url, categoria, historial)
            if p:
                enviar_telegram(p, categoria)
                # Espera segura antes de siguiente envío para no bloquear Telegram
                intervalo = random.randint(CFG["min_intervalo"], CFG["max_intervalo"])
                log(f"Esperando {intervalo // 60} min antes del siguiente envío...")
                time.sleep(intervalo)
                break  # envía solo un producto por ciclo

if __name__ == "__main__":
    main_loop()


