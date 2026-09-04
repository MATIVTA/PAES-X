#!/usr/bin/env python3
"""
Scraper de páginas de ensayos/eventos universitarios
-------------------------------------------------------
Revisa cada página de PAGINAS_A_VIGILAR (más las que encuentra el
descubridor) y publica el próximo ensayo/evento con su fecha en events.json,
TODO automático: no hace falta que nadie revise ni confirme nada a mano.

Cómo decide:
- Lee fechas en varios formatos ("5 de septiembre", "5 sep", "05/09/2026",
  rangos "5 al 7 de septiembre", con o sin año).
- Si la página es claramente de un ensayo (título o URL lo dicen), considera
  TODAS sus fechas; si no, solo las cercanas a la palabra "ensayo".
- Si encuentra varias fechas, elige la más confiable: la que el texto enmarca
  como fecha del evento ("se realizará", "se rinde") y, entre esas, la más
  próxima al presente. Sin ambigüedad pendiente: siempre se publica algo con
  fecha, o nada.
- Si la única fecha es en realidad un PLAZO de inscripción ("inscríbete hasta
  el X"), la publica como evento de inscripción/cierre de plazo (que es lo que
  la página anuncia), NO como la fecha del ensayo.
- Si una página no tiene ninguna fecha clara, simplemente no se publica: es
  preferible no avisar a avisar con la fecha equivocada (o pedir una revisión
  manual que nadie tiene tiempo de hacer).
- Si una página cambia su fecha, se reemplaza el evento viejo automáticamente.

No requiere librerías externas (solo stdlib). No abre Issues en GitHub.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

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
     "url": "https://ensayo.santotomas.cl/", "tipo": "ensayo"},
    {"nombre": "UC - Ensayos PAES", "titulo": "Ensayo PAES UC",
     "url": "https://admision.uc.cl/informacion-para/futuro-estudiante-de-pregrado/ensayos-paes-uc/", "tipo": "ensayo"},
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

# Archivo donde se guardan páginas extra a vigilar, además de las semillas de
# arriba (lo dejaron versiones anteriores del bot que buscaban páginas nuevas).
# Si no existe todavía, simplemente se ignora.
PAGINAS_DESCUBIERTAS_FILE = os.environ.get("PAGINAS_DESCUBIERTAS_FILE", "discovered_pages.json")


# Filtro de contenido: títulos que indican un recurso o contenido (PDF,
# banco de preguntas, ranking, blog de resúmenes, comunidad, tienda) en vez
# de un evento con fecha y lugar. Sin esto, la página de una noticia, un foro
# o una tienda terminaba convertida en "evento pendiente" y publicada en
# Discord como si fuera un ensayo con fecha por confirmar.
PALABRAS_NO_EVENTO = (
    "descargable", "descargar", "pdf", "banco de preguntas",
    "simulador online", "simulacros en línea", "simulacros en linea",
    "ensayo online gratis", "practica online", "práctica online",
    "ranking", "pack digital", "guía completa", "guia completa",
    "material imprescindible", "resumen", "resúmenes", "resumenes",
    "libro", "libros", "comunidad", "foro",
)
# Dominios de tiendas/comunidades/portales de resúmenes que jamás son un
# evento con fecha: sus páginas no deben convertirse en avisos de Discord.
DOMINIOS_NO_EVENTO = ("skool.com", "psulibros.com", "librospaes.com")


def dominio_de(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return url
    return netloc[4:] if netloc.startswith("www.") else netloc


def es_contenido_no_evento(titulo: str, url: str = "") -> bool:
    """True si el título/URL apuntan a un recurso o contenido (PDF, ranking,
    tienda, comunidad, blog) y NO a un evento con fecha y lugar."""
    titulo_low = (titulo or "").lower()
    if any(p in titulo_low for p in PALABRAS_NO_EVENTO):
        return True
    if url:
        dominio = dominio_de(url)
        if any(dom in dominio for dom in DOMINIOS_NO_EVENTO):
            return True
    return False


def cargar_paginas_a_revisar() -> list:
    """Semillas curadas + páginas nuevas encontradas por scripts/descubridor.py,
    sin duplicar URLs ni dominios. Cuando hay varias páginas del MISMO dominio
    (la landing, la subpágina de inscripción y una noticia, todas de la misma
    universidad), solo se usa la primera -- evita mandar varios avisos por el
    mismo ensayo, y evita colar noticias."""
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
            titulo_d = d.get("titulo") or ""
            url_d = d.get("url") or ""
            if es_contenido_no_evento(titulo_d, url_d):
                continue  # recurso/de contenido, no un evento con fecha
            if not url_d or url_d in urls_ya_incluidas:
                continue
            dominio = dominio_de(url_d)
            if dominio in dominios_ya_incluidos:
                continue  # ya hay otra página de este mismo dominio en la lista
            paginas.append({
                "nombre": titulo_d or url_d,
                "titulo": titulo_d or url_d,
                "url": url_d,
                "tipo": "ensayo",
            })
            urls_ya_incluidas.add(url_d)
            dominios_ya_incluidos.add(dominio)

    return paginas


# ---------- Extracción de fechas ----------

# Meses completos y abreviados (es lo que escriben las universidades: "5 de
# septiembre" o "5 sep"). Se busca el nombre más largo primero para que
# "septiembre" no se corte como "sep".
MESES = {
    "enero": 1, "ene": 1,
    "febrero": 2, "feb": 2,
    "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6,
    "julio": 7, "jul": 7,
    "agosto": 8, "ago": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "set": 9,
    "octubre": 10, "oct": 10,
    "noviembre": 11, "nov": 11,
    "diciembre": 12, "dic": 12,
}
_RE_MESES = "|".join(sorted(MESES, key=len, reverse=True))
RE_FECHA_RANGO = re.compile(
    r"\b(\d{1,2})\s+(?:al|a|y\s+el)\s+\d{1,2}\s+(?:de\s+)?(" + _RE_MESES + r")(?:\s+(?:de\s+)?(\d{4}))?\b",
    re.IGNORECASE,
)
RE_FECHA_TEXTO = re.compile(
    r"\b(\d{1,2})(?:º|°)?\s*(?:de\s+)?(" + _RE_MESES + r")(?:\s+(?:de\s+)?(\d{4}))?\b",
    re.IGNORECASE,
)
RE_FECHA_NUM = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
RE_TAG = re.compile(r"<[^>]+>")

PALABRAS_CLAVE = ("ensayo", "simulacro")
VENTANA_CONTEXTO = 150


def _tiene_palabra_clave(texto: str) -> bool:
    t = (texto or "").lower()
    return any(p in t for p in PALABRAS_CLAVE)


def inferir_anio(mes: int, dia: int, hoy: date):
    """Si la fecha (sin año explícito en el texto) todavía no ocurre este
    año, se usa el año actual. Si ya pasó este año, se descarta -- lo más
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


RE_PLAZO_INSCRIPCION = re.compile(
    r"\b(hasta el|antes del?|último\s+d[ií]a|(?:se\s+)?cierra|vence|"
    r"fecha\s+l[ií]mite|tope|fin\s+de\s+inscripci[oó]n)\b",
    re.IGNORECASE,
)
RE_FECHA_DEL_EVENTO = re.compile(
    r"\b(se\s+realiza|realizar[áa]|se\s+rendir|se\s+rinde|rendir[áa]s|"
    r"el ensayo (?:es|ser[áa]|se har[áa])|tendr[áa] lugar|se har[áa]|"
    r"fecha del ensayo|fecha\s+de\s+rendici[oó]n)\b",
    re.IGNORECASE,
)
# Para decidir el tipo cuando la única fecha es un plazo: si dice "cierra",
# "vence", "límite" o "tope" es un cierre de plazo; si no, una inscripción.
RE_TIPO_CIERRE = re.compile(r"\b(cierra|vence|fecha\s+l[ií]mite|tope)\b", re.IGNORECASE)


def contexto_sugiere_plazo(texto: str, ini: int, fin: int) -> bool:
    """True si la fecha viene enmarcada como un PLAZO/FECHA LÍMITE (ej.
    "inscríbete hasta el 5 de septiembre") y no como la fecha del ensayo."""
    ventana = texto[max(0, ini - 30): fin + 20]
    if not RE_PLAZO_INSCRIPCION.search(ventana):
        return False
    if RE_FECHA_DEL_EVENTO.search(ventana):
        return False  # la página también dice/insinúa que ESE día es el evento
    return True


def extraer_fechas_candidatas(texto: str, titulo: str = "", url: str = "",
                              hoy: date = None) -> list:
    """Todas las fechas FUTURAS mencionadas en la página. Si la página es de
    un ensayo (el título o la URL contienen "ensayo"/"simulacro"), TODAS sus
    fechas cuentan (no se filtran por proximidad a la palabra clave), porque
    la página puede mencionar "ensayo" muy lejos de la fecha. Devuelve una
    lista de (fecha, contexto, ini, fin) ordenada."""
    hoy = hoy or date.today()
    texto_lower = texto.lower()
    es_pagina_ensayo = _tiene_palabra_clave(titulo) or _tiene_palabra_clave(url)
    candidatas = {}

    def _agregar(f: date, ini: int, fin: int) -> None:
        if f < hoy:
            return
        candidatas.setdefault(
            f, (texto[max(0, ini - 40): fin + 40].strip(), ini, fin)
        )

    # Rangos primero: "5 al 7 de septiembre" -> se usa el día de inicio (5).
    for m in RE_FECHA_RANGO.finditer(texto):
        dia = int(m.group(1))
        mes = MESES[m.group(2).lower()]
        anio = int(m.group(3)) if m.group(3) else inferir_anio(mes, dia, hoy)
        if anio is None:
            continue
        try:
            _agregar(date(anio, mes, dia), m.start(), m.end())
        except ValueError:
            continue

    for m in RE_FECHA_TEXTO.finditer(texto):
        dia = int(m.group(1))
        mes = MESES[m.group(2).lower()]
        anio = int(m.group(3)) if m.group(3) else inferir_anio(mes, dia, hoy)
        if anio is None:
            continue
        try:
            f = date(anio, mes, dia)
        except ValueError:
            continue
        if es_pagina_ensayo or _cerca_de_palabra_clave(texto_lower, m.start(), m.end()):
            _agregar(f, m.start(), m.end())

    for m in RE_FECHA_NUM.finditer(texto):
        dia, mes, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            f = date(anio, mes, dia)
        except ValueError:
            continue
        if es_pagina_ensayo or _cerca_de_palabra_clave(texto_lower, m.start(), m.end()):
            _agregar(f, m.start(), m.end())

    return sorted((f, ctx, ini, fin) for f, (ctx, ini, fin) in candidatas.items())


def _elegir_candidata(candidatas: list, texto: str):
    """De varias fechas candidatas elige la MÁS CONFIABLE, sin intervención
    humana: 1) prefiere las que el texto enmarca como fecha del evento ("se
    realizará", "se rinde"); 2) entre ellas, la más próxima al presente. Si
    ninguna está enmarcada como fecha de evento, usa la más próxima futura."""
    como_evento = [
        c for c in candidatas
        if not contexto_sugiere_plazo(texto, c[2], c[3])
    ]
    fuente = como_evento or candidatas
    return min(fuente, key=lambda c: c[0])


# ---------- Descarga y limpieza ----------

def descargar_texto(url: str) -> str:
    for url2 in _url_candidata(url):
        try:
            return _descargar_una(url2)
        except (urllib.error.URLError, TimeoutError):
            continue
    raise urllib.error.URLError("todas las variantes fallaron")


def _url_candidata(url: str) -> list:
    # La versión móvil de Facebook a veces entrega el texto del post sin pedir
    # login; la de escritorio casi nunca. Para Instagram/X no hay atajo sin
    # login: si no se puede leer, no se encuentra fecha y el scraper no
    # publica nada (mejor que avisar con mala fecha).
    if "facebook.com" in url and "m.facebook.com" not in url:
        return [url.replace("www.facebook.com", "m.facebook.com")
                    .replace("//facebook.com", "//m.facebook.com"),
                url]
    return [url]


def _descargar_una(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS_HTTP)
    with urllib.request.urlopen(req, timeout=20) as resp:
        crudo = resp.read().decode("utf-8", errors="replace")
    crudo = re.sub(r"(?is)<(script|style).*?</\1>", "", crudo)
    texto = RE_TAG.sub(" ", crudo)
    texto = re.sub(r"[ \t]+", " ", texto).strip()
    return texto


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


def evento_ya_existe(titulo_pagina: str, fecha: date, eventos: list) -> bool:
    clave = slug(titulo_pagina)
    for ev in eventos:
        if ev.get("fecha") == fecha.isoformat() and clave in (ev.get("id") or ""):
            return True
    return False


# ---------- Main ----------

def main() -> int:
    data = cargar_events(EVENTS_FILE)
    eventos = data.setdefault("eventos", [])
    hoy = hoy_santiago()
    paginas = cargar_paginas_a_revisar()

    # Limpieza global (solo toca lo nuestro, origen scraper_universidades;
    # nunca eventos cargados a mano):
    #  1) Eventos de contenido que no es un evento (noticia, ranking, tienda,
    #     comunidad): se borran para que bot.py no los vuelva a publicar.
    #  2) Eventos SIN fecha ("fecha por confirmar") de versiones viejas del
    #     bot: con el modo automático, si una página no muestra una fecha
    #     clara no se publica nada, así que un evento sin fecha no tiene razón
    #     de existir. Si la página sí muestra fecha, se vuelve a crear sola
    #     unas líneas más abajo, ya con su fecha correcta.
    antes = len(eventos)
    eventos[:] = [
        e for e in eventos
        if e.get("origen") != "scraper_universidades"
        or (
            e.get("fecha")
            and not es_contenido_no_evento(e.get("titulo") or "", e.get("link") or "")
        )
    ]
    if len(eventos) != antes:
        print(f"Limpieza: se eliminaron {antes - len(eventos)} eventos viejos (sin fecha clara o de contenido no-PAES).")
        guardar_events(EVENTS_FILE, data)

    agregados = 0

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

        candidatas = extraer_fechas_candidatas(texto, titulo_limpio, url, hoy)
        if not candidatas:
            # Sin fecha clara: no se publica nada y NO se pide que nadie lo
            # revise. Es preferible no avisar a avisar con una fecha mala.
            print(f"Sin fecha clara en '{nombre}' — no se publica nada.")
            continue

        fecha, contexto, ini, fin = _elegir_candidata(candidatas, texto)

        if evento_ya_existe(titulo_limpio, fecha, eventos):
            print(f"Ya existe '{slug(titulo_limpio)}' para {fecha.isoformat()}.")
            continue

        # Si esta página ya tenía un evento con fecha (auto-...) de antes y
        # aparece una fecha nueva/distinta, se reemplaza: la anterior pudo
        # estar mal leída o ser un ensayo que se movió.
        prefijo_auto = f"auto-{slug(titulo_limpio)}-"
        eventos[:] = [
            e for e in eventos
            if not (e.get("id") or "").startswith(prefijo_auto)
        ]

        # La única fecha puede ser un PLAZO de inscripción ("inscríbete
        # hasta el X") en vez de la fecha del evento. Se publica igual, pero
        # con el tipo correcto (inscripción o cierre de plazo), nunca como
        # la fecha del ensayo.
        es_plazo = (
            tipo in ("ensayo", "generico")
            and contexto_sugiere_plazo(texto, ini, fin)
        )
        tipo_resultante = tipo
        if es_plazo:
            tipo_resultante = "cierre_plazo" if RE_TIPO_CIERRE.search(contexto) else "inscripcion"

        nuevo_id = f"auto-{slug(titulo_limpio)}-{fecha.isoformat()}"
        modalidad = detectar_modalidad(texto, ini, fin)
        evento_nuevo = {
            "id": nuevo_id,
            "tipo": tipo_resultante,
            "titulo": titulo_limpio,
            "fecha": fecha.isoformat(),
            "hora": None,
            "link": url,
            "origen": "scraper_universidades",
        }
        if modalidad:
            evento_nuevo["modalidad"] = modalidad
        if es_plazo:
            evento_nuevo["notas"] = (
                "La página lo anuncia como plazo de inscripción, no como la "
                "fecha del ensayo (la fecha del ensayo no aparece en la página)."
            )
        eventos.append(evento_nuevo)
        agregados += 1
        print(f"Agregado automáticamente: {nuevo_id}  (\"...{contexto}...\")")

    if agregados:
        guardar_events(EVENTS_FILE, data)

    print(f"Listo. Eventos agregados automáticamente: {agregados}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())