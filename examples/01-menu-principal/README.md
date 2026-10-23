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
- **Listas:** Qué hago ahora (`/next`), Proyectos activos, Algún día (proyectos y tareas),
  Esperando.
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

- `callback_data` ≤ 64 bytes: `c:next:ID`, `c:wait:ID`, `home`, `inbox`…
- Los botones se responden primero (`answerCallbackQuery`) y el mensaje se edita en el sitio para
  no llenar el chat.
- Los nodos `API: …` usan `neverError` + `onError: continueRegularOutput`: un fallo del backend se
  convierte en un mensaje amable, no en una ejecución rota.

## Qué se probó

Importado y ejecutado en n8n 2.44.6 con el backend real y un Telegram simulado: menú, captura,
todas las acciones de clarificación, respuestas con marcador y `@contexto`, listas, revisión
(incluido el segundo cierre del día), usuario no autorizado, grupos, mensajes sin texto, textos con
`< & _ *`. **No probado:** el Telegram Trigger con un bot real y los casos de backend caído.
