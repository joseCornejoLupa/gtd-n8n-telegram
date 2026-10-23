# gtd-n8n-telegram

Backend de **Getting Things Done (GTD)** pensado para usarse desde **n8n** y un **bot de Telegram**.
Solo API REST: el frontend es el bot, orquestado por n8n.

- FastAPI + SQLAlchemy 2 + Alembic
- PostgreSQL (se conecta a una instancia existente, no incluye una)
- Un único contenedor Docker

> Estado: en construcción. Ya disponible: captura, inbox, clarificación, listas (siguientes
> acciones, esperas, algún día), búsqueda, edición, completar, reabrir y descartar tareas, y la zona horaria del usuario. Faltan los
> endpoints de proyectos y los resúmenes. Ver la [hoja de ruta](#hoja-de-ruta).

## Principio: nada sensible en el repo

Este repositorio es público. No hay contraseñas, API keys, tokens ni IDs de usuario en el código
ni en el compose:

- Los secretos se montan como **Docker secrets** desde archivos locales ignorados por git (`./secrets/`).
- El host de Postgres se resuelve con `host.docker.internal` (mapeado al host por `extra_hosts`).
- Valores no sensibles (puerto, nombre de BD, usuario) tienen defaults y se pueden cambiar con
  variables `GTD_DB_HOST`, `GTD_DB_PORT`, `GTD_DB_NAME`, `GTD_DB_USER`.
- Los ajustes propios de tu servidor (red de n8n, puerto, etc.) van en un
  `docker-compose.override.yml` local, que git ignora.

## Requisitos para desplegar

**En el servidor**

- Linux con **Docker Engine 20.10 o superior** (hace falta para `host-gateway`) y **Docker Compose v2**
  (el comando `docker compose`, no `docker-compose`).
- **git**, para clonar el repo.
- **PostgreSQL** ya instalado y corriendo en el mismo host (probado con la versión 16), y acceso de
  superusuario (`sudo -u postgres psql`) para crear el rol y la base de datos.
- Conexión a internet **durante el build** (descarga la imagen `python:3.12-slim` y los paquetes de
  pip). En funcionamiento no necesita salir a internet.
- `openssl` (o cualquier generador de claves aleatorias) para crear la API key.

**Red**

- El contenedor tiene que poder llegar a Postgres en el host: Postgres debe escuchar en la interfaz
  que ve Docker, `pg_hba.conf` debe admitirlo y el firewall del host no debe bloquearlo
  (ver el paso 3).
- n8n tiene que poder llegar al backend (ver el paso 6).

**Datos que tienes que tener a mano**

- Tu **ID numérico de Telegram**. Lo ves en n8n, en la salida del *Telegram Trigger*:
  `{{ $json.message.from.id }}`.
- Una clave para el rol `gtd` de Postgres (la eliges tú al crearlo).

**Lo que NO necesita este backend**: el token del bot de Telegram. Vive solo en n8n.

## Despliegue paso a paso

### 1. Clonar

```bash
git clone https://github.com/joseCornejoLupa/gtd-n8n-telegram.git
cd gtd-n8n-telegram
```

### 2. Base de datos (una vez)

En el Postgres del host. `\password` pide la clave de forma interactiva, así no queda en ningún
archivo ni en el historial del shell:

```bash
sudo -u postgres psql
```

```sql
CREATE ROLE gtd LOGIN;
\password gtd
CREATE DATABASE gtd OWNER gtd;
```

### 3. Que el contenedor llegue a Postgres

Para esto necesitas la red que crea Docker Compose. Créala sin arrancar nada:

```bash
docker compose create
docker network inspect gtd-n8n-telegram_default \
  --format 'subred: {{(index .IPAM.Config 0).Subnet}}  gateway: {{(index .IPAM.Config 0).Gateway}}'
```

(El nombre de la red es `<directorio>_default`; cámbialo si clonaste en otra carpeta.)
Con esos dos valores:

1. **`postgresql.conf`** (en Ubuntu, `/etc/postgresql/<versión>/main/`): haz que Postgres escuche
   también en el gateway, además de en localhost:

   ```
   listen_addresses = 'localhost,<GATEWAY>'
   ```

2. **`pg_hba.conf`**: autoriza al rol `gtd` solo desde esa subred, con cifrado de clave:

   ```
   host  gtd  gtd  <SUBRED>  scram-sha-256
   ```

3. **Firewall** (si usas `ufw`): el tráfico del contenedor hacia el host pasa por sus reglas.

   ```bash
   sudo ufw allow from <SUBRED> to any port 5432 proto tcp
   ```

4. Reinicia Postgres (`listen_addresses` exige reinicio, no basta con recargar):

   ```bash
   sudo systemctl restart postgresql
   ```

La subred puede cambiar si más adelante haces `docker compose down` y la red se recrea con otro
rango. Si pasa, repite este paso o fija la subred en tu `docker-compose.override.yml`.

### 4. Secretos

Tres archivos en `./secrets/` (git los ignora; nada de esto se sube al repo):

```bash
mkdir -p secrets
umask 077
printf '%s' 'LA_CLAVE_DEL_ROL_GTD' > secrets/db_password.txt
openssl rand -hex 32 | tr -d '\n' > secrets/api_key.txt
printf '%s' 'TU_ID_NUMERICO_DE_TELEGRAM' > secrets/allowed_telegram_ids.txt
```

- `db_password.txt`: la clave del rol `gtd` que pusiste en el paso 2.
- `api_key.txt`: la clave que n8n envía en `X-API-Key`. Guárdala en una **credencial de n8n**
  (tipo *Header Auth*), no escrita dentro de los nodos: así tampoco se filtra si exportas un workflow.
- `allowed_telegram_ids.txt`: IDs de Telegram autorizados, separados por coma o salto de línea.
  **Sin este archivo el servicio no arranca**: nunca queda abierto.

Los secretos se leen al arrancar. Si los cambias, reinicia el contenedor
(`docker compose restart gtd-api`).

### 5. Levantar

```bash
docker compose up -d --build
docker compose logs -f gtd-api
curl http://localhost:8088/health
```

Al arrancar, el contenedor aplica las migraciones pendientes (`alembic upgrade head`) y luego
levanta la API. Si no puede conectarse a Postgres, se reinicia en bucle con el error visible en
los logs (ver [problemas comunes](#problemas-comunes)).

### 6. Conectar n8n

Depende de cómo corre tu n8n:

| n8n corre... | URL del backend en n8n | Qué hace falta |
|---|---|---|
| directamente en el host | `http://localhost:8088` | nada más |
| en Docker, con su propia red | `http://gtd-api:8000` | unir `gtd-api` a la red de n8n (abajo) |
| en Docker, sin red compartida | `http://host.docker.internal:8088` | dar a n8n `extra_hosts: ["host.docker.internal:host-gateway"]` |

Para unir el backend a la red de n8n, crea un `docker-compose.override.yml` junto al compose
(git lo ignora). Averigua el nombre de la red con `docker network ls`:

```yaml
services:
  gtd-api:
    networks: [default, n8n_net]

networks:
  n8n_net:
    external: true
    name: NOMBRE_DE_LA_RED_DE_N8N
```

Credencial en n8n: tipo **Header Auth**, nombre `X-API-Key`, valor el contenido de
`secrets/api_key.txt`.

Nodo *HTTP Request* para capturar lo que llega al bot (orientativo):

- Método `POST`, URL `<URL del backend>/capture`, autenticación con la credencial anterior.
- Header `X-Telegram-User-Id` = `{{ $json.message.from.id }}`
- Body JSON, como expresión (evita problemas de comillas con el texto del mensaje):
  `{{ { "text": $json.message.text, "source_ref": String($json.message.message_id) } }}`

### 7. Verificar

```bash
KEY=$(cat secrets/api_key.txt)
curl -s -X POST http://localhost:8088/capture \
  -H "X-API-Key: $KEY" -H "X-Telegram-User-Id: TU_ID" \
  -H 'Content-Type: application/json' -d '{"text":"prueba de despliegue"}'
curl -s http://localhost:8088/inbox -H "X-API-Key: $KEY" -H "X-Telegram-User-Id: TU_ID"
```

## Seguridad de red

El compose publica el puerto `8088` en todas las interfaces. Ojo: **Docker salta las reglas de
`ufw`** para los puertos que publica, así que `ufw deny 8088` no lo protege. Lo que protege la API
es la API key más la allowlist de Telegram. Si n8n corre en el mismo host fuera de Docker y quieres
que el puerto solo sea accesible desde la propia máquina, o si unes el contenedor a la red de n8n y
ya no necesitas publicarlo, usa tu `docker-compose.override.yml` (requiere Compose 2.24 o superior):

```yaml
services:
  gtd-api:
    ports: !override
      - "127.0.0.1:8088:8000"   # o `ports: !reset []` para no publicar nada
```

## Operación

```bash
docker compose logs -f gtd-api          # logs
docker compose restart gtd-api          # tras cambiar un secreto
```

**Actualizar.** Haz antes una copia de seguridad (abajo). Las migraciones se aplican solas al arrancar.

```bash
git pull
docker compose up -d --build
```

**Copia de seguridad y restauración** (como superusuario de Postgres; no hace falta ninguna clave):

```bash
sudo -u postgres pg_dump -Fc gtd -f gtd-$(date +%F).dump
sudo -u postgres pg_restore -d gtd --clean --if-exists gtd-AAAA-MM-DD.dump
```

**Deshacer la última migración:**

```bash
docker compose run --rm gtd-api alembic downgrade -1
```

**Rotar la API key:** cambia `secrets/api_key.txt`, reinicia `gtd-api` y actualiza la credencial en n8n.

## Problemas comunes

| Síntoma en `docker compose logs gtd-api` o al llamar | Causa probable |
|---|---|
| `connection refused` o `timed out` al conectar con Postgres | Postgres no escucha en el gateway (`listen_addresses`), no se reinició, o el firewall bloquea el 5432 desde la subred |
| `no pg_hba.conf entry for host ...` | La subred de la red de Compose no está en `pg_hba.conf` (¿cambió al recrear la red?) |
| `password authentication failed for user "gtd"` | `secrets/db_password.txt` no coincide con la clave del rol |
| `Falta el secreto: ...` o `La allowlist no contiene ningún ID` | Falta un archivo en `secrets/` o está vacío o mal escrito |
| `host.docker.internal` no resuelve | Docker anterior a 20.10 |
| Puerto 8088 ocupado | Cámbialo con `ports: !override` en tu override |
| `401` | Falta `X-API-Key` o no coincide con `secrets/api_key.txt` |
| `400` | Falta `X-Telegram-User-Id` o no es un número positivo |
| `403` | Ese ID de Telegram no está en `secrets/allowed_telegram_ids.txt` |

## API

Todas las rutas (salvo `/health`) exigen dos headers, que n8n debe enviar en cada llamada:

| Header | Valor |
|---|---|
| `X-API-Key` | la clave de `secrets/api_key.txt` |
| `X-Telegram-User-Id` | el ID de Telegram de quien escribió (`{{ $json.message.from.id }}`) |

Respuestas de autenticación: `401` clave ausente o incorrecta; `400` ID ausente o mal formado;
`403` ID fuera de la allowlist. Primero se valida la clave, así que sin ella no se revela nada.
El usuario se crea automáticamente la primera vez, y solo si está autorizado. La documentación
interactiva (con todos los esquemas) está en `/docs`.

### `POST /capture`

Guarda lo capturado en el inbox, tal cual lo escribió el usuario.

```json
{ "text": "comprar leche", "source_ref": "123456" }
```

`source_ref` (opcional, p. ej. el `message_id` de Telegram) hace la captura idempotente: si n8n
reintenta el mismo mensaje, responde `200` con la tarea original y `"created": false`, sin
duplicar. Una captura nueva responde `201`.

### `GET /inbox?limit=50`

Lo capturado y aún sin clarificar, del más antiguo al más nuevo. Devuelve
`{"count": <total pendiente>, "items": [{"id", "raw_text", "captured_at"}]}`; `count` es el total
aunque `limit` (1 a 200) recorte la lista.

### `POST /tasks/{id}/clarify`

Convierte una captura del inbox en lo que realmente es. `raw_text` (lo capturado) no se modifica.

| `action` | Resultado | Obligatorios | Opcionales |
|---|---|---|---|
| `next` | siguiente acción | `title` | `notes`, `context`, `project_id`, `due_at`, `defer_until` |
| `waiting` | a la espera de alguien | `title`, `waiting_on` | `notes`, `project_id`, `due_at` |
| `someday` | algún día / tal vez | — | `title`, `notes`, `project_id` |
| `reference` | solo información | — | `title`, `notes` |
| `trash` | descartada | — | — |

- Un campo que no corresponde a la acción se rechaza con `422` (no se ignora en silencio). Los
  valores `null` y `""` cuentan como "no enviado", que es lo que n8n suele mandar.
- `context` va por nombre y se crea si no existe. Se normaliza: `PC` y `@pc` son el mismo contexto.
- `project_id` añade la tarea al proyecto. Si es secuencial, va al final de la fila.
- `due_at` y `defer_until` sin zona horaria se interpretan en la zona del usuario; con zona
  (`...+02:00` o `Z`) se respeta.
- La respuesta incluye `available_now`: `true` si la tarea ya aparece en "siguientes acciones";
  `false` si está diferida, bloqueada por la secuencia del proyecto o no es una siguiente acción.
  Sirve para que el bot responda con precisión.
- `404` si la tarea o el proyecto no existen (o son de otro usuario); `409` si la tarea ya no está
  en el inbox o el proyecto está cerrado.

### Listas

Todas devuelven `{"count": <total>, "items": [...]}`; `count` es el total aunque `limit`
(1 a 200, por defecto 50) recorte la lista. Cada tarea trae el nombre de su contexto
(`context`) y el título de su proyecto (`project_title`).

| Ruta | Qué devuelve |
|---|---|
| `GET /next?context=@pc&project_id=` | Lo que puedes hacer ahora: siguientes acciones no diferidas, de proyectos activos y, en los secuenciales, solo el paso que toca. Primero las de fecha límite más próxima. `context` se normaliza (`PC` = `@pc`); un contexto que no existe da lista vacía |
| `GET /waiting?older_than_days=7` | Lo que esperas de otros, de la espera más antigua a la más reciente, con `days_waiting`. Con `older_than_days`, solo las que llevan al menos esos días (para dar seguimiento) |
| `GET /someday` | Algún día / tal vez, de lo más antiguo a lo más nuevo |
| `GET /tasks?status=&project_id=&context=&q=&limit=&offset=` | Búsqueda general, de lo más reciente a lo más antiguo. `status` se puede repetir (`status=next&status=waiting`); sin él no se incluyen las descartadas ni las convertidas en proyecto. `q` busca en título, captura y notas, sin distinguir mayúsculas |
| `GET /tasks/{id}` | Una tarea, con `available_now` |

### `PATCH /tasks/{id}`

Edita una tarea abierta (`next`, `waiting`, `someday`, `reference`) y/o la mueve entre esos estados
con `status`. Solo cambia lo que envías; `null` y `""` cuentan como "no enviado". Para **borrar**
un valor, nómbralo en `clear`:

```json
{ "status": "waiting", "waiting_on": "Carlos", "clear": ["due_at", "context"] }
```

- Campos: `title`, `notes`, `context`, `project_id`, `waiting_on`, `due_at`, `defer_until`.
  Se aplican las mismas reglas que en `/clarify` según el **estado resultante**: `next` exige
  título, `waiting` exige título y `waiting_on`, y solo se aceptan los campos de ese estado (`422`).
- Al pasar a `waiting` se guarda `waiting_since`; al salir de `waiting` se borran `waiting_on` y
  `waiting_since`.
- `project_id` mueve la tarea: en un proyecto secuencial queda al final de la fila. Para sacarla
  de su proyecto, `"clear": ["project_id"]`.
- `409` si la tarea no es editable (en el inbox: usa `/clarify`; completada o descartada: reábrela;
  ya convertida en proyecto).

### Completar, reabrir y descartar

- `POST /tasks/{id}/complete`: marca la tarea como hecha y dice cómo queda su proyecto:
  - `project_stalled`: `true` si el proyecto sigue activo pero ya no tiene siguiente acción ni
    espera (una acción diferida a futuro no cuenta). El bot debería preguntar por el siguiente paso.
  - `project_open_tasks`: tareas del proyecto aún abiertas (siguiente, espera o algún día). Si
    `project_stalled` es `true` y esto es `0`, probablemente el proyecto terminó.
  - `next_task`: en un proyecto secuencial, el paso que acaba de quedar disponible.
  - Es idempotente: completar una tarea ya completada responde `200` con `already_completed: true`.
    Se puede completar desde `inbox`, `next`, `waiting` o `someday`.
- `POST /tasks/{id}/reopen`: deshace completar o descartar y devuelve la tarea **al estado en que
  estaba** (una de `waiting` vuelve a `waiting`, una del inbox vuelve al inbox).
- `DELETE /tasks/{id}`: descarta la tarea (`trashed`); no la borra, se puede reabrir. Idempotente.

### Proyectos

Un proyecto es un resultado que requiere más de una acción. Por defecto es **secuencial**
(`sequential: true`): solo su primera tarea abierta aparece en `/next`. Con `sequential: false` todas
sus tareas están disponibles a la vez. Todo proyecto trae `live_tasks` (siguientes o en espera, no
diferidas), `open_tasks` (esas más las de algún día) y `stalled`: `true` si está activo y no tiene
ninguna tarea viva, es decir, **necesita que decidas su siguiente acción**.

| Ruta | Qué hace |
|---|---|
| `POST /projects` | Crea un proyecto: `title` (obligatorio), `outcome`, `sequential`, `status` (`active`/`someday`) y `tasks` (primeras acciones). `201`. No es idempotente (usa `convert-to-project` si parte de una captura). |
| `GET /projects` | Lista; sin `status` trae activos y algún día. Filtros `status` (repetible), `q`, `limit`, `offset`. |
| `GET /projects/stalled` | Proyectos activos sin siguiente acción ni espera. Es la lista para preguntar "¿cuál es el próximo paso?". |
| `GET /projects/{id}` | Detalle con sus tareas abiertas en orden y `next_task` (la primera disponible). `?include_closed=true` añade hechas y descartadas. |
| `PATCH /projects/{id}` | `title`, `outcome`, `sequential`, `status` (`someday` pausa; `active` reactiva, también uno cerrado), `clear: ["outcome"]`. Al pasar a secuencial se ordenan las tareas por antigüedad; al pasar a paralelo se olvida el orden. |
| `POST /projects/{id}/tasks` | Añade hasta 50 tareas ya clarificadas: `{"tasks": [{"title": "...", "context": "@pc"}], "after_task_id": 12}`. En secuenciales van al final, o con `after_task_id` / `first: true`. Cada tarea admite `status` (`next`, `waiting`, `someday`) y los campos de ese estado. |
| `POST /projects/{id}/reorder` | Mueve una tarea: `{"task_id": 5, "first": true}` o `{"task_id": 5, "after_task_id": 9}`. Solo proyectos secuenciales (`409` si no). |
| `POST /projects/{id}/complete` | El resultado se logró. |
| `POST /projects/{id}/drop` | Se abandona. |
| `POST /tasks/{id}/convert-to-project` | Convierte una tarea (normalmente del inbox) en proyecto. |

**Idempotencia (para los reintentos de n8n).**
- `POST /projects/{id}/tasks` acepta `request_id` (por ejemplo `{{ $execution.id }}`): repetir la
  petición con la misma clave en el mismo proyecto responde `200` con las mismas tareas y
  `created: false`, sin duplicar.
- `convert-to-project` es idempotente por sí mismo: si la tarea ya era proyecto responde `200`
  con `already_converted: true` y el proyecto existente, sin cambiar nada.
- `complete` y `drop` también: repetirlos responde `200` con `already_closed: true`.

**Convertir una tarea en proyecto.** Cuerpo opcional: `title` (por defecto, el de la tarea; una
captura de más de 500 caracteres exige uno), `outcome`, `sequential`, `status` y `tasks` (las
primeras acciones). Todo ocurre en una sola transacción: la tarea original queda en estado
`converted` (conserva lo que escribiste) y el proyecto apunta a ella con `origin_task_id`. Si no
envías `tasks`, el proyecto nace `stalled`: el bot debe preguntar por la primera acción.

**Cerrar.** `complete` y `drop` aceptan `{"open_tasks": "refuse" | "complete" | "trash"}`. Por
defecto (`refuse`) responden `409` si quedan tareas abiertas, con cuántas son; `complete` las marca
hechas y `trash` las descarta (se pueden reabrir una a una y vuelven a su estado). Un proyecto
cerrado no admite tareas nuevas.

**Deshacer una conversión.** Si te equivocaste: `POST /projects/{id}/drop` con
`{"open_tasks": "trash"}` y luego `POST /tasks/{id}/reopen` sobre la tarea original, que vuelve
exactamente a su estado anterior. Mientras el proyecto viva, `reopen` responde `409`.

### Contextos

Los contextos (`@pc`, `@casa`, `@calle`...) se crean solos al usarlos en `/clarify` o `PATCH`; estas
rutas sirven para verlos y ordenarlos. Los nombres se normalizan (`PC` → `@pc`).

- `GET /contexts`: por nombre, con `next_tasks` (siguientes acciones con ese contexto) y
  `available_tasks` (las que se pueden hacer ahora).
- `POST /contexts` con `{"name": "@pc"}`: `201`, o `200` con `created: false` si ya existía.
- `PATCH /contexts/{id}` con `{"name": "@oficina"}`: renombra y las tareas lo conservan; `409` si
  ya existe otro con ese nombre.
- `DELETE /contexts/{id}`: `409` si lo usan tareas; con `?force=true` se lo quita a esas tareas
  (que no se tocan más) y responde cuántas fueron.

### Errores de concurrencia

Si dos peticiones simultáneas chocan en la base de datos, Postgres revierte una de ellas y la API
responde `503` con `Retry-After: 1` (o `409` si un dato que usaba acababa de borrarse), en lugar
de un error genérico. En n8n activa *Retry On Fail* en el nodo HTTP; es seguro reintentar porque
nada quedó a medias.

### Resumen diario y revisión semanal

- `GET /digest/daily`: lo que n8n envía cada mañana. Secciones: `inbox_count`, `overdue` (siguientes o
  esperas con fecha límite pasada), `due_today`, `reactivated` (estaban diferidas y hoy quedaron
  disponibles), `next_actions` (las primeras disponibles), `waiting_followups` (esperas sin respuesta
  hace `waiting_days` días, 7 por defecto) y `stalled_projects`. Cada sección trae `count` (el total) e
  `items` (hasta `limit`, 5 por defecto). "Hoy" es el día en la zona del usuario.
- `GET /digest/daily?format=text`: lo mismo como **texto plano** listo para el nodo de Telegram (sin
  Markdown ni HTML, así no depende del *parse mode*). Las secciones vacías no aparecen; si no hay nada,
  dice que no hay pendientes.
- `GET /review/weekly`: el material de la revisión, sin cambiar nada: inbox por vaciar, proyectos sin
  siguiente acción, vencidas, **todas** las esperas, la lista de "algún día", los números de la semana
  (`snapshot`) y cuándo fue la última revisión. También admite `?format=text`.
- `POST /review/weekly/complete`: registra que la terminaste, con los números de ese momento. Es
  idempotente por día en la zona del usuario: la primera vez responde `201`; si hoy ya hay una,
  `200` con `already_recorded: true`.
- `GET /reviews`: historial, de la más reciente a la más antigua.

Para programarlo (orientativo, no probado con n8n real), un nodo *Schedule Trigger* de n8n a la hora que quieras, un nodo HTTP a
`/digest/daily?format=text` y un nodo de Telegram con `{{ $json.data }}` como texto (según la versión
de n8n, la respuesta de texto llega en `data` o en `body`).

### Zona horaria del usuario

Las fechas las define el usuario: al recibir una fecha sin zona horaria (`due_at`, `defer_until`) se
lee en **su** zona, y todas las fechas de las respuestas se devuelven en ella, con su desfase
(`2030-05-01T09:00:00-05:00`). Una fecha que llega con desfase o `Z` se respeta como ese instante y
se devuelve convertida. Se usa la zona real (nombre IANA), así que el horario de verano se aplica
bien.

- `GET /users/me` → `{"timezone": "America/Lima"}`
- `PUT /users/me` con `{"timezone": "Europe/Paris"}` la cambia (`422` si el nombre no existe).

Internamente las fechas se guardan como instantes (`timestamptz`), así que cambiar de zona
no altera lo guardado, solo cómo se muestra.

## Desarrollo

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Para correr la API en local necesitas los mismos tres secretos (apunta `GTD_DB_PASSWORD_FILE`,
`GTD_API_KEY_FILE` y `GTD_ALLOWED_TELEGRAM_IDS_FILE` a archivos tuyos) y un Postgres accesible.

## Tests

Corren contra un Postgres real (la vista `available_tasks` y los índices parciales dependen de
Postgres). El compose de pruebas levanta uno desechable, sin contraseña y solo en `127.0.0.1`:

```bash
docker compose -f docker-compose.test.yml up -d
export GTD_TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/gtd_test
pytest
docker compose -f docker-compose.test.yml down
```

Los tests **borran el schema `public`** de la base indicada; por eso se niegan a correr si su
nombre no contiene `test`. No apuntes `GTD_TEST_DATABASE_URL` a tu base real. Usan una API key y
IDs de Telegram falsos.

## Modelo de datos

- `tasks` es una sola tabla con estado: `inbox`, `next`, `waiting`, `someday`, `reference`,
  `done`, `trashed`, `converted`. Clarificar es un `UPDATE`.
- `projects` puede ser `sequential`: sus tareas llevan `position` (siempre 1..N, sin huecos) y
  solo la primera pendiente aparece como disponible. Un proyecto paralelo no usa posiciones.
- Un proyecto que nace de una tarea guarda `origin_task_id`; la tarea queda `converted`.
- La vista `available_tasks` calcula lo que se puede hacer ahora (respeta secuencia,
  `defer_until` y proyectos que no están activos).
- `raw_text` guarda la captura original sin tocarla; `title` es la acción ya clarificada.
- `closed_from` guarda el estado en que estaba una tarea al completarla o descartarla, para que
  reabrirla la devuelva exactamente a donde estaba.
- Las fechas son instantes (`timestamptz`); la zona del usuario (`users.timezone`) decide cómo
  se leen y se muestran. La sesión de Postgres trabaja en UTC.

## Hoja de ruta

1. Estructura base y `/health` ✅
2. Modelos SQLAlchemy y migración inicial ✅
3. Autenticación (API key + allowlist de Telegram) y `POST /capture` ✅
4. Inbox y clarify ✅
5. Tareas: siguientes acciones, esperas, algún día, búsqueda, completar, reabrir, editar, descartar ✅
6. Proyectos (secuenciales, reordenar, convertir tarea en proyecto, estancados) y contextos ✅
7. Resumen diario y revisión semanal ✅
8. Snooze y recurrencia
