#!/usr/bin/env python3
"""
PAES X - Bot de avisos automáticos para el canal #eventos-paes
----------------------------------------------------------------
Este script:
1. Lee events.json (la lista de eventos PAES).
2. Calcula si HOY corresponde publicar un aviso para algún evento,
   según su tipo y las reglas de anticipación (avisos_dias).
3. Genera un mensaje distinto según el tipo de evento y la cercanía.
4. Lo publica en Discord vía Webhook.
5. Registra en sent_log.json lo que ya se envió, para no repetirlo.

No requiere librerías externas (solo la librería estándar de Python),
para que el workflow de GitHub Actions no tenga que instalar nada.
"""

import json
import os
import sys
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

EMOJI_TIPO = {
    "ensayo": "📝",
    "inscripcion": "📋",
    "resultados": "📊",
    "cierre_plazo": "⏰",
    "generico": "📌",
}

CIERRE_COMUNIDAD = (
    f"Cuéntennos en <#{CANAL_GENERAL_ID}> o en <#{CANAL_METAS_ID}> cómo les fue."
)
CIERRE_MOTIVACIONAL = "**PAES X** — Tu esfuerzo de hoy es tu cupo de mañana. 💪"
CIERRE_ACCION = "No dejes todo para el último momento, organízate desde ya. 🚀"
CIERRE_URGENCIA = "⚠️ **No te quedes fuera.** Revisa que todo esté en orden."


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


def linea_modalidad(evento: dict) -> str:
    modalidad = evento.get("modalidad", "").strip()
    if not modalidad:
        return ""
    return f"📍 Modalidad: {modalidad}\n"


def linea_hora(evento: dict) -> str:
    hora = evento.get("hora", "").strip()
    if not hora:
        return ""
    return f"🕐 Hora: {hora}\n"


# ---------- Plantillas por tipo de evento ----------

def msg_ensayo(evento: dict, dias: int) -> str:
    titulo = evento["titulo"]
    link = evento["link"]
    if dias == 0:
        cabecera = f"📝 **¡HOY es el {titulo}!**"
        cuerpo = "Es tu momento de poner a prueba tu preparación. Respira, confía en tu proceso y da lo mejor de ti."
        cierre = CIERRE_COMUNIDAD
    elif dias == 1:
        cabecera = f"📝 **Mañana rinden el {titulo}**"
        cuerpo = "Último día para repasar con calma: duerme bien, prepara tus materiales y llega con tiempo."
        cierre = CIERRE_MOTIVACIONAL
    elif dias <= 7:
        cabecera = f"📝 **{titulo} — {dias_texto(dias)}**"
        cuerpo = "Es un buen momento para reforzar tus áreas más débiles y hacer un repaso general."
        cierre = CIERRE_MOTIVACIONAL
    else:
        cabecera = f"📝 **{titulo} — {dias_texto(dias)}**"
        cuerpo = "Todavía tienes tiempo de sobra para organizar un plan de estudio y prepararte con calma."
        cierre = CIERRE_MOTIVACIONAL

    return (
        f"{cabecera}\n\n"
        f"{cuerpo}\n"
        f"{linea_hora(evento)}"
        f"{linea_modalidad(evento)}\n"
        f"🔗 [Revisa aquí]({link})\n\n"
        f"{cierre}"
    )


def msg_inscripcion(evento: dict, dias: int) -> str:
    titulo = evento["titulo"]
    link = evento["link"]
    if dias == 0:
        cabecera = f"📋 **¡ÚLTIMO DÍA para {titulo}!**"
        cuerpo = "Si aún no te has inscrito, hazlo ahora mismo. Después de hoy ya no podrás hacerlo."
        cierre = CIERRE_URGENCIA
    elif dias == 1:
        cabecera = f"📋 **Mañana cierra: {titulo}**"
        cuerpo = "Queda un día. No dejes pasar el plazo, revisa que tengas todos tus documentos listos."
        cierre = CIERRE_URGENCIA
    else:
        cabecera = f"📋 **{titulo} — {dias_texto(dias)}**"
        cuerpo = "Aquí tienes toda la información para inscribirte con tiempo y sin apuro."
        cierre = CIERRE_ACCION

    return (
        f"{cabecera}\n\n"
        f"{cuerpo}\n"
        f"{linea_hora(evento)}"
        f"{linea_modalidad(evento)}\n"
        f"🔗 [Inscríbete aquí]({link})\n\n"
        f"{cierre}"
    )


def msg_resultados(evento: dict, dias: int) -> str:
    titulo = evento["titulo"]
    link = evento["link"]
    cabecera = f"📊 **¡Ya están disponibles los {titulo}!**"
    cuerpo = "Este es un buen momento para revisar tu desempeño, identificar qué reforzar y seguir avanzando."

    return (
        f"{cabecera}\n\n"
        f"{cuerpo}\n"
        f"{linea_hora(evento)}\n"
        f"🔗 [Consulta tus resultados aquí]({link})\n\n"
        f"{CIERRE_COMUNIDAD}"
    )


def msg_cierre_plazo(evento: dict, dias: int) -> str:
    titulo = evento["titulo"]
    link = evento["link"]
    if dias == 0:
        cabecera = f"⏰ **¡HOY vence el plazo de {titulo}!**"
        cuerpo = "Es tu última oportunidad. Si te falta algún trámite, hazlo ahora."
    elif dias == 1:
        cabecera = f"⏰ **Mañana vence: {titulo}**"
        cuerpo = "Queda muy poco tiempo. Verifica que no te falte nada."
    else:
        cabecera = f"⏰ **{titulo} — {dias_texto(dias)}**"
        cuerpo = "Ve organizando lo que necesites para no dejarlo para el final."

    return (
        f"{cabecera}\n\n"
        f"{cuerpo}\n"
        f"{linea_hora(evento)}\n"
        f"🔗 [Revisa aquí]({link})\n\n"
        f"{CIERRE_URGENCIA}"
    )


def msg_generico(evento: dict, dias: int) -> str:
    titulo = evento["titulo"]
    link = evento["link"]
    cabecera = f"📌 **{titulo} — {dias_texto(dias)}**"
    return (
        f"{cabecera}\n\n"
        f"{linea_hora(evento)}"
        f"{linea_modalidad(evento)}\n"
        f"🔗 [Revisa aquí]({link})\n\n"
        f"{CIERRE_MOTIVACIONAL}"
    )


def msg_nuevo(evento: dict) -> str:
    """Mensaje que se publica el MISMO día en que un evento se agrega
    a events.json, sin esperar a la cuenta regresiva. Pensado para casos
    como 'la UOH anunció hoy un ensayo en 1 mes con cupos limitados'."""
    titulo = evento["titulo"]
    link = evento["link"]
    tipo = evento.get("tipo", "generico")
    emoji = EMOJI_TIPO.get(tipo, "📌")

    fecha_evento = date.fromisoformat(evento["fecha"])
    fecha_str = fecha_evento.strftime("%d-%m-%Y")

    cabecera = f"🆕 {emoji} **¡Nuevo evento anunciado!**\n**{titulo}**"

    cuerpo = f"📅 Fecha: {fecha_str}\n"
    cuerpo += linea_hora(evento)
    cuerpo += linea_modalidad(evento)

    aviso_cupos = ""
    if tipo in ("ensayo", "inscripcion"):
        aviso_cupos = (
            "⚠️ Los cupos suelen ser limitados y se agotan rápido — "
            "no esperes a último momento para inscribirte.\n\n"
        )

    return (
        f"{cabecera}\n\n"
        f"{cuerpo}\n"
        f"{aviso_cupos}"
        f"🔗 [Inscríbete/Revisa aquí]({link})\n\n"
        f"{CIERRE_ACCION}"
    )


CONSTRUCTORES = {
    "ensayo": msg_ensayo,
    "inscripcion": msg_inscripcion,
    "resultados": msg_resultados,
    "cierre_plazo": msg_cierre_plazo,
    "generico": msg_generico,
}


def construir_mensaje(evento: dict, dias: int) -> str:
    tipo = evento.get("tipo", "generico")
    fn = CONSTRUCTORES.get(tipo, msg_generico)
    return fn(evento, dias)


def enviar_webhook(mensaje: str) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("ERROR: falta la variable de entorno DISCORD_WEBHOOK_URL", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({"content": mensaje}).encode("utf-8")
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
        return json.load(f)


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
        try:
            fecha_evento = date.fromisoformat(evento["fecha"])
        except (KeyError, ValueError):
            print(f"AVISO: evento '{evento.get('id')}' tiene fecha inválida, se omite.")
            continue

        tipo = evento.get("tipo", "generico")

        # --- Anuncio inmediato: si este evento nunca se ha anunciado,
        # se publica HOY sin importar cuántos días falten (útil cuando
        # un evento se anuncia con poca anticipación y hay cupos limitados). ---
        key_nuevo = f"{evento.get('id', evento['titulo'])}_nuevo"
        if not log.get(key_nuevo):
            print(f"Enviando anuncio de evento nuevo: {key_nuevo}")
            enviar_webhook(msg_nuevo(evento))
            log[key_nuevo] = True
            enviados += 1

        avisos_dias = evento.get("avisos_dias") or DEFAULTS_POR_TIPO.get(tipo, [0])

        for dias in avisos_dias:
            fecha_aviso = fecha_evento - timedelta(days=dias)
            if fecha_aviso != hoy_fecha:
                continue

            key = f"{evento.get('id', evento['titulo'])}_{dias}"
            if log.get(key):
                continue  # ya enviado antes, no duplicar

            mensaje = construir_mensaje(evento, dias)
            print(f"Enviando aviso: {key}")
            enviar_webhook(mensaje)
            log[key] = True
            enviados += 1

    guardar_json(LOG_FILE, log)
    print(f"Listo. Avisos enviados hoy: {enviados}")


if __name__ == "__main__":
    main()
