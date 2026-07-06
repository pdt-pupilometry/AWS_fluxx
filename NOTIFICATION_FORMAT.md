# Formato de la notificación y del archivo consolidado

Este documento detalla los dos formatos que produce la Lambda `notifier`
([functions/notifier/app.py](functions/notifier/app.py)) al terminar de
procesar un video:

1. El **POST** que llega a tu `ENDPOINT_URL` (metadata pequeña + un link).
2. El **archivo consolidado** (JSON o CSV) que ese link permite descargar,
   con las métricas de todos los frames.

## 1. El POST de notificación

```http
POST <ENDPOINT_URL> HTTP/1.1
Content-Type: application/json
x-api-key: <ENDPOINT_API_KEY>        # solo si está configurado en .env
```

```json
{
  "session_id": "sesion123",
  "eye": "left",
  "video_key": "s3://ocular-pipeline-videos-050071414246/sesion123_left.mp4",
  "fps": 30.003000300030003,
  "total_frames": 1358,
  "frames_failed": 0,
  "format": "json",
  "content_type": "application/json",
  "compressed": true,
  "download_url": "https://ocular-pipeline-frames-050071414246.s3.amazonaws.com/deliverables/<execution_name>/frames.json?X-Amz-Algorithm=...",
  "expires_at": "2026-07-06T01:09:14+00:00"
}
```

| Campo | Tipo | De dónde sale |
|---|---|---|
| `session_id` | string | Parseado del nombre del archivo subido (`{session_id}_{left\|right}.mp4`) |
| `eye` | `"left"` \| `"right"` | Idem |
| `video_key` | string | Ruta S3 del video original (`s3://bucket/key`) |
| `fps` | float | Calculado por OpenCV al abrir el video (Lambda 1) |
| `total_frames` | int | Cantidad de frames extraídos del video |
| `frames_failed` | int | Frames cuyo procesamiento falló a nivel de infraestructura (ejecución `FAILED` del Map) y fueron reconciliados con métricas en `0.0`. **Si es alto respecto de `total_frames`, la corrida está degradada** — los ceros de esos frames no son "sin detección", son "no se pudo procesar" |
| `format` | `"json"` \| `"csv"` | Parámetro `OutputFormat` del stack |
| `content_type` | `"application/json"` \| `"text/csv"` | Coincide con `format` |
| `compressed` | bool | Parámetro `GzipFile` — si es `true`, el archivo en `download_url` está gzip-eado |
| `download_url` | string | URL prefirmada de S3 (`GetObject`), vigente `PresignedUrlExpirationSeconds` (default 3600s) |
| `expires_at` | string ISO 8601 | Momento exacto en que `download_url` deja de servir |

**Nunca viaja la data de los frames en este body** — sea cual sea el tamaño
del video (100 o 100.000 frames), el POST siempre pesa lo mismo. Tu endpoint
tiene que hacer un `GET` a `download_url` para obtener el consolidado.

### Qué hacer con `download_url`

1. `GET download_url` (sin autenticación adicional, la firma va en la query string).
2. Si `compressed: true`, descomprimir con gzip antes de parsear.
3. Parsear como `format` indica (JSON array o CSV).

`testing/test_endpoint.py` hace exactamente estos 3 pasos automáticamente —
ver [testing/TEST_ENDPOINT.md](testing/TEST_ENDPOINT.md).

## 2. El archivo consolidado (contenido de `download_url`)

Es un único archivo (nunca fragmentado) con **un registro por frame**,
ordenado por orden temporal del video (aunque el campo de orden interno,
`frame_index`, no se incluye en el registro público).

### Formato JSON (`OutputFormat=json`, default)

Array de objetos:

```json
[
  {
    "session_id": "sesion123",
    "eye": "left",
    "timestamp": 0.0,
    "pupil_area_pixels": 452.31,
    "iris_area_pixels": 1810.77,
    "pupil_iris_ratio": 0.2498,
    "pupil_confidence": 0.91,
    "iris_confidence": 0.95
  },
  {
    "session_id": "sesion123",
    "eye": "left",
    "timestamp": 0.033,
    "pupil_area_pixels": 448.02,
    "iris_area_pixels": 1805.10,
    "pupil_iris_ratio": 0.2481,
    "pupil_confidence": 0.89,
    "iris_confidence": 0.94
  }
]
```

### Formato CSV (`OutputFormat=csv`)

Mismos campos, como encabezado + una fila por frame:

```csv
session_id,eye,timestamp,pupil_area_pixels,iris_area_pixels,pupil_iris_ratio,pupil_confidence,iris_confidence
sesion123,left,0.0,452.31,1810.77,0.2498,0.91,0.95
sesion123,left,0.033,448.02,1805.1,0.2481,0.89,0.94
```

### Campos de cada registro

| Campo | Tipo | Significado |
|---|---|---|
| `session_id` | string | Igual que en la notificación |
| `eye` | `"left"` \| `"right"` | Igual que en la notificación |
| `timestamp` | float (segundos) | Instante del frame dentro del video, derivado del nombre interno del frame (`f{idx}_t{ts_ms}.jpg`) |
| `pupil_area_pixels` | float | Área de la elipse ajustada a la máscara de pupila (`π·a·b`), en píxeles² |
| `iris_area_pixels` | float | Ídem para iris |
| `pupil_iris_ratio` | float | `pupil_area / iris_area`; **`0.0` si `iris_area` es `0`** (nunca división por cero) |
| `pupil_confidence` | float 0-1 | Confianza de la detección YOLO26 de pupila en ese frame |
| `iris_confidence` | float 0-1 | Ídem para iris |

**`frame_index`** existe internamente (para ordenar el consolidado) pero
**no se incluye** en el registro público — es un detalle de implementación,
no información del examen.

### Semántica de los valores en cero

Si un frame no tuvo detección de pupila o iris (falla de inferencia genuina,
o el frame vino de una ejecución `FAILED` del Distributed Map reconciliada
por la Lambda `notifier`), sus 5 métricas numéricas quedan en `0.0` — **el
registro igual aparece** en el consolidado, con `total_frames` registros
siempre presentes. Nunca falta un frame silenciosamente.

Para distinguir los dos casos a nivel corrida está `frames_failed` en la
notificación: los ceros por "sin detección" (ojo cerrado, frame borroso) no
cuentan ahí; los ceros por fallo de infraestructura sí. Un `frames_failed`
alto indica corrida degradada que conviene reprocesar (basta re-subir el
mismo video).

## Ver también

- [README.md](README.md) — flujo completo del pipeline.
- [testing/TEST_ENDPOINT.md](testing/TEST_ENDPOINT.md) — endpoint de prueba
  que recibe este POST, descarga y muestra el consolidado automáticamente.
