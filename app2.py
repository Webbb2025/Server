
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

# "ULTRA"  -> canales nuevos / riesgo cero
# "SAFE"   -> crecimiento lento
# "NORMAL" -> crecimiento estable (recomendado tras 2-3 semanas)
MODO = "ULTRA"

MODOS_CONFIG = {
    "ULTRA": {
        "min_intervalo": 3600,     # 1h
        "max_intervalo": 7200,     # 2h
        "min_descuento": 7,
        "max_envios_dia": 10
    },
    "SAFE": {
        "min_intervalo": 2700,     # 45 min
        "max_intervalo": 5400,     # 90 min
        "min_descuento": 5,
        "max_envios_dia": 20
    },
    "NORMAL": {
        "min_intervalo": 1800,     # 30 min
        "max_intervalo": 3600,     # 60 min
        "min_descuento": 3,
        "max_envios_dia": 35
    }
}

CFG = MODOS_CONFIG[MODO]

# ==================================================
# ================= CONFIG =========================
# ==================================================

PALABRAS_CLAVE = [
    "hogar", "electronica", "deporte",
    "cocina", "bricolaje", "oficina"
]

HEADERS = [
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/121.0"},
    {"User-Agent": "Mozilla/5.0 (Macintosh) Safari/605.1.15"},
]

TAG_AFILIADO = "crt06f-21"
HORA_INICIO = 9
HORA_FIN = 22

HISTORIAL_FILE = "enviados_historial.json"
ENVIO_DIARIO_FILE = "envios_diarios.json"

# ---------------- TELEGRAM ----------------
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
        log("RIESGO: límite diario alcanzado → pausa")
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
# ============== TELEGRAM ==========================
# ==================================================

def generar_mensaje(p):
    textos = [
        f"{p['titulo']}\n\nPrecio actual: {p['precio']} €\nMás información:\n{p['url']}",
        f"{p['titulo']}\n\nCoste: {p['precio']} €\nEnlace:\n{p['url']}",
        f"{p['titulo']}\n\nDisponible en Amazon:\n{p['url']}"
    ]
    return random.choice(textos)

def enviar_telegram(p):
    try:
        img = requests.get(p["imagen"], timeout=20).content
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHAT_ID, "caption": generar_mensaje(p)},
            files={"photo": ("img.jpg", img)},
            timeout=30
        )
        if r.status_code == 200:
            registrar_envio()
            log(f"Enviado ASIN {p['asin']}")
    except Exception as e:
        log(f"Telegram error: {e}")

# ==================================================
# ============== BUSQUEDA ==========================
# ==================================================

def buscar_productos():
    palabra = random.choice(PALABRAS_CLAVE)
    urls = set()

    for page in range(1, 4):
        html = get_html(f"https://www.amazon.es/s?k={palabra}&page={page}")
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='/dp/']"):
            asin = extract_asin(a.get("href", ""))
            if asin:
                urls.add(f"https://www.amazon.es/dp/{asin}")

    return list(urls)

def obtener_producto(url, historial):
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
        "imagen": imagen["src"] if imagen else None,
        "url": crear_url(asin)
    }

# ==================================================
# ================= MAIN ===========================
# ==================================================

def main():
    historial = set(cargar_json(HISTORIAL_FILE, []))
    log(f"Sistema iniciado | MODO={MODO}")

    while True:
        if not horario_permitido() or not evaluar_riesgo():
            time.sleep(600)
            continue

        urls = buscar_productos()

        for url in urls:
            p = obtener_producto(url, historial)
            if p:
                enviar_telegram(p)
                break

        espera = random.randint(CFG["min_intervalo"], CFG["max_intervalo"])
        log(f"Esperando {espera // 60} minutos")
        time.sleep(espera)

if __name__ == "__main__":
    main()
