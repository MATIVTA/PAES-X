#!/usr/bin/env python3
"""
PAES X - Bot de avisos automáticos para el canal #eventos-paes
----------------------------------------------------------------
Este script:
1. Lee events.json (la lista de eventos PAES).
2. Detecta eventos nuevos (nunca anunciados) y publica un anuncio
   inmediato el mismo día en que se agregan, sin esperar a la cuenta
   regresiva (útil cuando hay cupos limitados).
3. Calcula si HOY corresponde publicar un recordatorio de cuenta
   regresiva para algún evento, según su tipo y avisos_dias.
4. Genera un embed distinto según el tipo de evento y la cercanía.
5. Lo publica en Discord vía Webhook.
6. Registra en sent_log.json lo que ya se envió, para no repetirlo.

No requiere librerías externas (solo la librería estándar de Python),
para que el workflow de GitHub Actions no tenga que instalar nada.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Santiago")
except Exception:
    TZ = None

EVENTS_FILE = "events.json"
LOG_FILE = "sent_log.json"

# IDs de canales para que las menciones sean clickeables de verdad en Discord.
# Formato real de mención de canal: <#ID>. Cambia estos IDs si mueves los canales.
CANAL_GENERAL_ID = 1520940545868828735
CANAL_METAS_ID = 1520940547991273492

# Reglas de anticipación por defecto si un evento no define "avisos_dias"
DEFAULTS_POR_TIPO = {
    "ensayo": [14, 7, 1, 0],
    "inscripcion": [7, 3, 1, 0],
    "resultados": [0],
    "cierre_plazo": [3, 1, 0],
    "generico": [7, 1, 0],
}

# El anuncio inmediato de "evento nuevo" solo se publica si al evento le
# quedan como máximo estos días. Si agregas un evento con más anticipación
# que esto (ej. algo a 5 meses), el bot NO lo anuncia todavía: espera y lo
# publicará automáticamente el día en que el evento entre en esta ventana,
# sin que tengas que hacer nada. Ajusta este número si quieres otra ventana.
UMBRAL_ANUNCIO_INMEDIATO_DIAS = 25

# ---------- Filtro de contenido no-PAES (defensa de último momento) ----------
# El scraper de universidades ya filtra antes de escribir events.json, pero el
# bot corre ANTES que él cada día (12:00 vs 16:45 UTC) y no escribe events.json.
# Si por cualquier razón queda una página suelta convertida en evento (una
# noticia, un ranking, una tienda, una comunidad), este filtro la descarta AQUÍ
# para que nunca llegue a Discord.
PALABRAS_NO_EVENTO = (
    "descargable", "descargar", "pdf", "banco de preguntas",
    "simulador online", "simulacros en línea", "simulacros en linea",
    "ensayo online gratis", "practica online", "práctica online",
    "ranking", "pack digital", "guía completa", "guia completa",
    "material imprescindible", "resumen", "resúmenes", "resumenes",
    "libro", "libros", "comunidad", "foro",
)
# Dominios de tiendas/comunidades/portales de resúmenes que jamás son un
# evento con fecha, pase lo que pase con el título que les hayan puesto.
DOMINIOS_NO_EVENTO = ("skool.com", "psulibros.com", "librospaes.com")


def dominio_de(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return url
    return netloc[4:] if netloc.startswith("www.") else netloc


def es_contenido_no_evento(titulo: str, url: str = "") -> bool:
    """True si el evento es en realidad un recurso o contenido (PDF, ranking,
    tienda, comunidad, blog) disfrazado de evento. En ese caso NO se publica."""
    titulo_low = (titulo or "").lower()
    if any(p in titulo_low for p in PALABRAS_NO_EVENTO):
        return True
    if url:
        dominio = dominio_de(url)
        if any(dom in dominio for dom in DOMINIOS_NO_EVENTO):
            return True
    return False


EMOJI_TIPO = {
    "ensayo": "📝",
    "inscripcion": "📋",
    "resultados": "📊",
    "cierre_plazo": "⏰",
    "generico": "📌",
}

# Colores de la barra lateral del embed, decimal (hex -> decimal)
COLOR_TIPO = {
    "ensayo": 3447003,       # azul
    "inscripcion": 3066993,  # verde
    "resultados": 10181046,  # morado
    "cierre_plazo": 15158332,  # rojo
    "generico": 9807270,     # gris
}
COLOR_NUEVO = 15844367  # dorado, para destacar el anuncio de evento nuevo

CIERRE_COMUNIDAD = (
    f"Cuéntennos en <#{CANAL_GENERAL_ID}> o en <#{CANAL_METAS_ID}> cómo les fue."
)
CIERRE_MOTIVACIONAL = "**PAES X** — Tu esfuerzo de hoy es tu cupo de mañana. 💪"
CIERRE_ACCION = "No dejes todo para el último momento, organízate desde ya. 🚀"
CIERRE_URGENCIA = "⚠️ **No te quedes fuera.** Revisa que todo esté en orden."

FOOTER = {"text": "PAES X · Admisión 2027"}


MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def fecha_larga(fecha_evento: date) -> str:
    """Ej: 22 de julio de 2026"""
    return f"{fecha_evento.day} de {MESES_ES[fecha_evento.month - 1]} de {fecha_evento.year}"


def hoy() -> date:
    if TZ:
        return datetime.now(TZ).date()
    return date.today()


def dias_texto(dias: int) -> str:
    if dias == 0:
        return "hoy"
    if dias == 1:
        return "mañana"
    return f"en {dias} días"


def campos_evento(evento: dict) -> list:
    fecha_raw = evento.get("fecha")
    if fecha_raw:
        fields = [
            {"name": "📅 Fecha", "value": fecha_larga(date.fromisoformat(fecha_raw)), "inline": True}
        ]
    else:
        fields = [{"name": "📅 Fecha", "value": "Por confirmar", "inline": True}]
    if (evento.get("hora") or "").strip():
        fields.append({"name": "🕐 Hora", "value": evento["hora"], "inline": True})
    if (evento.get("modalidad") or "").strip():
        fields.append({"name": "📍 Modalidad", "value": evento["modalidad"], "inline": True})
    if (evento.get("notas") or "").strip():
        fields.append({"name": "🗒️ Notas", "value": evento["notas"], "inline": False})
    return fields


def construir_embed(evento: dict, titulo_embed: str, cuerpo: str, cierre: str,
                     color: int, link_texto: str = "Revisa aquí") -> dict:
    link = evento["link"]
    descripcion = f"{cuerpo}\n\n🔗 [{link_texto}]({link})\n\n{cierre}"
    return {
        "title": titulo_embed,
        "url": link,  # hace que el título del embed también sea clickeable
        "description": descripcion,
        "color": color,
        "fields": campos_evento(evento),
        "footer": FOOTER,
    }


# ---------- Plantillas por tipo de evento ----------

def msg_ensayo(evento: dict, dias: int) -> dict:
    titulo = evento["titulo"]
    color = COLOR_TIPO["ensayo"]
    fecha_evento = date.fromisoformat(evento["fecha"])
    fecha_txt = fecha_larga(fecha_evento)

    if dias == 0:
        titulo_embed = f"📝 ¡Hoy rindes el {titulo}!"
        cuerpo = (
            "Hoy es el gran día. Ya hiciste tu preparación — ahora toca confiar "
            "en ti mismo y dar lo mejor. Lleva tus materiales y llega con tiempo."
        )
        cierre = CIERRE_COMUNIDAD
    elif dias == 1:
        titulo_embed = f"📝 Mañana rindes el {titulo}"
        cuerpo = (
            f"Mañana, **{fecha_txt}**, es el día. Aprovecha hoy para un último "
            "repaso liviano, descansar bien y dejar todo listo para mañana."
        )
        cierre = CIERRE_MOTIVACIONAL
    elif dias <= 7:
        titulo_embed = f"📝 {titulo} — {dias_texto(dias)}"
        cuerpo = (
            f"El **{fecha_txt}** rindes el {titulo}. Buen momento para reforzar "
            "tus puntos más débiles con un repaso enfocado, sin agobiarte."
        )
        cierre = CIERRE_MOTIVACIONAL
    else:
        titulo_embed = f"📝 {titulo} — {dias_texto(dias)}"
        cuerpo = (
            f"El **{fecha_txt}** se realiza el {titulo}. Todavía tienes tiempo "
            "de sobra para armar un plan de estudio ordenado y llegar preparado."
        )
        cierre = CIERRE_MOTIVACIONAL

    return construir_embed(evento, titulo_embed, cuerpo, cierre, color, "Revisa aquí")


def msg_inscripcion(evento: dict, dias: int) -> dict:
    titulo = evento["titulo"]
    color = COLOR_TIPO["inscripcion"]
    fecha_evento = date.fromisoformat(evento["fecha"])
    fecha_txt = fecha_larga(fecha_evento)

    if dias == 0:
        titulo_embed = f"📋 ¡Último día para {titulo}!"
        cuerpo = (
            f"Hoy, **{fecha_txt}**, es el último día para inscribirte. Si todavía "
            "no lo haces, este es el momento — después de hoy ya no vas a poder."
        )
        cierre = CIERRE_URGENCIA
    elif dias == 1:
        titulo_embed = f"📋 Mañana cierra: {titulo}"
        cuerpo = (
            f"Mañana, **{fecha_txt}**, cierra la inscripción. Revisa que tengas "
            "todos tus documentos listos para no quedarte fuera por algo evitable."
        )
        cierre = CIERRE_URGENCIA
    else:
        titulo_embed = f"📋 {titulo} — {dias_texto(dias)}"
        cuerpo = (
            f"Puedes inscribirte hasta el **{fecha_txt}**. Te dejamos el link "
            "para que lo hagas con tiempo y sin apuro."
        )
        cierre = CIERRE_ACCION

    return construir_embed(evento, titulo_embed, cuerpo, cierre, color, "Inscríbete aquí")


def msg_resultados(evento: dict, dias: int) -> dict:
    titulo = evento["titulo"]
    color = COLOR_TIPO["resultados"]
    fecha_evento = date.fromisoformat(evento["fecha"])
    fecha_txt = fecha_larga(fecha_evento)
    titulo_embed = f"📊 ¡Ya están disponibles los {titulo}!"
    cuerpo = (
        f"Se publicaron hoy, **{fecha_txt}**. Es un buen momento para revisar "
        "cómo te fue, identificar qué reforzar y seguir avanzando en tu preparación."
    )

    return construir_embed(evento, titulo_embed, cuerpo, CIERRE_COMUNIDAD, color, "Consulta tus resultados aquí")


def msg_cierre_plazo(evento: dict, dias: int) -> dict:
    titulo = evento["titulo"]
    color = COLOR_TIPO["cierre_plazo"]
    fecha_evento = date.fromisoformat(evento["fecha"])
    fecha_txt = fecha_larga(fecha_evento)

    if dias == 0:
        titulo_embed = f"⏰ ¡Hoy vence el plazo de {titulo}!"
        cuerpo = (
            f"Hoy, **{fecha_txt}**, vence el plazo. Es tu última oportunidad — "
            "si te falta algún trámite, complétalo ahora."
        )
    elif dias == 1:
        titulo_embed = f"⏰ Mañana vence: {titulo}"
        cuerpo = (
            f"Mañana, **{fecha_txt}**, vence el plazo. Queda muy poco tiempo, "
            "verifica que no te falte nada."
        )
    else:
        titulo_embed = f"⏰ {titulo} — {dias_texto(dias)}"
        cuerpo = (
            f"El plazo vence el **{fecha_txt}**. Ve organizando lo que "
            "necesites para no dejarlo para el último momento."
        )

    return construir_embed(evento, titulo_embed, cuerpo, CIERRE_URGENCIA, color, "Revisa aquí")


def msg_generico(evento: dict, dias: int) -> dict:
    titulo = evento["titulo"]
    color = COLOR_TIPO["generico"]
    fecha_evento = date.fromisoformat(evento["fecha"])
    fecha_txt = fecha_larga(fecha_evento)
    titulo_embed = f"📌 {titulo} — {dias_texto(dias)}"
    cuerpo = f"Este evento se realiza el **{fecha_txt}**. Aquí tienes los detalles."

    return construir_embed(evento, titulo_embed, cuerpo, CIERRE_MOTIVACIONAL, color, "Revisa aquí")


def frase_plazo(dias_restantes: int) -> str:
    if dias_restantes < 0:
        return "el plazo ya pasó"
    if dias_restantes == 0:
        return "es **hoy**"
    if dias_restantes == 1:
        return "es **mañana**"
    return f"quedan **{dias_restantes} días**"


def msg_nuevo(evento: dict) -> dict:
    """Embed que se publica el MISMO día en que un evento se agrega
    a events.json (si está dentro de la ventana de anuncio inmediato).
    Da contexto explícito de la fecha y el estado real del plazo,
    para que no se preste a confusión (ej. que un 'cierre de inscripción'
    no se lea como si ya hubiera cerrado)."""
    titulo = evento["titulo"]
    tipo = evento.get("tipo", "generico")
    fecha_evento = date.fromisoformat(evento["fecha"])
    fecha_txt = fecha_larga(fecha_evento)
    dias_restantes = (fecha_evento - hoy()).days
    plazo = frase_plazo(dias_restantes)

    titulo_embed = f"🆕 Nuevo en la agenda — {titulo}"

    if tipo == "inscripcion":
        cuerpo = (
            f"Ya puedes inscribirte. El plazo cierra el **{fecha_txt}** "
            f"({plazo} para hacerlo) — todavía no se ha cerrado."
        )
    elif tipo == "cierre_plazo":
        cuerpo = (
            f"Este es el plazo límite de este trámite: **{fecha_txt}**. "
            f"**Aún no se cierra** — {plazo}."
        )
    elif tipo == "ensayo":
        cuerpo = f"Se agregó un nuevo ensayo a la agenda. Se rinde el **{fecha_txt}** ({plazo})."
    elif tipo == "resultados":
        cuerpo = f"Ya está agendada la publicación de resultados para el **{fecha_txt}** ({plazo})."
    else:
        cuerpo = f"Se agregó un nuevo evento a la agenda, programado para el **{fecha_txt}** ({plazo})."

    if tipo in ("ensayo", "inscripcion"):
        cuerpo += (
            "\n\n⚠️ Los cupos suelen ser limitados y se agotan rápido — "
            "no esperes a último momento para inscribirte."
        )

    return construir_embed(evento, titulo_embed, cuerpo, CIERRE_ACCION, COLOR_NUEVO, "Inscríbete/Revisa aquí")


def msg_pendiente(evento: dict) -> dict:
    """Embed para un evento con "fecha por confirmar". Los scrapers ya NO
    crean este tipo de evento (prefieren no publicar nada a publicar una
    fecha incierta o pedir revisión manual); este embed queda solo para
    eventos que quedaron así en events.json antes de ese cambio."""
    titulo = evento["titulo"]
    tipo = evento.get("tipo", "generico")
    etiqueta = {
        "ensayo": "Ensayo",
        "inscripcion": "Inscripción",
        "resultados": "Resultados",
        "cierre_plazo": "Plazo",
    }.get(tipo, "Evento")
    emoji = EMOJI_TIPO.get(tipo, "🔎")
    titulo_embed = f"{emoji} {etiqueta} detectado — {titulo} (fecha por confirmar)"
    cuerpo = (
        f"Encontramos indicios de un próximo **{titulo}**, pero no pudimos "
        "leer la fecha exacta automáticamente (la página no la indica con "
        "claridad, o tiene más de una fecha mencionada). Entra al link para "
        "confirmar cuándo es."
    )
    return construir_embed(evento, titulo_embed, cuerpo, CIERRE_ACCION, COLOR_NUEVO, "Revisa la fecha aquí")


CONSTRUCTORES = {
    "ensayo": msg_ensayo,
    "inscripcion": msg_inscripcion,
    "resultados": msg_resultados,
    "cierre_plazo": msg_cierre_plazo,
    "generico": msg_generico,
}


def construir_mensaje(evento: dict, dias: int) -> dict:
    tipo = evento.get("tipo", "generico")
    fn = CONSTRUCTORES.get(tipo, msg_generico)
    return fn(evento, dias)


def enviar_webhook(embed: dict) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("ERROR: falta la variable de entorno DISCORD_WEBHOOK_URL", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({"embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            # Discord/Cloudflare bloquea el User-Agent por defecto de urllib
            # (error 1010). Con un User-Agent de navegador normal funciona.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        print(f"ERROR al enviar a Discord: {e.code} {e.read()}", file=sys.stderr)
        raise


def cargar_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        contenido = f.read()
    if not contenido.strip():
        return default
    try:
        return json.loads(contenido)
    except json.JSONDecodeError as e:
        print(
            f"AVISO: {path} tiene un error de sintaxis JSON ({e}). "
            "Se usará un valor por defecto para no detener la ejecución. "
            "Revisa y corrige el archivo (comas de más/menos son la causa más común).",
            file=sys.stderr,
        )
        return default


def guardar_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)


def main():
    data = cargar_json(EVENTS_FILE, {"eventos": []})
    eventos = data.get("eventos", [])
    log = cargar_json(LOG_FILE, {})

    hoy_fecha = hoy()
    enviados = 0

    for evento in eventos:
        # Nunca publicar contenido que no es un evento (noticia, ranking,
        # tienda, comunidad) aunque haya quedado en events.json.
        if es_contenido_no_evento(
            evento.get("titulo") or "",
            evento.get("link") or "",
        ):
            print(f"Omitido (no es un evento con fecha): {evento.get('id')}")
            continue

        fecha_raw = evento.get("fecha")

        if not fecha_raw:
            # Evento con fecha por confirmar (lo agrega scraper_universidades.py
            # cuando detecta un posible ensayo pero no una fecha clara). Se
            # avisa UNA sola vez -- no hay fecha para calcular cuenta regresiva.
            key_pendiente = f"{evento.get('id', evento['titulo'])}_pendiente"
            if not log.get(key_pendiente):
                print(f"Enviando aviso de evento pendiente de confirmar: {key_pendiente}")
                enviar_webhook(msg_pendiente(evento))
                log[key_pendiente] = True
                enviados += 1
            continue

        try:
            fecha_evento = date.fromisoformat(fecha_raw)
        except ValueError:
            print(f"AVISO: evento '{evento.get('id')}' tiene fecha inválida, se omite.")
            continue

        tipo = evento.get("tipo", "generico")
        dias_restantes = (fecha_evento - hoy_fecha).days

        # Un evento que ya pasó nunca debe generar avisos (ni el de "nuevo"
        # ni ningún recordatorio), sin importar qué diga sent_log.json o
        # avisos_dias. Esto es lo que antes producía mensajes del tipo
        # "ensayo en -7 días" después de la fecha del evento.
        if dias_restantes < 0:
            continue

        # --- Anuncio inmediato: si este evento nunca se ha anunciado Y está
        # dentro de la ventana razonable (o se fuerza con "anuncio_inmediato"),
        # se publica HOY sin importar los avisos_dias (útil cuando un evento
        # se anuncia con poca anticipación y hay cupos limitados). Si el
        # evento está muy lejos todavía, se espera y se revisa de nuevo cada
        # día hasta que entre en la ventana. ---
        forzar = evento.get("anuncio_inmediato")  # true/false explícito, o None = automático
        if forzar is True:
            debe_anunciar_ahora = True
        elif forzar is False:
            debe_anunciar_ahora = False
        else:
            debe_anunciar_ahora = dias_restantes <= UMBRAL_ANUNCIO_INMEDIATO_DIAS

        key_nuevo = f"{evento.get('id', evento['titulo'])}_nuevo"
        if debe_anunciar_ahora and not log.get(key_nuevo):
            print(f"Enviando anuncio de evento nuevo: {key_nuevo}")
            enviar_webhook(msg_nuevo(evento))
            log[key_nuevo] = True
            enviados += 1

        avisos_dias = evento.get("avisos_dias")
        if avisos_dias is None:
            avisos_dias = DEFAULTS_POR_TIPO.get(tipo, [0])
        elif isinstance(avisos_dias, (int, float)):
            avisos_dias = [int(avisos_dias)]  # tolerar un único int ("avisos_dias": 7)
        elif not isinstance(avisos_dias, list):
            print(
                f"AVISO: evento '{evento.get('id')}' tiene avisos_dias inválido "
                f"({type(avisos_dias).__name__}); se usan los defaults.",
                file=sys.stderr,
            )
            avisos_dias = DEFAULTS_POR_TIPO.get(tipo, [0])
        else:
            avisos_dias = [d for d in avisos_dias if isinstance(d, int)]
        avisos_dias_negativos = [d for d in avisos_dias if d < 0]
        if avisos_dias_negativos:
            print(
                f"AVISO: evento '{evento.get('id')}' tiene avisos_dias negativos "
                f"{avisos_dias_negativos} (deben ser días ANTES del evento, "
                "positivos). Se ignoran esos valores."
            )
        avisos_dias = [d for d in avisos_dias if d >= 0]

        for dias in avisos_dias:
            fecha_aviso = fecha_evento - timedelta(days=dias)
            if fecha_aviso != hoy_fecha:
                continue

            key = f"{evento.get('id', evento['titulo'])}_{dias}"
            if log.get(key):
                continue  # ya enviado antes, no duplicar

            embed = construir_mensaje(evento, dias)
            print(f"Enviando aviso: {key}")
            enviar_webhook(embed)
            log[key] = True
            enviados += 1

    guardar_json(LOG_FILE, log)
    print(f"Listo. Avisos enviados hoy: {enviados}")


if __name__ == "__main__":
    main()
