#!/usr/bin/env python3
"""
Descubridor de páginas de ensayos PAES
-----------------------------------------
Busca en DuckDuckGo (la versión HTML sin JavaScript, no requiere cuenta ni
API key -- gratis) páginas de instituciones que ofrecen ensayos PAES,
incluyendo variantes regionales ("ensayo PAES Concepción", "...Temuco", etc.)
para no depender solo de una lista fija de universidades.

Los resultados nuevos se guardan en discovered_pages.json. Desde ahí,
scripts/scraper_universidades.py los toma junto con sus semillas curadas y
les aplica la misma lógica de "solo publica si la fecha es clara, si no
abre un Issue" -- este script NO publica nada en Discord por sí mismo, solo
amplía la lista de páginas a revisar.

Pensado para correr 1 vez por semana (es una búsqueda, no cambia todos los
días). Si DuckDuckGo bloquea o cambia su HTML, el script no rompe nada: no
encuentra resultados nuevos y sigue igual la próxima semana.
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DISCOVERED_FILE = os.environ.get("PAGINAS_DESCUBIERTAS_FILE", "discovered_pages.json")
MAX_PAGINAS_GUARDADAS = 120

HEADERS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Cubre a nivel nacional y las regiones/ciudades donde suele haber ensayos
# PAES organizados localmente (liceos, sedes regionales de universidades,
# municipalidades, etc.), no solo las universidades grandes de Santiago.
CONSULTAS = [
    "ensayo PAES gratuito 2026 inscripciones",
    "ensayo PAES nacional 2026",
    "ensayo PAES gratis Santiago 2026",
    "ensayo PAES gratis Concepción 2026",
    "ensayo PAES gratis Valparaíso 2026",
    "ensayo PAES gratis Viña del Mar 2026",
    "ensayo PAES gratis Temuco 2026",
    "ensayo PAES gratis La Serena 2026",
    "ensayo PAES gratis Coquimbo 2026",
    "ensayo PAES gratis Antofagasta 2026",
    "ensayo PAES gratis Valdivia 2026",
    "ensayo PAES gratis Puerto Montt 2026",
    "ensayo PAES gratis Rancagua 2026",
    "ensayo PAES gratis Talca 2026",
    "ensayo PAES gratis Chillán 2026",
    "ensayo PAES gratis Osorno 2026",
    "ensayo PAES gratis Punta Arenas 2026",
    "ensayo PAES gratis Copiapó 2026",
    "ensayo PAES gratis Iquique 2026",
    "ensayo PAES gratis Arica 2026",
    "ensayo PAES gratis Los Ángeles 2026",
    "ensayo PAES gratis Curicó 2026",
    "ensayo PAES liceo municipal 2026",
    "preuniversitario ensayo PAES presencial 2026",
    # Consultas dirigidas a redes sociales, que es donde muchos liceos y
    # sedes regionales avisan primero (ver nota sobre DOMINIOS_EXCLUIDOS).
    "ensayo PAES 2026 site:instagram.com",
    "ensayo PAES 2026 site:facebook.com",
]

# Dominios que rara vez o nunca son la fuente de una fecha de ensayo (video,
# enciclopedia, red profesional), se descartan directamente. A propósito NO
# se excluyen Instagram/Facebook/X/TikTok: ahí es donde muchos liceos y
# sedes regionales avisan primero. El motivo técnico por el que casi nunca
# se van a auto-publicar solas desde esas redes es que su contenido se carga
# con JavaScript y normalmente pide login, así que un GET simple (sin
# navegador, sin login, sin pagar nada) suele traer una página casi vacía.
# Cuando eso pasa, el scraper no encuentra fecha y usa su mecanismo normal
# de respaldo: abre un Issue con el link para que revises tú el post --
# sigue siendo mucho mejor que no vigilar esa cuenta para nada.
DOMINIOS_EXCLUIDOS = (
    "wikipedia.org", "linkedin.com", "pinterest.com", "youtube.com", "youtu.be",
    "duckduckgo.com",
)

RE_RESULT_LINK = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)
RE_TAG = re.compile(r"<[^>]+>")


def limpiar_titulo(txt: str) -> str:
    txt = RE_TAG.sub("", txt)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def resolver_url_ddg(href: str) -> str:
    """DuckDuckGo a veces envuelve el link real en /l/?uddg=<url_codeada>."""
    if href.startswith("//duckduckgo.com/l/") or "uddg=" in href:
        if href.startswith("//"):
            href = "https:" + href
        qs = urllib.parse.urlparse(href).query
        params = urllib.parse.parse_qs(qs)
        if "uddg" in params:
            return urllib.parse.unquote(params["uddg"][0])
    return href


def dominio_valido(url: str) -> bool:
    try:
        dominio = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return False
    if not dominio:
        return False
    if any(excl in dominio for excl in DOMINIOS_EXCLUIDOS):
        return False
    return True


def buscar(consulta: str) -> list:
    """Devuelve [(url, titulo), ...] de la primera página de resultados."""
    url_busqueda = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": consulta})
    req = urllib.request.Request(url_busqueda, headers=HEADERS_HTTP)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            cuerpo = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"AVISO: falló la búsqueda '{consulta}': {e}", file=sys.stderr)
        return []

    resultados = []
    for m in RE_RESULT_LINK.finditer(cuerpo):
        href_crudo, titulo_html = m.group(1), m.group(2)
        url_final = resolver_url_ddg(href_crudo)
        if not url_final.startswith("http") or not dominio_valido(url_final):
            continue
        resultados.append((url_final, limpiar_titulo(titulo_html)))
    return resultados


# Palabras en el título del resultado que casi siempre indican que NO es un
# ensayo con fecha y lugar, sino un recurso descargable, comercial o de
# contenido (PDF, banco de preguntas, tienda, comunidad, blog de resúmenes)
# -- se descartan para no generar avisos de "fecha por confirmar" sobre algo
# que nunca va a tener fecha.
PALABRAS_NO_EVENTO = (
    "descargable", "descargar", "pdf", "banco de preguntas",
    "simulador online", "ensayo online gratis", "practica online",
    "práctica online", "ranking", "pack digital", "guía completa",
    "guia completa", "material imprescindible", "resumen", "resúmenes",
    "resumenes", "libro", "libros", "comunidad", "foro",
)

# Dominios que son tiendas, comunidades o blogs de contenido -- nunca van a
# publicar la fecha de un evento con lugar/hora, así que ni vale la pena
# revisarlos.
DOMINIOS_NO_EVENTO = ("skool.com", "psulibros.com", "librospaes.com")


def parece_evento_real(titulo: str, url: str = "") -> bool:
    titulo_lower = titulo.lower()
    if any(p in titulo_lower for p in PALABRAS_NO_EVENTO):
        return False
    dominio = urllib.parse.urlparse(url).netloc.lower() if url else ""
    return not any(d in dominio for d in DOMINIOS_NO_EVENTO)


def cargar_descubiertas() -> list:
    if not os.path.exists(DISCOVERED_FILE):
        return []
    try:
        with open(DISCOVERED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def guardar_descubiertas(paginas: list) -> None:
    with open(DISCOVERED_FILE, "w", encoding="utf-8") as f:
        json.dump(paginas, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> int:
    descubiertas = cargar_descubiertas()
    urls_conocidas = {p["url"] for p in descubiertas}
    nuevas = 0

    for consulta in CONSULTAS:
        for url, titulo in buscar(consulta):
            if url in urls_conocidas:
                continue
            # Filtro mínimo de relevancia: que la palabra "paes" o "ensayo"
            # aparezca en la URL o el título del resultado.
            texto_check = (url + " " + titulo).lower()
            if "paes" not in texto_check and "ensayo" not in texto_check:
                continue
            if not parece_evento_real(titulo, url):
                print(f"Descartada (parece recurso comercial/contenido, no evento con fecha): {titulo}")
                continue
            descubiertas.append({"url": url, "titulo": titulo or url})
            urls_conocidas.add(url)
            nuevas += 1
            print(f"Descubierta: {titulo} -> {url}")
        time.sleep(1)  # evitar golpear DuckDuckGo demasiado rápido

    # nos quedamos con las más recientes si la lista crece mucho
    if len(descubiertas) > MAX_PAGINAS_GUARDADAS:
        descubiertas = descubiertas[-MAX_PAGINAS_GUARDADAS:]

    guardar_descubiertas(descubiertas)
    print(f"Listo. Páginas nuevas descubiertas: {nuevas}. Total guardadas: {len(descubiertas)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
