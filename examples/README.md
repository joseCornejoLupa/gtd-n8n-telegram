# Ejemplos de flujos de n8n

Flujos listos para importar que usan este backend como API. El bot de Telegram vive solo en n8n:
**el token del bot nunca pasa por este repo ni por el backend.**

| Ejemplo | Qué hace |
|---|---|
| [01-menu-principal](01-menu-principal/) | Menú de inicio con contadores, captura sin fricción, procesado del inbox, listas, proyectos, esperando, algún día y revisión semanal. |

## Reglas de todos los ejemplos

- **Sin secretos ni direcciones.** Los JSON usan credenciales de n8n por nombre (`GTD API key`,
  `Telegram bot`) con id `REEMPLAZAR`, y una URL de marcador (`http://URL-DE-TU-API-GTD:8000`) en
  el nodo `Config`. Al importar, reasigna las credenciales y cambia la URL.
- **Texto plano.** Los mensajes no llevan Markdown ni HTML escrito a mano; el flujo escapa
  `& < >` una sola vez y fija `parse_mode: HTML` en cada nodo de Telegram (el nodo de Telegram de
  n8n impone Markdown si no se indica otro modo, y rompería textos con `_` o `*`).
- **Sin estado en n8n.** Las preguntas de seguimiento usan *ForceReply* y llevan un marcador en la
  última línea del mensaje del bot (`[#45 next]`); al responder se lee ese marcador.

## Qué se probó y qué no

Se importó y ejecutó el flujo en un n8n real (2.44.6) contra este backend real y un Telegram
simulado: se enviaron mensajes y pulsaciones de botón y se comprobó lo que el flujo manda a
Telegram. **No se probó la conexión con un bot real** (Telegram Trigger y webhook público): es lo
primero que debes verificar tú.
