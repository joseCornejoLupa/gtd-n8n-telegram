# Ejemplo 1 — Menú principal

Es la «pantalla de inicio» del bot. Cualquier comando (`/start`, `/menu`…) muestra:

```
🏠 GTD — ¿qué quieres hacer?

📥 Inbox: 3    ▶️ Acciones: 5
⏳ Esperando: 1    🌱 Algún día: 4

✍️ Escríbeme cualquier cosa y la capturo al inbox.
```

con botones: ➕ Añadir al inbox · 📥 Procesar inbox · ▶️ Qué hago ahora · 🧩 Proyectos ·
🌱 Algún día · ⏳ Esperando · 🗓 Revisión semanal.

## Comportamiento

- **Captura sin fricción:** cualquier texto suelto va al inbox (`POST /capture`), sin preguntar.
- **Procesar inbox:** muestra un ítem a la vez con ▶️ Siguiente acción · ⏳ Esperando ·
  🌱 Algún día · 📚 Referencia · 🧩 Es un proyecto · 🗑 Descartar. Si hace falta un dato, pregunta
  con una respuesta (ForceReply): la acción concreta (puedes añadir `@contexto` al final), a quién
  esperas, o la primera acción del proyecto. Al terminar uno, pasa al siguiente.
- **Listas con acciones:** «Qué hago ahora», «Esperando» y «Algún día» (tareas; los proyectos
  se muestran como texto) salen en páginas de 5 con un botón por número. Tocar un número abre
  la tarea:
  - *Siguiente acción:* ✅ Hecha · 💤 Mañana / 3 días / 1 semana · ⏳ Esperando (pregunta a quién
    esperas) · 🗑 Descartar.
  - *Esperando:* ✅ Ya llegó · 🗑 Descartar.
  - *Algún día:* ▶️ Pasar a acciones · 🗑 Descartar.
- **Deshacer:** cada acción confirma lo hecho y ofrece ↩️ Deshacer (reabre, quita el
  aplazamiento o devuelve la tarea a su lista). Al completar, avisa si en un proyecto secuencial
  ya toca otro paso, o si el proyecto se quedó sin tareas.
- **Proyectos activos:** solo lista (sin botones todavía).
- **Revisión semanal:** muestra el texto de `/review/weekly?format=text` y un botón para
  registrarla (idempotente por día).
- **Seguridad:** solo chats privados; si el backend responde 403 (usuario fuera de la allowlist) el
  flujo no contesta; 401 muestra un aviso de configuración.

## Instalar

1. En n8n: *Import from file* → `workflow.json`.
2. Credenciales:
   - **Telegram bot** (tipo Telegram API): el token de tu bot. Asígnala a los nodos de Telegram.
   - **GTD API key** (tipo Header Auth): nombre `X-API-Key`, valor el contenido de
     `secrets/api_key.txt`. Asígnala a los nodos `API: …`.
3. Nodo **Config**: cambia `api_url` por la URL del backend vista desde n8n (ver el README
   principal, paso 6).
4. Activa el flujo. El Telegram Trigger registra el webhook; n8n necesita una URL pública HTTPS
   (`WEBHOOK_URL`).

## Notas de diseño

- `callback_data` ≤ 64 bytes: `c:next:ID` (inbox), `lp:n:PÁGINA` (página de lista), `v:ID:n` (abrir),
  `a:done:ID:n` (acción; `n`/`w`/`s` = lista de origen), `noop`…
- Los teclados de Telegram en n8n son fijos, así que hay un nodo por cantidad de botones (1–5, con
  y sin fila de navegación).
- Los botones se responden primero (`answerCallbackQuery`) y el mensaje se edita en el sitio para
  no llenar el chat.
- Los nodos `API: …` usan `neverError` + `onError: continueRegularOutput`: un fallo del backend se
  convierte en un mensaje amable, no en una ejecución rota.

## Qué se probó

Importado y ejecutado en n8n 2.44.6 con el backend real y un Telegram simulado: menú, captura,
todas las acciones de clarificación, respuestas con marcador y `@contexto`, listas paginadas,
completar / posponer / esperar / descartar / activar y deshacerlos, proyecto secuencial, revisión
(incluido el segundo cierre del día), usuario no autorizado, grupos, mensajes sin texto, textos con
`< & _ *`. **No probado:** el Telegram Trigger con un bot real y los casos de backend caído.

## Límites conocidos

- Cerrar un proyecto terminado, editar el texto de una tarea o poner fechas no tiene botón todavía.
- Al volver de una tarea se regresa a la primera página de la lista.
- Se cargan hasta 200 tareas por lista; si tienes más, el aviso «… y N más» sale en la última página.
