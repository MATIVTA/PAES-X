#!/usr/bin/env python3
"""
Scraper de páginas de ensayos/eventos universitarios
-------------------------------------------------------
Revisa cada página de PAGINAS_A_VIGILAR y trata de detectar la fecha del
próximo ensayo/evento directamente del texto. Si encuentra UNA fecha futura
clara asociada a la palabra "ensayo" (u otras palabras clave), la agrega
sola a events.json -- sin que tengas que hacer nada.

Solo cuando el resultado es ambiguo (no encontró ninguna fecha, o encontró
varias fechas distintas y no puede saber cuál es la buena) deja de adivinar
y abre un Issue en GitHub para que la revises tú: es preferible dejar un
evento pendiente de revisión a publicar una fecha incorrecta a todo el
servidor de Discord.

No usa librerías externas (solo stdlib). Usa el GITHUB_TOKEN automático de
Actions para abrir Issues -- no requiere ningún secreto nuevo ni pagar nada.
"""

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

EVENTS_FILE = os.environ.get("EVENTS_FILE", "events.json")
STATE_FILE = os.environ.get("WATCH_STATE_FILE", "watch_state.json")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")  # lo pone Actions solo

HEADERS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Semillas curadas: instituciones que ya sabemos que hacen ensayos PAES
# gratuitos, varias con sedes en más de una ciudad (así una sola página
# cubre varias regiones). "titulo" es el nombre limpio que se usa en el
# mensaje de Discord (sin fecha pegada, sin URLs).
PAGINAS_A_VIGILAR = [
    {"nombre": "Santo Tomás - Ensayo PAES", "titulo": "Ensayo PAES Santo Tomás",
     "url": "https://enlinea.santotomas.cl/etiquetas/ensayo-paes/", "tipo": "ensayo"},
    {"nombre": "UC - Ensayos PAES", "titulo": "Ensayo PAES UC",
     "url": "https://admision.uc.cl/informacion-para/futuro-estudiante-de-pregrado/ensayos-paes-uc/ensayo-paes-uc-inscripcion/", "tipo": "ensayo"},
    {"nombre": "U. Autónoma - Ensayo PAES", "titulo": "Ensayo PAES Universidad Autónoma",
     "url": "https://admision.uautonoma.cl/ensayo-paes-gratuito/", "tipo": "ensayo"},
    {"nombre": "USM - Ensayo PAES", "titulo": "Ensayo PAES USM",
     "url": "https://usm.cl/eventos/ensayo-paes/", "tipo": "ensayo"},
    {"nombre": "UAH - Ven y conócenos", "titulo": "UAH — Actividades para postulantes",
     "url": "https://admision.uahurtado.cl/ven-y-conocenos/", "tipo": "generico"},
    # Cubre Santiago, Concepción, Valdivia y Puerto Montt en una sola página.
    {"nombre": "U. San Sebastián - Ensayo PAES presencial", "titulo": "Ensayo PAES Universidad San Sebastián",
     "url": "https://preuniversitario.uss.cl/ensayo-presencial", "tipo": "ensayo"},
    {"nombre": "U. San Sebastián - Ensayo PAES online", "titulo": "Ensayo PAES online Universidad San Sebastián",
     "url": "https://preuniversitario.uss.cl/ensayo-online", "tipo": "ensayo"},
    # UNAB + Preuniversitario Pedro de Valdivia: cubre Santiago, Viña del Mar y Concepción.
    {"nombre": "UNAB + PDV - Ensayo Masivo PAES", "titulo": "Ensayo Masivo PAES UNAB + PDV",
     "url": "https://explora.unab.cl/ensayo-masivo-paes-unab-pdv-2026-inscribete-aqui-y-preparate-con-un-ensayo-presencial-y-gratuito/", "tipo": "ensayo"},
]

# Archivo donde se guardan las páginas que el descubridor (scripts/descubridor.py)
# va encontrando por búsqueda, además de las semillas de arriba. Si no existe
# todavía (o descubridor.py no se ha corrido), simplemente se ignora.
PAGINAS_DESCUBIERTAS_FILE = os.environ.get("PAGINAS_DESCUBIERTAS_FILE", "discovered_pages.json")


# Mismo filtro que usa descubridor.py: títulos/dominios que indican un
# recurso comercial o de contenido (PDF, tienda, comunidad, blog de
# resúmenes) en vez de un evento con fecha y lugar.
PALABRAS_NO_EVENTO = (
    "descargable", "descargar", "pdf", "banco de preguntas",
    "simulador online", "ensayo online gratis", "practica online",
    "práctica online", "ranking", "pack digital", "guía completa",
    "guia completa", "material imprescindible", "resumen", "resúmenes",
    "resumenes", "libro", "libros", "comunidad", "foro",
)
DOMINIOS_NO_EVENTO = ("skool.com", "psulibros.com", "librospaes.com")


def dominio_de(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return url
    return netloc[4:] if netloc.startswith("www.") else netloc


def cargar_paginas_a_revisar() -> list:
    """Semillas curadas + páginas nuevas encontradas por scripts/descubridor.py,
    sin duplicar URLs. Cuando el descubridor encontró varias páginas del
    MISMO dominio (típico: la landing, la subpágina de inscripción y una
    noticia, todas de la misma universidad), solo se usa la primera --
    evita mandar varios avisos "fecha por confirmar" por el mismo ensayo."""
    paginas = list(PAGINAS_A_VIGILAR)
    urls_ya_incluidas = {p["url"] for p in paginas}
    dominios_ya_incluidos = {dominio_de(p["url"]) for p in paginas}

    if os.path.exists(PAGINAS_DESCUBIERTAS_FILE):
        try:
            with open(PAGINAS_DESCUBIERTAS_FILE, "r", encoding="utf-8") as f:
                descubiertas = json.load(f)
        except (json.JSONDecodeError, OSError):
            descubiertas = []
        for d in descubiertas:
            titulo_d = (d.get("titulo") or "").lower()
            url_d = d.get("url") or ""
            if any(p in titulo_d for p in PALABRAS_NO_EVENTO):
                continue  # recurso comercial/de contenido, no un evento con fecha
            if any(dom in dominio_de(url_d) for dom in DOMINIOS_NO_EVENTO):
                continue
            if not url_d or url_d in urls_ya_incluidas:
                continue
            dominio = dominio_de(url_d)
            if dominio in dominios_ya_incluidos:
                continue  # ya hay otra página de este mismo dominio en la lista
            paginas.append({
                "nombre": d.get("titulo") or url_d,
                "titulo": d.get("titulo") or url_d,
                "url": url_d,
                "tipo": "ensayo",
            })
            urls_ya_incluidas.add(url_d)
            dominios_ya_incluidos.add(dominio)

    return paginas

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
RE_FECHA_TEXTO = re.compile(
    r"\b(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")(?:\s+de\s+(\d{4}))?\b",
    re.IGNORECASE,
)
RE_FECHA_NUM = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
RE_TAG = re.compile(r"<[^>]+>")

PALABRAS_CLAVE = ("ensayo", "simulacro")
VENTANA_CONTEXTO = 150


# ---------- Descarga y limpieza ----------

def descargar_texto(url: str) -> str:
    # La versión móvil de Facebook a veces entrega el texto del post sin
    # pedir login; la versión de escritorio casi nunca lo hace. Para
    # Instagram/X no existe un atajo equivalente sin login -- si no se puede
    # leer, simplemente no se encuentra fecha y el scraper cae a su
    # mecanismo normal de abrir un Issue con el link, en vez de fallar.
    if "facebook.com" in url and "m.facebook.com" not in url:
        url = url.replace("www.facebook.com", "m.facebook.com").replace("//facebook.com", "//m.facebook.com")

    req = urllib.request.Request(url, headers=HEADERS_HTTP)
    with urllib.request.urlopen(req, timeout=20) as resp:
        crudo = resp.read().decode("utf-8", errors="replace")
    crudo = re.sub(r"(?is)<(script|style).*?</\1>", "", crudo)
    texto = RE_TAG.sub(" ", crudo)
    texto = re.sub(r"[ \t]+", " ", texto).strip()
    return texto


# ---------- Extracción de fechas ----------

def inferir_anio(mes: int, dia: int, hoy: date):
    """Si la fecha (sin año explícito en el texto) todavía no ocurre este
    año, se asume el año actual. Si ya pasó este año, se descarta -- lo más
    probable es que sea una fecha vieja que quedó en la página (un ensayo
    que ya se hizo), NO un anuncio para el año siguiente. Adivinar "debe
    ser el próximo año" es lo que generaba fechas fantasma tipo 2027 a
    partir de anuncios de ensayos que ya pasaron. Devuelve None si se debe
    descartar."""
    try:
        f = date(hoy.year, mes, dia)
    except ValueError:
        return None
    return hoy.year if f >= hoy else None


def _cerca_de_palabra_clave(texto_lower: str, ini: int, fin: int) -> bool:
    ventana = texto_lower[max(0, ini - VENTANA_CONTEXTO): fin + VENTANA_CONTEXTO]
    return any(p in ventana for p in PALABRAS_CLAVE)


def extraer_fechas_candidatas(texto: str, hoy: date = None) -> list:
    """Devuelve fechas futuras únicas mencionadas cerca de una palabra clave
    tipo "ensayo". Si la página menciona varias fechas distintas de ensayo,
    todas quedan como candidatas (ambigüedad real, no se adivina cuál usar).
    Cada elemento es (fecha, contexto_texto, posicion_inicio, posicion_fin)."""
    hoy = hoy or date.today()
    texto_lower = texto.lower()
    candidatas = {}

    for m in RE_FECHA_TEXTO.finditer(texto):
        dia = int(m.group(1))
        mes = MESES[m.group(2).lower()]
        anio = int(m.group(3)) if m.group(3) else inferir_anio(mes, dia, hoy)
        if anio is None:
            continue  # fecha sin año explícito que ya pasó este año: se descarta, no se adivina
        try:
            f = date(anio, mes, dia)
        except ValueError:
            continue
        if f >= hoy and _cerca_de_palabra_clave(texto_lower, m.start(), m.end()):
            candidatas.setdefault(f, (texto[max(0, m.start() - 40):m.end() + 40].strip(), m.start(), m.end()))

    for m in RE_FECHA_NUM.finditer(texto):
        dia, mes, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            f = date(anio, mes, dia)
        except ValueError:
            continue
        if f >= hoy and _cerca_de_palabra_clave(texto_lower, m.start(), m.end()):
            candidatas.setdefault(f, (texto[max(0, m.start() - 40):m.end() + 40].strip(), m.start(), m.end()))

    return sorted((f, ctx, ini, fin) for f, (ctx, ini, fin) in candidatas.items())


# ---------- events.json ----------

def cargar_events(path: str) -> dict:
    if not os.path.exists(path):
        return {"eventos": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_events(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def cargar_estado() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        contenido = f.read().strip()
    return json.loads(contenido) if contenido else {}


def guardar_estado(estado: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)
        f.write("\n")


def slug(texto: str) -> str:
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    return texto.strip("-")[:60]


def detectar_modalidad(texto: str, ini: int, fin: int) -> str:
    """Busca menciones de 'presencial' / 'online'/'virtual' cerca de la fecha
    detectada. Si no hay nada claro, devuelve "" (y el mensaje simplemente no
    muestra la fila de modalidad, en vez de un feo 'Por confirmar')."""
    ventana = texto[max(0, ini - VENTANA_CONTEXTO): fin + VENTANA_CONTEXTO].lower()
    tiene_presencial = "presencial" in ventana
    tiene_online = "online" in ventana or "virtual" in ventana
    if tiene_presencial and tiene_online:
        return "Presencial y online"
    if tiene_presencial:
        return "Presencial"
    if tiene_online:
        return "Online"
    return ""


def evento_ya_existe(nombre_pagina: str, fecha: date, eventos: list) -> bool:
    clave = slug(nombre_pagina)
    for ev in eventos:
        if ev.get("fecha") == fecha.isoformat() and clave in (ev.get("id") or ""):
            return True
    return False


# ---------- GitHub Issues (respaldo cuando hay ambigüedad) ----------

def issue_ya_abierto(titulo: str) -> bool:
    if not (GITHUB_TOKEN and GITHUB_REPOSITORY):
        return False
    query = urllib.parse.quote(f'repo:{GITHUB_REPOSITORY} type:issue state:open in:title "{titulo}"')
    url = f"https://api.github.com/search/issues?q={query}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "paes-x-bot",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("total_count", 0) > 0
    except (urllib.error.URLError, ValueError):
        return False


def crear_issue(titulo: str, cuerpo: str) -> None:
    if not (GITHUB_TOKEN and GITHUB_REPOSITORY):
        print("AVISO: sin GITHUB_TOKEN/GITHUB_REPOSITORY, no se puede crear el Issue.", file=sys.stderr)
        return
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/issues"
    payload = json.dumps({"title": titulo, "body": cuerpo, "labels": ["evento-por-revisar"]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "paes-x-bot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        print(f"Issue creado: {titulo}")
    except urllib.error.HTTPError as e:
        print(f"ERROR al crear issue: {e.code} {e.read()}", file=sys.stderr)


# ---------- Main ----------

def main() -> int:
    data = cargar_events(EVENTS_FILE)
    eventos = data.setdefault("eventos", [])
    estado = cargar_estado()
    hoy = date.today()
    paginas = cargar_paginas_a_revisar()

    agregados = 0
    pendientes = 0

    for pagina in paginas:
        nombre = pagina["nombre"]
        titulo_limpio = pagina.get("titulo") or nombre
        url = pagina["url"]
        tipo = pagina.get("tipo", "generico")
        try:
            texto = descargar_texto(url)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"AVISO: no se pudo revisar '{nombre}' ({url}): {e}", file=sys.stderr)
            continue

        candidatas = extraer_fechas_candidatas(texto, hoy)
        id_pendiente = f"pendiente-{slug(titulo_limpio)}"

        if len(candidatas) == 1:
            fecha, contexto, ini, fin = candidatas[0]
            if evento_ya_existe(titulo_limpio, fecha, eventos):
                continue
            # si esta página ya estaba publicada como "fecha por confirmar",
            # la reemplazamos por la versión definitiva con fecha.
            eventos[:] = [e for e in eventos if e.get("id") != id_pendiente]
            nuevo_id = f"auto-{slug(titulo_limpio)}-{fecha.isoformat()}"
            modalidad = detectar_modalidad(texto, ini, fin)
            evento_nuevo = {
                "id": nuevo_id,
                "tipo": tipo,
                "titulo": titulo_limpio,
                "fecha": fecha.isoformat(),
                "hora": None,
                "link": url,
                "origen": "scraper_universidades",
            }
            if modalidad:
                evento_nuevo["modalidad"] = modalidad
            eventos.append(evento_nuevo)
            agregados += 1
            print(f"Agregado automáticamente: {nuevo_id}  (\"...{contexto}...\")")
            continue

        # 0 fechas encontradas o varias distintas: en vez de dejarlo esperando
        # revisión manual, se publica igual como "fecha por confirmar" (una
        # sola vez -- si ya existe la versión pendiente, no se duplica).
        if not any(e.get("id") == id_pendiente for e in eventos):
            evento_pendiente = {
                "id": id_pendiente,
                "tipo": tipo,
                "titulo": titulo_limpio,
                "fecha": None,
                "link": url,
                "origen": "scraper_universidades",
            }
            if len(candidatas) > 1:
                fechas_listadas = ", ".join(f.strftime("%d-%m-%Y") for f, *_ in candidatas)
                evento_pendiente["notas"] = f"Fechas mencionadas en la página: {fechas_listadas}"
            eventos.append(evento_pendiente)
            agregados += 1
            print(f"Agregado como pendiente (fecha por confirmar): {id_pendiente}")

        # Issue de respaldo (no es necesario revisarlo -- el aviso ya salió
        # en Discord -- pero queda como registro por si sirve).
        hash_actual = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        if estado.get(url) == hash_actual:
            continue

        titulo_issue = f"Revisar fecha en: {nombre}"
        if len(candidatas) == 0:
            motivo = "no se detectó ninguna fecha de ensayo en el texto de la página."
        else:
            listado = "\n".join(f"- {f.isoformat()} (\"...{c}...\")" for f, c, _, _ in candidatas)
            motivo = f"se detectaron {len(candidatas)} fechas distintas y no es seguro cuál usar:\n\n{listado}"

        if not issue_ya_abierto(titulo_issue):
            crear_issue(
                titulo_issue,
                f"La página **{nombre}** ({url}) cambió y {motivo}\n\n"
                "El bot ya publicó esto en Discord como \"fecha por confirmar\", "
                "así que revisar este Issue es opcional -- solo queda como registro.\n\n"
                "_Este issue se generó automáticamente. Ciérralo cuando quieras._"
            )
        pendientes += 1

        estado[url] = hash_actual

    if agregados:
        guardar_events(EVENTS_FILE, data)
    guardar_estado(estado)

    print(f"Listo. Eventos agregados automáticamente: {agregados}. Pendientes de revisión manual: {pendientes}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
