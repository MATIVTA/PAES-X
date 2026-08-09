# PAES X — Bot de avisos automáticos para #eventos-paes

Publica automáticamente en tu canal de Discord avisos anticipados de eventos
PAES (ensayos, inscripciones, resultados, cierres de plazo), con un mensaje
distinto según el tipo de evento y cuántos días faltan. Corre gratis sobre
GitHub Actions: no necesitas servidor, ni bot conectado 24/7, ni pagar nada.

## Cómo funciona

- `events.json` — tu lista de eventos. Es lo único que editarás normalmente.
- `bot.py` — revisa la fecha de hoy, decide si toca avisar de algún evento
  y arma el mensaje según la plantilla de su tipo.
- `.github/workflows/avisos.yml` — hace que GitHub ejecute `bot.py`
  automáticamente todos los días a la hora que definas.
- `sent_log.json` — registro interno para no enviar el mismo aviso dos veces.
  No lo edites a mano.

## Configuración inicial (una sola vez, ~10 minutos)

1. **Crea el Webhook en Discord**
   - Ve al canal `#eventos-paes` → Editar canal → Integraciones → Webhooks →
     Nuevo Webhook.
   - Ponle un nombre (ej. "PAES X Bot"), opcional un avatar.
   - Copia la **URL del Webhook**. Es secreta, no la compartas.

2. **Sube este proyecto a un repositorio de GitHub**
   - Crea una cuenta en github.com si no tienes.
   - Crea un repositorio nuevo (puede ser privado).
   - Sube todos estos archivos (arrastrándolos desde la web de GitHub
     funciona, o con `git push` si prefieres línea de comandos).

3. **Guarda la URL del Webhook como secreto del repositorio**
   - En tu repo: Settings → Secrets and variables → Actions → New repository
     secret.
   - Nombre: `DISCORD_WEBHOOK_URL`
   - Valor: la URL que copiaste en el paso 1.

4. **Listo.** El workflow ya está programado (`cron` en `avisos.yml`) para
   correr todos los días. Puedes probarlo de inmediato sin esperar: pestaña
   **Actions** → "Avisos PAES X" → **Run workflow**.

## Cómo agregar o editar un evento

Edita `events.json` directamente desde la web de GitHub (icono de lápiz) y
guarda ("Commit changes"). No necesitas tocar `bot.py` para nada de esto.

Campos de cada evento:

| Campo         | Obligatorio | Descripción                                                        |
|---------------|:-----------:|----------------------------------------------------------------------|
| `id`          | Sí          | Identificador único, sin espacios (ej. `ensayo-2-2026`).             |
| `tipo`        | Sí          | `ensayo`, `inscripcion`, `resultados`, `cierre_plazo` o `generico`.   |
| `titulo`      | Sí          | Nombre del evento tal cual quieres que aparezca en el mensaje.        |
| `fecha`       | Sí          | Formato `YYYY-MM-DD`.                                                 |
| `hora`        | No          | Texto libre, ej. `"09:00"`.                                          |
| `link`        | Sí          | URL de inscripción / información / resultados.                       |
| `modalidad`   | No          | Ej. `"Online"`, `"Presencial en tu liceo"`. Déjalo `""` si no aplica. |
| `avisos_dias` | No          | Lista de días de anticipación, ej. `[14, 7, 1, 0]`. Si lo omites, se usa un default según el tipo (ver tabla abajo). |
| `anuncio_inmediato` | No    | `true` / `false`. Por defecto (si no lo defines) el bot decide solo: anuncia el mismo día en que agregas el evento **solo si** faltan 25 días o menos para la fecha (`UMBRAL_ANUNCIO_INMEDIATO_DIAS` en `bot.py`). Si faltan más, espera y lo anuncia automáticamente el día en que el evento entre en esa ventana. Pon `true` para forzar el anuncio inmediato sin importar cuánto falte, o `false` para que nunca se anuncie de forma inmediata (solo cuenta regresiva). |
| `origen`      | No          | Campo informativo, lo agrega el scraper automático (`scraper_demre.py`) a los eventos que detecta solo. Puedes borrarlo o ignorarlo. |

**Importante:** `avisos_dias` siempre son días **antes** del evento, y siempre positivos (`[14, 7, 1, 0]`, nunca `[-14, -7, -1, 0]`). Un evento con fecha ya pasada nunca genera avisos, sin importar qué diga `avisos_dias` o `sent_log.json`.

### Defaults de anticipación por tipo (si no defines `avisos_dias`)

- `ensayo` → 14, 7, 1 y 0 días antes
- `inscripcion` → 7, 3, 1 y 0 días antes
- `resultados` → solo el mismo día (0)
- `cierre_plazo` → 3, 1 y 0 días antes
- `generico` → 7, 1 y 0 días antes

## Tono de los mensajes por tipo

- **ensayo** → recordatorio/motivación a prepararse, cierre con la frase
  "Tu esfuerzo de hoy es tu cupo de mañana", y el día del ensayo invita a
  comentar en el canal.
- **inscripcion** → informativo y orientado a la acción ("Inscríbete aquí"),
  con urgencia creciente cerca del cierre.
- **resultados** → tono de comunidad, invita a comentar cómo les fue.
- **cierre_plazo** → urgencia explícita.

Si quieres cambiar la redacción, todo el texto de las plantillas está en
`bot.py`, en las funciones `msg_ensayo`, `msg_inscripcion`, etc. — son texto
plano, no requieren saber programar para editarlas.

## Automatización: eventos que se agregan solos

Aparte de `avisos.yml` (que publica los avisos todos los días), hay dos
workflows más, ambos gratis y sin ningún servicio pago de por medio:

- **`scraper_demre.yml`** (`scripts/scraper_demre.py`) — todos los lunes lee
  el calendario oficial de DEMRE (`demre.cl/calendario/...`) y agrega a
  `events.json` cualquier hito oficial (inscripciones, PAES, resultados,
  postulación, matrícula, etc.) que todavía no tengas cargado. Nunca borra
  ni modifica un evento que ya exista — si un hito del calendario coincide
  en fecha y palabras clave con uno que ya tienes, lo salta para no duplicar.
  Es la fuente más confiable porque el formato de esa página es estable.

- **`descubridor.yml`** (`scripts/descubridor.py`) — todas las semanas
  busca en DuckDuckGo (sin API key, gratis) páginas nuevas de ensayos PAES,
  a nivel nacional y por ciudad (Concepción, Valparaíso, Temuco, La Serena,
  Antofagasta, Valdivia, Puerto Montt, Rancagua, Talca...), incluyendo
  Instagram y Facebook, que suele ser donde los liceos y sedes regionales
  avisan primero. Ojo con esto último: Instagram y la versión de escritorio
  de Facebook cargan el contenido con JavaScript y piden login, así que un
  script sin navegador (la única forma de hacerlo gratis, sin depender de
  ninguna cuenta) casi nunca puede leer el texto del post — para Facebook se
  intenta la versión móvil, que a veces sí funciona; para Instagram no hay
  atajo. Cuando no se puede leer, esas páginas caen igual al mecanismo de
  abajo (Issue con el link) en vez de perderse.
- **`vigilancia_universidades.yml`** (`scripts/scraper_universidades.py`) —
  todos los días revisa las páginas semilla (Santo Tomás, UC, U. Autónoma,
  USM, UAH, U. San Sebastián, UNAB+PDV — con sedes en Santiago, Concepción,
  Viña del Mar, Temuco, La Serena, Valdivia, Puerto Montt, Calama, entre
  otras) más las que va encontrando `descubridor.py`, y busca fechas cerca
  de la palabra "ensayo" en el texto de cada una:
  - Si encuentra **una sola fecha futura clara**, agrega el evento a
    `events.json` sola, sin que hagas nada.
  - Si encuentra **varias fechas distintas** (ambigüedad real) o
    **ninguna** (incluye el caso de páginas de redes sociales que no se
    pudieron leer), no adivina — abre un **Issue** en este repo con lo que
    encontró (o con el link, si no encontró nada) para que lo confirmes tú.

Con esto, en la inmensa mayoría de los casos no tienes que tocar nada:
DEMRE y las semillas curadas se agregan solas siempre, la lista de páginas
a vigilar crece sola con el descubridor, y solo terminas revisando un Issue
cuando una página es realmente ambigua o no se pudo leer automáticamente.

## Mantenimiento

Prácticamente cero. El bot corre solo, no duplica avisos, y no requiere que
mantengas nada "prendido". Revisa de vez en cuando los Issues que puedan
abrirse (solo pasa cuando una página no tiene una fecha clara que extraer).

## Notas

- El horario del cron está en UTC; viene ajustado para publicar cerca de las
  8-9 AM hora de Chile. Puedes cambiarlo editando el `cron` en
  `.github/workflows/avisos.yml`.
- GitHub Actions es gratuito para repositorios públicos (minutos ilimitados)
  y también da minutos gratuitos de sobra para repos privados en una tarea
  tan liviana como esta (corre en segundos, una vez al día).
