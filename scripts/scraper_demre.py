#!/usr/bin/env python3
"""
Scraper del calendario oficial de DEMRE
----------------------------------------
Lee https://demre.cl/calendario/calendario-proceso-<anio> (fuente oficial,
gratuita, sin API key) y agrega a events.json cualquier fecha del calendario
que todavía no esté registrada, para que los hitos oficiales del proceso
de admisión (inscripciones, PAES, resultados, postulación, matrícula, etc.)
dejen de depender de que alguien los tipee a mano.

No usa librerías externas (solo stdlib), para que no haya que instalar
nada en el workflow de GitHub Actions.

Estrategia, a propósito conservadora:
- Solo AGREGA eventos que no existan todavía (nunca modifica ni borra un
  evento que ya está en events.json, por si lo editaste a mano).
- El calendario de DEMRE separa mes, día, hora y descripción en elementos
  HTML distintos; este script los agrupa de vuelta en eventos con fecha,
  hora y título.
- Un evento del calendario se considera "ya existente" si hay un evento en
  events.json con la MISMA FECHA y al menos una palabra clave del título en
  común. Así no duplicamos, por ejemplo, "Rendición PAES Regular – Día 1"
  del calendario con "Rendición PAES Regular 2026 – Competencia Matemática 2"
  que ya tienes cargado a mano con más detalle.
- Los eventos nuevos se agregan con tipo inferido por palabras clave y SIN
  "avisos_dias" explícito, para que bot.py use los defaults según el tipo.
- Si el scraping falla (la página cambió de formato, no hay internet, etc.)
  el script no rompe nada: no toca events.json y termina con código 0,
  dejando un aviso en el log de la Action.
"""

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Santiago")
except Exception:
    TZ = None


def hoy_santiago() -> date:
    """Misma "hoy" que usa bot.py (hora de Chile), para que las comparaciones
    de fecha sean consistentes con el país al que apuntan los eventos."""
    try:
        if TZ:
            return datetime.now(TZ).date()
    except Exception:
        pass
    return date.today()

EVENTS_FILE = os.environ.get("EVENTS_FILE", "events.json")

CALENDAR_URLS = [
    "https://demre.cl/calendario/calendario-proceso-2027",
    "https://demre.cl/calendario/calendario-proceso-2026",
]

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Tags de bloque tras los que insertamos un salto de línea al limpiar el HTML,
# para no pegar el texto de dos elementos distintos.
BLOCK_TAGS = {"li", "br", "p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

RE_TAG = re.compile(r"<[^>]+>")
RE_YEAR_HEADER = re.compile(r"a[ñn]o\s+(\d{4})", re.IGNORECASE)

# El calendario actual de DEMRE separa mes, día, hora y descripción en
# ELEMENTOS DISTINTOS, así que al pasar por strip_html_to_text quedan en
# líneas separadas (ej: "Ene" / "23" / "Publicacion Temarios..."). Los tres
# patrones de abajo reconocen cada tipo de línea dentro de esa secuencia.
RE_MES_SOLO = re.compile(r"^(Ene|Feb|Mar|Abr|May|Jun|Jul|Ago|Sep|Oct|Nov|Dic)\.?$", re.IGNORECASE)
RE_DIA_SOLO = re.compile(r"^\d{1,2}$")
RE_HORA_SOLO = re.compile(
    r"^\d{1,2}:\d{2}(?:\s*(?:a|-|hasta)\s*\d{1,2}:\d{2})?\s*(?:hrs?\.?)?$",
    re.IGNORECASE,
)
# Conectores con los que una línea de descripción puede terminar cortada
# (el resto de la frase sigue en la línea siguiente).
RE_INCOMPLETA = re.compile(
    r"\b(de|del|el|la|los|las|en|al|a|y|hasta|para|por|sobre|con|según|durante)$",
    re.IGNORECASE,
)
RESIDUO_COMENTARIO = {">", "->", "-->", "--", "-", "*"}

TIPO_KEYWORDS = [
    ("resultados", ["resultado", "puntaje"]),
    ("cierre_plazo", ["cierre", "finaliza", "fin ", "vence"]),
    ("inscripcion", ["inscripci"]),
    ("ensayo", ["rendici", "aplicaci"]),
]


def strip_html_to_text(raw_html: str) -> str:
    """Convierte HTML crudo a texto plano conservando saltos de línea entre
    elementos de bloque, sin depender de ninguna librería externa."""
    raw_html = re.sub(r"(?is)<(script|style).*?</\1>", "", raw_html)

    def repl_tag(m: re.Match) -> str:
        tag = m.group(0).strip("<>/ ").split()[0].lower() if m.group(0).strip("<>/ ") else ""
        return "\n" if tag in BLOCK_TAGS else " "

    texto = RE_TAG.sub(repl_tag, raw_html)
    texto = html.unescape(texto)
    # Colapsa espacios (no saltos de línea) repetidos
    texto = re.sub(r"[ \t]+", " ", texto)
    return texto


def inferir_tipo(descripcion: str) -> str:
    desc = descripcion.lower()
    for tipo, palabras in TIPO_KEYWORDS:
        if any(p in desc for p in palabras):
            return tipo
    return "generico"


def slug(texto: str) -> str:
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto)
    return texto.strip("-")[:60]


def _armar_titulo(descripcion_lines: list) -> str:
    """Une las líneas de descripción de un evento. Se toma la primera línea
    como título; si queda cortada con un conector ("Tarjeta de"), se agregan
    las siguientes hasta completar la frase."""
    if not descripcion_lines:
        return ""
    titulo = descripcion_lines[0]
    i = 1
    while i < len(descripcion_lines) and RE_INCOMPLETA.search(titulo):
        titulo = f"{titulo} {descripcion_lines[i]}"
        i += 1
    return titulo.strip(" *")


def parse_calendar_text(texto: str, anio_por_defecto: int = None) -> list:
    """Extrae eventos (fecha, hora, descripción) de la versión en texto plano
    del calendario. El formato actual de DEMRE secuencia, en líneas separadas:
    MES / DÍA / [HORA] / DESCRIPCIÓN(1..n). Este parser consume esa secuencia
    y descarta el ruido (encabezados de navegación, residuos de comentarios).
    Devuelve una lista de dicts con fecha ISO."""
    eventos = []
    anio_actual = anio_por_defecto
    mes_actual = None
    dia_actual = None
    hora_actual = None
    desc_actual = []

    def _flush():
        nonlocal mes_actual, dia_actual, hora_actual, desc_actual
        if (anio_actual and mes_actual is not None and dia_actual is not None):
            try:
                fecha = date(anio_actual, mes_actual, dia_actual)
            except ValueError:
                pass
            else:
                descripcion = _armar_titulo(desc_actual)
                if len(descripcion) >= 6:
                    eventos.append({
                        "fecha": fecha.isoformat(),
                        "hora": hora_actual,
                        "descripcion": descripcion,
                    })
        mes_actual = None
        dia_actual = None
        hora_actual = None
        desc_actual = []

    for linea in texto.splitlines():
        linea = linea.strip()
        linea = re.sub(r"^[-•*]\s*", "", linea)  # quita viñetas markdown/HTML
        if not linea or linea in RESIDUO_COMENTARIO:
            continue

        m_anio = RE_YEAR_HEADER.search(linea)
        if m_anio:
            _flush()  # "Año 2026"/"Año 2027": cierra lo anterior y cambia el año
            anio_actual = int(m_anio.group(1))
            continue

        # Una línea que es SOLO el mes abre (o reabre) un evento.
        if RE_MES_SOLO.fullmatch(linea):
            _flush()
            mes_actual = MESES.get(linea.lower().rstrip(".")[:3])
            continue

        if mes_actual is not None and RE_DIA_SOLO.fullmatch(linea):
            dia_actual = int(linea)
            hora_actual = None
            desc_actual = []
            continue

        if dia_actual is not None and RE_HORA_SOLO.fullmatch(linea):
            hora_actual = linea
            continue

        # Con mes y día definidos, todo lo demás razonable es descripción.
        if dia_actual is not None:
            desc_actual.append(linea)
            continue

        # Línea fuera de un evento: se ignora (navegación, otros bloques).

    _flush()
    return eventos


def descargar(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def cargar_events(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_events(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def palabras_clave(titulo: str) -> set:
    stop = {"de", "la", "el", "en", "y", "a", "del", "los", "las", "para", "un", "una"}
    return {w for w in re.findall(r"[a-záéíóúñ0-9]+", titulo.lower()) if w not in stop and len(w) > 3}


def ya_existe(nuevo: dict, eventos_actuales: list) -> bool:
    for ev in eventos_actuales:
        if ev.get("fecha") != nuevo["fecha"]:
            continue
        comunes = palabras_clave(ev.get("titulo", "")) & palabras_clave(nuevo["descripcion"])
        if comunes:
            return True
    return False


def main() -> int:
    encontrados = []
    for url in CALENDAR_URLS:
        try:
            html_crudo = descargar(url)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"AVISO: no se pudo descargar {url}: {e}", file=sys.stderr)
            continue
        texto = strip_html_to_text(html_crudo)
        encontrados.extend(parse_calendar_text(texto))

    if not encontrados:
        print("AVISO: el scraper no encontró ningún evento (¿cambió el formato de la página de DEMRE?). No se modifica events.json.")
        return 0

    try:
        data = cargar_events(EVENTS_FILE)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ERROR: no se pudo leer {EVENTS_FILE}: {e}", file=sys.stderr)
        return 1

    eventos_actuales = data.setdefault("eventos", [])
    hoy = hoy_santiago()
    agregados = 0

    for enc in encontrados:
        fecha_evento = date.fromisoformat(enc["fecha"])
        if fecha_evento < hoy:
            continue  # no agregamos hitos que ya pasaron
        if ya_existe(enc, eventos_actuales):
            continue

        tipo = inferir_tipo(enc["descripcion"])
        nuevo_id = f"demre-auto-{slug(enc['descripcion'])}-{enc['fecha']}"
        if any(ev.get("id") == nuevo_id for ev in eventos_actuales):
            continue

        eventos_actuales.append({
            "id": nuevo_id,
            "tipo": tipo,
            "titulo": enc["descripcion"],
            "fecha": enc["fecha"],
            "hora": enc["hora"],
            "link": "https://demre.cl/calendario",
            "modalidad": "Online",
            "origen": "scraper_demre",
        })
        agregados += 1
        print(f"Agregado desde calendario DEMRE: {nuevo_id}")

    if agregados:
        guardar_events(EVENTS_FILE, data)

    print(f"Listo. Eventos nuevos agregados: {agregados}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
