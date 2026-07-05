# Endpoint de prueba local (ngrok)

Para probar el pipeline de punta a punta sin depender de un backend real,
`testing/test_endpoint.py` levanta un servidor HTTP local, lo expone a
internet con [ngrok](https://ngrok.com) y actúa como el `ENDPOINT_URL` que
recibe la notificación final de la Lambda `notifier`. Cuando llega la
notificación:

1. Imprime el payload completo (`session_id`, `eye`, `download_url`, etc.).
2. Descarga automáticamente el archivo desde `download_url`.
3. Si `compressed: true`, lo descomprime (gzip).
4. Lo guarda en disco y muestra un resumen (cantidad de registros, tamaño).

No depende de ninguna librería externa de Python — solo usa la librería
estándar (`http.server`, `urllib`) y el binario de `ngrok`.

## Requisitos

- **ngrok** instalado y autenticado una vez con un token gratuito:

  ```bash
  brew install ngrok
  ngrok config add-authtoken <tu-token>
  ```

  El token se obtiene creando una cuenta gratuita en
  [dashboard.ngrok.com](https://dashboard.ngrok.com).

- Python 3 (ya lo necesitas para `scripts/export_model.py`).

## Uso

```bash
python3 testing/test_endpoint.py
#   opcional: --port 9000 --download-dir ./mis_descargas
```

Al arrancar imprime algo así:

```
Servidor local escuchando en http://127.0.0.1:8787
======================================================================
Tunel ngrok listo:  https://a1b2c3d4.ngrok-free.app

Actualiza ENDPOINT_URL en tu .env con esta URL y despliega de nuevo:
  ENDPOINT_URL=https://a1b2c3d4.ngrok-free.app
  ./scripts/deploy.sh

(Esto solo actualiza un parametro de la Lambda notifier, no toca
 los buckets S3 -- es un update rapido, sin rebuild de imagenes.)
======================================================================

Esperando notificaciones... (Ctrl+C para salir)
```

## Apuntar el pipeline al endpoint de prueba

1. Copia la URL de ngrok que imprime el script.
2. Edita `ENDPOINT_URL` en tu `.env` con esa URL.
3. Corre `./scripts/deploy.sh` de nuevo.

Este cambio **no toca los buckets S3** ni las imágenes Docker — es solo una
actualización de la variable de entorno `ENDPOINT_URL` en la Lambda
`notifier`, así que `sam deploy` termina en segundos.

4. Sube un video de prueba: `aws s3 cp sesion123_left.mp4 s3://<stack>-videos-<account-id>/`.
5. Cuando el pipeline termine, la notificación llega al script y ves algo así:

```
======================================================================
[2026-07-05T19:30:12] POST /
{
  "session_id": "sesion123",
  "eye": "left",
  "video_key": "s3://ocular-pipeline-videos-.../sesion123_left.mp4",
  "fps": 30.0,
  "total_frames": 1800,
  "format": "json",
  "content_type": "application/json",
  "compressed": true,
  "download_url": "https://ocular-pipeline-frames-....s3.amazonaws.com/deliverables/.../frames.json?X-Amz-...",
  "expires_at": "2026-07-05T20:30:12+00:00"
}
Descargando: https://ocular-pipeline-frames-....s3.amazonaws.com/...
Guardado en: test_downloads/sesion123_left_20260705_193013.json (842013 bytes)
Registros: 1800
Primer registro: {"session_id": "sesion123", "eye": "left", "timestamp": 0.0, ...}
======================================================================
```

## Notas

- **La URL de ngrok cambia cada vez que reinicias el script** (a menos que
  tengas un dominio reservado en tu cuenta de ngrok) — si lo reinicias,
  actualiza `ENDPOINT_URL` de nuevo y despliega.
- **No es para producción**: mientras el túnel está arriba, cualquiera con la
  URL puede hacer POST a tu máquina. Úsalo solo durante la prueba y ciérralo
  con `Ctrl+C` cuando termines.
- Los archivos descargados quedan en `./test_downloads/` (o el directorio que
  pases con `--download-dir`) — no se suben a git.
- Si `ENDPOINT_API_KEY` está configurado en tu `.env`, el script no lo
  valida (acepta cualquier request) — solo es un receptor de prueba, no
  reemplaza la autenticación real de tu backend.
