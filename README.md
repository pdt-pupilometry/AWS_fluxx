# Pipeline Serverless de Procesamiento de Videos Oculares (v2)

Procesa videos de exámenes oculares (`{session_id}_{left|right}.mp4`) a alta
velocidad: extrae todos los frames, ejecuta segmentación YOLO26 de pupila e
iris en hasta `MaxConcurrency` Lambdas en paralelo (default 1000), calcula
las áreas analíticas por ajuste de elipses y **notifica** a un endpoint
externo con un link de descarga al JSON/CSV consolidado — con todos los
frames garantizados, incluso los que fallaron a nivel de infraestructura.

**Pilares:** replicable con un solo comando (`./scripts/deploy.sh`), costo
fijo **$0** cuando no hay videos procesándose.

---

## Flujo completo, paso a paso (de punta a punta)

### Paso 0 — Preparación (una sola vez)

1. Exportás tu modelo entrenado `.pt` a ONNX (`scripts/export_model.py`) →
   queda en `functions/inference/model/yolo26_seg.onnx`. **El `.pt` nunca se
   sube a AWS**, solo se usa localmente para generar el `.onnx`.
2. Completás `.env` (copiado de `.env.example`) con tus credenciales de AWS y
   la URL del endpoint que recibirá las notificaciones.
3. Corrés `./scripts/deploy.sh`, que construye las imágenes Docker (Lambda 1
   y 2), las publica en ECR y crea/actualiza todo el stack de CloudFormation
   (buckets, Lambdas, Step Functions, EventBridge, IAM).

### Paso 1 — Ingesta del video

Subís `sesion123_left.mp4` al bucket `{stack}-videos-{account}`. Ese bucket
tiene EventBridge habilitado: cualquier `Object Created` con sufijo `.mp4`
dispara automáticamente **una ejecución** del state machine de Step
Functions (1 ejecución = 1 video).

### Paso 2 — Lambda 1: extracción y pre-procesamiento

La primera Lambda del flujo (`functions/frame_extractor/app.py`):
1. Descarga el video a `/tmp` y parsea `session_id`/`eye` del nombre del
   archivo (`rsplit('_', 1)`).
2. Abre el video con OpenCV (`cv2.VideoCapture`) y, por cada frame: lo pasa a
   escala de grises, lo redimensiona a 480×640, lo codifica como JPEG (q90) y
   lo sube al bucket `{stack}-frames-{account}` — todo en memoria, sin tocar
   disco, con hasta 16 subidas en paralelo.
3. Cada frame queda en
   `frames/{execution_name}/f{idx:06d}_t{ts_ms}.jpg` (el nombre de la
   ejecución de Step Functions, no el `session_id` a secas, evita mezclar
   frames si se reprocesa el mismo video).
4. Devuelve `{session_id, eye, fps, total_frames, frames_bucket,
   frames_prefix, execution_name, source_video}` — este objeto (`$.job`)
   viaja dentro del JSON de Step Functions hasta el final del flujo.

### Paso 3 — Distributed Map: inferencia masiva en paralelo

Step Functions lista los frames **directo desde S3** (`ItemReader:
s3:listObjectsV2`, nunca carga miles de nombres inline) y lanza hasta
`MaxConcurrency` (default 1000) invocaciones simultáneas de la Lambda 2.
Cuando una termina y libera su slot, Lambda casi siempre reutiliza ese mismo
contenedor (ya "tibio", con el modelo ONNX en memoria) para el siguiente
frame en cola — comportamiento propio del servicio Lambda, no algo
controlado por el código.

Cada invocación de la Lambda 2 (`functions/inference/app.py` +
`functions/inference/yolo_onnx.py`):
1. Descarga su frame desde S3.
2. Corre YOLO26-seg en ONNX Runtime (CPU, arm64): letterbox 640×640, decodifica
   la salida (soporta tanto el formato E2E `(1,300,38)` como el clásico
   `(1,38,8400)`), y con un simple argmax por clase obtiene la mejor detección
   de pupila (clase 0) y de iris (clase 1) — no hace falta NMS completo.
3. Reconstruye la máscara de cada detección, ajusta la elipse de mejor calce
   (`cv2.findContours` → `cv2.fitEllipse`) y calcula el área analítica
   `π·a·b`.
4. **Nunca deja que una excepción aborte la invocación**: cualquier fallo de
   decode/inferencia/geometría en ese frame puntual devuelve un registro con
   las 5 métricas en 0, no propaga el error.
5. `return [...]` — no escribe a ningún lado, no sabe que existen las otras
   invocaciones. **No hay DynamoDB ni tabla compartida.**

Cuando el Map completo termina (`ToleratedFailurePercentage: 100`: el flujo
**siempre** continúa, sin importar cuántas ejecuciones hijas fallen), su
`ResultWriter` junta automáticamente todos esos `return` en archivos S3:
`manifest.json` + uno o más `SUCCEEDED_*.json` + `FAILED_*.json` (si hubo
fallos de infraestructura genuinos: timeout, OOM, throttling agotado).

### Paso 4 — Lambda 3: agregación, reconciliación y entrega

La última Lambda (`functions/notifier/app.py`):
1. Lee `manifest.json`, junta **todos** los `SUCCEEDED_*.json` (parseando el
   campo `Output` de cada entrada) y **reconcilia** los `FAILED_*.json`
   (parseando el `Input` original de cada ejecución fallida para reconstruir,
   por cada frame afectado, un registro con métricas en 0). Resultado: el
   total de registros siempre coincide con `total_frames` de la Lambda 1 —
   **ningún frame se pierde silenciosamente**.
2. Ordena los registros por `frame_index` y arma el formato público exacto
   (`session_id`, `eye`, `timestamp`, `pupil_area_pixels`,
   `iris_area_pixels`, `pupil_iris_ratio`, `pupil_confidence`,
   `iris_confidence` — sin `frame_index`, que es solo uso interno para
   ordenar).
3. **Serializa TODO el consolidado como un único archivo** JSON o CSV
   (parámetro `OutputFormat`), opcionalmente comprimido con gzip, y lo sube a
   `s3://{stack}-frames-{account}/deliverables/{execution_name}/frames.json`
   (o `.csv`).
4. Genera una **URL prefirmada** de descarga (S3 `GetObject`, vigente por
   `PresignedUrlExpirationSeconds`, default 1h) y envía al endpoint externo
   una notificación **pequeña** (metadata + esa URL) — nunca la data de los
   frames en el body del POST.

### Paso 5 — El endpoint recibe la notificación y descarga el archivo

El POST que llega al `EndpointUrl` tiene esta forma (independiente de si el
video tiene 100 o 100.000 frames — el body siempre es chico):

```json
{
  "session_id": "sesion123",
  "eye": "left",
  "video_key": "s3://stack-videos-123/sesion123_left.mp4",
  "fps": 30.0,
  "total_frames": 1800,
  "format": "json",
  "content_type": "application/json",
  "compressed": true,
  "download_url": "https://stack-frames-123.s3.amazonaws.com/deliverables/.../frames.json?X-Amz-...",
  "expires_at": "2026-07-05T18:30:00+00:00"
}
```

El endpoint hace un `GET` a `download_url` (descomprimiendo si
`compressed: true`) para obtener el array/CSV completo de los `total_frames`
registros. **Por qué un link y no la data inline**: con miles de frames por
video, embeber todo en el body arriesga timeouts, límites de tamaño de
request del lado del endpoint, y payloads de varios MB por POST — subir un
único archivo a S3 y enlazarlo desacopla completamente el tamaño de los datos
del tamaño de la notificación HTTP.

### Fin del flujo

Los objetos en `frames/`, `results/` y `deliverables/` tienen lifecycle rules
(`FramesTTLDays`=1, `ResultsTTLDays`=7 por defecto) — se autodestruyen solos,
no se necesitan más una vez que el endpoint descargó el archivo.

---

## Diagrama de arquitectura

```
video .mp4 → S3 (videos) → EventBridge (Object Created, suffix .mp4)
    → Step Functions STANDARD (1 ejecución = 1 video)
        ├─ 1. ExtractFrames (Lambda 1, imagen Docker ARM64, OpenCV)
        │     todos los frames → gris → 480×640 → JPEG q90 → s3://frames/frames/{exec}/f{idx}_t{ms}.jpg
        │     retorna $.job = {session_id, eye, fps, total_frames, frames_bucket, frames_prefix, execution_name}
        ├─ 2. SegmentFrames (Distributed Map, MaxConcurrency=1000, hijos EXPRESS)
        │     ItemReader: S3 ListObjectsV2 sobre frames_prefix
        │     ItemBatching: MaxItemsPerBatch=1 (parámetro) + BatchInput con metadata del job
        │     ItemProcessor: Lambda 2 (ONNX Runtime YOLO26-seg + fitEllipse) → métricas por frame
        │     ResultWriter: s3://frames/results/{exec}/ (manifest.json + SUCCEEDED_*.json + FAILED_*.json)
        └─ 3. AggregateAndNotify (Lambda 3, zip ARM64)
              lee SUCCEEDED_*.json + reconcilia FAILED_*.json (frames con métricas en 0)
              → ordena por frame_index → sube UN archivo JSON/CSV a s3://frames/deliverables/{exec}/
              → genera URL prefirmada → POST pequeño (metadata + link) al endpoint
```

### Consistencia de datos (sin DynamoDB)

No hay ninguna base de datos intermedia. El estado del job viaja en el JSON
de Step Functions (`$.job`); los resultados por frame los junta
automáticamente el `ResultWriter` del Distributed Map. La Lambda 2 nunca
escribe a ningún lado — solo `return`.
- **Todos los frames llegan al endpoint**: incluso los de ejecuciones
  `FAILED` del Map se reconstruyen con métricas en 0.
- **Sin división por cero**: `pupil_iris_ratio = pupil_area/iris_area` solo
  si `iris_area > 0`, si no `0.0`.
- **Sin payloads gigantes**: la data viaja como un archivo en S3 con link
  prefirmado, no en el body del POST.

### ¿Por qué es de bajo costo?

| Decisión | Ahorro |
|---|---|
| 100% serverless (S3 + Lambda + Step Functions) | **$0 de costo fijo** sin videos |
| Lambdas en **arm64/Graviton** | ~20% menos por GB-segundo |
| Ejecuciones hijas **Express** en el Map | Cobro por duración, no por transición |
| `ItemReader`/`ResultWriter` sobre S3 | Evita pagar transiciones y payloads gigantes |
| **ONNX Runtime** en vez de PyTorch/Ultralytics | Imagen ~400MB vs ~2GB, cold start ~2-3s vs 10s+ |
| Un solo archivo + link prefirmado (no POST con la data) | Un único PUT/GET a S3 por video, sin importar cuántos frames |
| Lifecycle S3: `frames/` 1 día, `results/`+`deliverables/` 7 días | Storage temporal tiende a $0 |
| Modelo ONNX horneado en la imagen | Sin tráfico S3 por cold start |
| Sin DynamoDB | Cero escrituras por frame (v1 hacía 1800+1 por video) |

Costo aproximado por video de ~1800 frames (60s @ 30fps): **~$0.025-0.045**
(dominado por las ~1800 invocaciones de la Lambda 2). Cuota de concurrencia
Lambda: el aumento a 1000 ya fue solicitado por el usuario en su cuenta; quien
replique el proyecto en otra cuenta debe solicitar lo mismo en Service Quotas.

---

## Infraestructura como código

[`template.yaml`](template.yaml) — AWS SAM: 2 buckets S3, 3 Lambdas ARM64
(Lambda 1 y 2 como imagen Docker, Lambda 3 como zip), el state machine
([`statemachine/pipeline.asl.json`](statemachine/pipeline.asl.json)) con el
Distributed Map, la regla de EventBridge y los roles IAM mínimos por función.
Todo lo que cambia entre cuentas/entornos es un `Parameter` (`EndpointUrl`,
`MaxConcurrency`, `MaxItemsPerBatch`, `OutputFormat`, `GzipFile`,
`PresignedUrlExpirationSeconds`, memorias, TTLs, etc.).

## Mapa del código

```
functions/
├── frame_extractor/app.py   # Lambda 1: OpenCV VideoCapture → gris → resize → S3 (ThreadPool)
├── inference/
│   ├── app.py                # Lambda 2: handler, parsea frame_key, try/except por frame
│   └── yolo_onnx.py          # sesión ONNX (lazy), letterbox, decode E2E/clásico, máscara,
│                              #   findContours→fitEllipse→área, compute_pupil_iris_ratio
└── notifier/app.py           # Lambda 3: lee SUCCEEDED+FAILED, reconcilia, sube archivo
                               #   JSON/CSV, genera URL prefirmada, notifica al endpoint
```

## Despliegue (un solo comando, credenciales desde `.env`)

Requisitos: AWS CLI, SAM CLI y Docker corriendo localmente.

```bash
# 1. Exporta tu modelo .pt entrenado a ONNX
python scripts/export_model.py --weights ~/Downloads/best.pt \
    --out functions/inference/model/yolo26_seg.onnx

# 2. Configura tus credenciales y variables
cp .env.example .env
# edita .env: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION, ENDPOINT_URL...

# 3. Build + push de las imágenes Docker + deploy/actualización del stack
./scripts/deploy.sh
#   (opcional: ./scripts/deploy.sh mi-stack-alternativo)

# 4. Procesa un video
aws s3 cp sesion123_left.mp4 s3://<stack>-videos-<account-id>/
```

`scripts/deploy.sh` carga las credenciales de `.env` (sin necesitar `aws
configure` ni perfiles del CLI), valida que el modelo `.onnx` exista, corre
`sam build` (compila las imágenes Docker ARM64 y empaqueta la Lambda zip) y
`sam deploy` (publica las imágenes en ECR y crea/actualiza toda la stack).
Para replicar en otra cuenta AWS basta con otro `.env` — los nombres de
bucket incluyen el Account ID, así que no hay colisiones entre cuentas.

## Estrategias de optimización

### Protección del endpoint externo
- El endpoint nunca recibe la data de los frames en el body: solo una
  notificación pequeña con metadata + un link de descarga a S3. El tamaño del
  POST es constante sin importar si el video tiene 100 o 100.000 frames.
- `ReservedConcurrentExecutions` (default 5) en la Lambda 3 evita que muchos
  videos simultáneos saturen el endpoint con notificaciones.
- `requests.Session` + `Retry` de `urllib3` (5 intentos, backoff exponencial,
  reintenta 429/5xx, respeta `Retry-After`, `timeout=(5,30)`) para el POST de
  notificación.
- El archivo consolidado se puede pedir en **CSV** (`OutputFormat=csv`) si el
  consumidor prefiere cargarlo directo a una hoja de cálculo o a un `COPY`/
  `LOAD DATA` de base de datos, en vez de parsear JSON.

### ARM64 / Graviton
- `Architectures: [arm64]` global en el template.
- OpenCV headless, NumPy, ONNX Runtime y `requests` tienen wheels `aarch64`
  nativas; las imágenes Docker usan `public.ecr.aws/lambda/python:3.12-arm64`.

### Otras optimizaciones
- **Sesión ONNX perezosa y global**: se carga una vez por contenedor, en el
  primer frame que procesa.
- **Subidas S3 en paralelo** (ThreadPool de 16) en la Lambda 1.
- **Sin NMS completo**: solo se necesita la mejor detección de pupila y de
  iris por frame — un argmax por clase reemplaza al NMS.
- **Archivo entregable comprimido con gzip** (`GzipFile=true` por defecto):
  reduce tanto el costo de storage en S3 como el tiempo de descarga del
  endpoint.

### Nota sobre la URL prefirmada

Una URL prefirmada generada por una Lambda **nunca dura más que las
credenciales temporales (STS) del rol de ejecución**, sin importar el valor
de `PresignedUrlExpirationSeconds` — por eso el default es conservador (1h).
El endpoint debería descargar el archivo apenas recibe la notificación, no
tratar el link como permanente.

---

## Verificación

```bash
# 1. Validar el template SAM (si tenés SAM CLI instalado)
sam validate --lint

# 2. Compilar los módulos Python
python -m py_compile functions/frame_extractor/app.py \
    functions/inference/app.py functions/inference/yolo_onnx.py \
    functions/notifier/app.py

# 3. Correr los tests (no requieren AWS ni el modelo real)
pip install pytest opencv-python-headless numpy onnxruntime requests boto3
python -m pytest tests/ -v
```

Prueba E2E completa (requiere cuenta AWS + tu modelo `.onnx` ya colocado):
`./scripts/deploy.sh` → subir `sesion123_left.mp4` al bucket de videos → ver
la ejecución en la consola de Step Functions → verificar la notificación
recibida en el endpoint (o en [webhook.site](https://webhook.site) para
pruebas rápidas) y que la `download_url` efectivamente sirva el archivo.
