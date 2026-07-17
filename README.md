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

## Mantenimiento

Prácticamente cero. Una vez configurado, solo necesitas volver a
`events.json` cuando quieras agregar un evento nuevo. El bot corre solo,
no duplica avisos, y no requiere que mantengas nada "prendido".

## Notas

- El horario del cron está en UTC; viene ajustado para publicar cerca de las
  8-9 AM hora de Chile. Puedes cambiarlo editando el `cron` en
  `.github/workflows/avisos.yml`.
- GitHub Actions es gratuito para repositorios públicos (minutos ilimitados)
  y también da minutos gratuitos de sobra para repos privados en una tarea
  tan liviana como esta (corre en segundos, una vez al día).
