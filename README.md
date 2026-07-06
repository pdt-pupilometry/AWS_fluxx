# Pipeline Serverless de Procesamiento de Videos Oculares (v2)

Procesa videos de exámenes oculares (`{session_id}_{left|right}.mp4`) a alta
velocidad: extrae todos los frames, ejecuta segmentación YOLO26 de pupila e
iris en hasta `MaxConcurrency` Lambdas en paralelo (default 1000), calcula
las áreas analíticas por ajuste de elipses y **notifica** a un endpoint
externo con un link de descarga al JSON/CSV consolidado — con todos los
frames garantizados, incluso los que fallaron a nivel de infraestructura.

**Pilares:** replicable con un solo comando (`./scripts/deploy.sh`), costo
fijo **$0** cuando no hay videos procesándose.

> Historia completa de la puesta en marcha (todos los errores reales de
> permisos, cuotas y runtime que aparecieron y cómo se resolvieron):
> **[BITACORA.md](BITACORA.md)**.

---

## Herramientas necesarias

> **Nota sobre el sistema operativo**: este README (comandos de instalación,
> `brew install ...`, rutas, etc.) está escrito y probado en **macOS**. Los
> binarios y el flujo de `deploy.sh` funcionan igual en Linux/Windows, pero
> hay que adaptar la forma de instalar cada herramienta (por ejemplo `apt`/
> `dnf`/`choco`/descarga manual en vez de `brew`) y algunos detalles
> específicos de macOS (Docker Desktop, `python3.12` vía Homebrew, etc.).

| Herramienta | Para qué se usa | Instalación (macOS) |
|---|---|---|
| **AWS CLI** (`aws`) | `deploy.sh` la usa para validar credenciales (`sts get-caller-identity`); también sirve para subir videos (`aws s3 cp`) y diagnosticar stacks/permisos a mano | `brew install awscli` |
| **AWS SAM CLI** (`sam`) | Compila las imágenes Docker y crea/actualiza todo el stack de CloudFormation (`sam build` + `sam deploy`), usado por `deploy.sh` | `brew install aws-sam-cli` |
| **Docker** | Build de las imágenes ARM64 de las Lambdas 1 y 2 (`frame_extractor`, `inference`) — tiene que estar **corriendo** (Docker Desktop) al ejecutar `deploy.sh` | `brew install --cask docker` |
| **Python 3.12** | Correr los tests, `testing/local_test_inference.py`, `testing/test_endpoint.py` y `scripts/export_model.py`. Debe ser 3.12 —la misma versión que el runtime de Lambda— porque `numpy`/`onnxruntime` fijados en `requirements.txt` no tienen wheels para 3.13+ | `brew install python@3.12` |
| **ngrok** | Solo si vas a usar `testing/test_endpoint.py` (endpoint de prueba local con notificación real) — no hace falta para el resto del pipeline | `brew install ngrok` + `ngrok config add-authtoken <token>` ([dashboard.ngrok.com](https://dashboard.ngrok.com), cuenta gratis) |
| **Homebrew** | Gestor de paquetes de macOS, la forma más simple de instalar todo lo anterior | [brew.sh](https://brew.sh) |

Además necesitas una **cuenta de AWS** con un usuario/rol y sus credenciales
(`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` en `.env`) con los permisos
descritos en [`IAM_PERMISSIONS.md`](IAM_PERMISSIONS.md).

## Flujo completo, paso a paso (de punta a punta)

### Paso 0 — Preparación (una sola vez)

1. Exportas tu modelo entrenado `.pt` a ONNX (`scripts/export_model.py`) →
   queda en `functions/inference/model/yolo26_seg.onnx`. **El `.pt` nunca se
   sube a AWS**, solo se usa localmente para generar el `.onnx`.
2. Completas `.env` (copiado de `.env.example`) con tus credenciales de AWS y
   la URL del endpoint que recibirá las notificaciones.
3. Ejecutas `./scripts/deploy.sh`, que construye las imágenes Docker (Lambda 1
   y 2), las publica en ECR y crea/actualiza todo el stack de CloudFormation
   (buckets, Lambdas, Step Functions, EventBridge, IAM).

### Paso 1 — Ingesta del video

Subes `sesion123_left.mp4` al bucket `{stack}-videos-{account}`. Ese bucket
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

El POST que llega al `EndpointUrl` es chico (metadata + un link), sin importar
si el video tiene 100 o 100.000 frames. El endpoint hace un `GET` a
`download_url` (descomprimiendo si `compressed: true`) para obtener el
array/CSV completo. **Por qué un link y no la data inline**: con miles de
frames por video, embeber todo en el body arriesga timeouts, límites de
tamaño de request del lado del endpoint, y payloads de varios MB por POST —
subir un único archivo a S3 y enlazarlo desacopla completamente el tamaño de
los datos del tamaño de la notificación HTTP.

Detalle completo del POST y del archivo consolidado (JSON y CSV, campo por
campo, con ejemplos reales): **[`NOTIFICATION_FORMAT.md`](NOTIFICATION_FORMAT.md)**.

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

**Importante — bloquea el primer deploy**: cuentas nuevas de AWS suelen
arrancar con el límite de "Concurrent executions" de Lambda en **10** (el
default estándar de AWS es 1000). Con el límite en 10, el primer `sam
deploy` falla al crear `NotifierFunction` con
`ReservedConcurrentExecutions decreases account's UnreservedConcurrentExecution
below its minimum value of [10]` — AWS exige que el pool sin reservar nunca
baje de 10, así que con límite total 10 ninguna función puede reservar
concurrencia (ni siquiera 1). Hay que pedir el aumento **antes** de desplegar:

```bash
aws service-quotas request-service-quota-increase \
  --service-code lambda --quota-code L-B99A9384 \
  --desired-value 1000 --region <tu-region>
```

Revisar el estado con:

```bash
aws service-quotas get-service-quota \
  --service-code lambda --quota-code L-B99A9384 --region <tu-region>
```

La aprobación suele tardar minutos, a veces hasta 1-2 días hábiles. Recién
cuando el valor aprobado supere `NotifierReservedConcurrency` (5 por
defecto) por al menos 10, `sam deploy` puede terminar de crear el stack.

### Rendimiento medido y parámetros recomendados

Mediciones reales del mismo video ocular (2477 frames, ~82s @ 30fps, 5MB),
variando solo parámetros — sin tocar código (ver [BITACORA.md](BITACORA.md)
por el contexto completo):

| Configuración | ExtractFrames | SegmentFrames (Map) | Total pipeline |
|---|---|---|---|
| `2048MB`, `batch=1` (defaults del template) | 20.3s | 115.9s | ~139s |
| `EXTRACTOR_MEMORY=3008`, `batch=8` | 14.1s | 50.6s | ~67s |
| Ambas Lambdas `3008MB`, `batch=8` | 13.0s | **39.6s** | **~55s (-60%)** |

Por qué funcionan estas dos palancas:
- **Memoria = CPU en Lambda**: la vCPU asignada escala con la memoria. Más
  memoria acelera el decode de OpenCV (Lambda 1) y la inferencia ONNX
  (Lambda 2). El costo por GB-segundo sube, pero la duración baja casi en la
  misma proporción — costo neto casi neutro, latencia mucho menor.
- **`MAX_ITEMS_PER_BATCH=8`**: con batch=1, cada frame paga el overhead
  completo de una ejecución Express + invocación Lambda + (si toca) cold
  start con carga del modelo. Con 8 frames por invocación ese overhead se
  amortiza 8×. No conviene subirlo mucho más: el timeout de la Lambda 2 es
  60s y cada ejecución fallida arrastraría más frames a la reconciliación.

**Proyección para videos largos (20 min, ~36.000 frames @ 30fps)** con la
configuración recomendada: extracción ~3-4 min (throughput medido ~190 fps,
dentro del timeout de 900s) + Map ~2 min (4500 ejecuciones hijas en ~4.5
olas de 1000) ≈ **~5-6 minutos por video**. Si la extracción se convierte en
la pared para videos aún más largos, la palanca siguiente es pedir en
Service Quotas el límite de memoria de Lambda a 10240MB (cuentas nuevas
vienen capadas a **3008MB**) o particionar la extracción en chunks paralelos
(cambio de arquitectura, documentado como trabajo futuro).

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

testing/                      # Todo lo de testeo, separado del código de producción
├── test_geometry.py                    # unit tests: geometría (findContours→fitEllipse)
├── test_aggregator_reconciliation.py   # unit tests: reconciliación + notificación (Lambda 3)
├── local_test_inference.py             # prueba el .onnx contra una imagen real, sin AWS
├── test_endpoint.py                    # servidor local + ngrok para recibir notificaciones
└── TEST_ENDPOINT.md                    # doc del endpoint de prueba
```

## Despliegue (un solo comando, credenciales desde `.env`)

Requisitos: ver [Herramientas necesarias](#herramientas-necesarias) (AWS CLI,
SAM CLI y Docker corriendo localmente).

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

**Nota — error `409` al crear los buckets (nombre global en liberación)**: si
un deploy falla y CloudFormation hace rollback, **borra los buckets S3** que
alcanzó a crear. Los nombres de bucket son **globales** (`{stack}-videos-{account}`
es el mismo string en todas las regiones), y S3 tarda un rato —de minutos a
~1 hora— en liberar un nombre recién borrado. Si reintentas el deploy antes de
que se libere, la recreación falla con:

```
CREATE_FAILED  AWS::S3::Bucket  VideosBucket
A conflicting conditional operation is currently in progress against this
resource. Please try again. (Service: S3, Status Code: 409)
```

Y como ese fallo dispara **otro rollback que vuelve a borrar el nombre**,
reintentar de inmediato reinicia el reloj y perpetúa el problema. La solución
**no** es reintentar en el acto:

1. Borra el stack que quedó en `ROLLBACK_COMPLETE`:
   `aws cloudformation delete-stack --stack-name <stack>` (los buckets ya
   fueron borrados en el rollback, el stack queda vacío).
2. **Espera** a que S3 libere el nombre (deja pasar ~30–60 min sin correr
   `deploy.sh`; cada intento fallido en el medio reinicia la espera).
3. Recién entonces corre `./scripts/deploy.sh` **una sola vez**.

## Subir un video (disparar el pipeline)

Cada `.mp4` subido al bucket de videos dispara **una ejecución completa** del
pipeline (extracción → inferencia → notificación). Se sube por línea de
comandos con `aws s3 cp`, no hay UI.

### Credenciales en tu shell (importante)

`.env` solo se carga automáticamente **dentro de `deploy.sh`** (que hace
`source .env` internamente) — si corrés `aws` directamente en tu terminal
para subir un video o inspeccionar el stack, esa sesión no tiene las
credenciales cargadas y falla con `Unable to locate credentials`. Cargalas
primero una vez por sesión de terminal:

```bash
set -a && source .env && set +a
```

Después de eso, cualquier comando `aws ...` en esa misma terminal usa las
credenciales del `.env` (hasta que cierres la terminal).

### Nombre del archivo (obligatorio)

Tiene que respetar el formato `{session_id}_{left|right}.mp4` — la Lambda
`frame_extractor` parsea `session_id` y `eye` del nombre con
`rsplit('_', 1)` ([functions/frame_extractor/app.py](functions/frame_extractor/app.py)).
Ejemplos válidos: `sesion123_left.mp4`, `paciente001_right.mp4`. Si el
nombre no termina en `_left.mp4` o `_right.mp4`, la ejecución falla al
parsear el archivo.

### Obtener el nombre del bucket

El nombre incluye el Account ID, así que conviene no hardcodearlo. Si ya
desplegaste, lo sacas del output del stack:

```bash
set -a && source .env && set +a
aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME:-ocular-pipeline}" \
  --region "$AWS_DEFAULT_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='VideosBucketName'].OutputValue" \
  --output text
```

(`./scripts/deploy.sh` también imprime este mismo valor al final de cada
deploy.)

### Subir el video

```bash
aws s3 cp /ruta/a/tu-video.mp4 s3://<stack>-videos-<account-id>/sesion123_left.mp4
```

Podés subir varios de una vez (uno por ejecución, se procesan en paralelo):

```bash
aws s3 cp sesion123_left.mp4  s3://<stack>-videos-<account-id>/
aws s3 cp sesion123_right.mp4 s3://<stack>-videos-<account-id>/
```

### Verificar que se disparó

El bucket tiene EventBridge habilitado (`Object Created`, sufijo `.mp4`), así
que la ejecución arranca sola apenas termina el `PUT`. Para confirmarlo:

```bash
aws stepfunctions list-executions \
  --state-machine-arn "arn:aws:states:$AWS_DEFAULT_REGION:<account-id>:stateMachine:${STACK_NAME:-ocular-pipeline}-pipeline" \
  --region "$AWS_DEFAULT_REGION" \
  --max-items 5 --output table
```

O seguirlo visualmente en la consola de AWS: **Step Functions → State
machines → `<stack>-pipeline` → Executions**. Al terminar, la notificación
llega a tu `ENDPOINT_URL` (ver [`testing/TEST_ENDPOINT.md`](testing/TEST_ENDPOINT.md)
si querés probarlo sin un backend propio).

## Permisos IAM necesarios (usuario/rol del `.env`)

El usuario/rol que corre `sam build`/`sam deploy` (las credenciales del
`.env`) necesita permisos propios para crear la infraestructura — distintos
de los roles de ejecución de las Lambdas, que ya están acotados dentro de
`template.yaml`. Policy de least-privilege lista para usar, más las
gotchas específicas de SAM (stacks de bootstrap, transform, límite de
tamaño de inline policy): **[`IAM_PERMISSIONS.md`](IAM_PERMISSIONS.md)**.

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
# 0. Entorno local para correr scripts/tests (una sola vez) -- usa Python 3.12,
#    la misma version que corre en Lambda (ver requirements.txt para el detalle)
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 1. Validar el template SAM (si tienes SAM CLI instalado)
sam validate --lint

# 2. Compilar los módulos Python
python -m py_compile functions/frame_extractor/app.py \
    functions/inference/app.py functions/inference/yolo_onnx.py \
    functions/notifier/app.py

# 3. Correr los tests (no requieren AWS ni el modelo real)
python -m pytest testing/ -v
```

Prueba E2E completa (requiere cuenta AWS + tu modelo `.onnx` ya colocado):
`./scripts/deploy.sh` → subir `sesion123_left.mp4` al bucket de videos → ver
la ejecución en la consola de Step Functions → verificar la notificación
recibida en el endpoint y que la `download_url` efectivamente sirva el
archivo.

Para probar esto último sin depender de un backend propio, hay un endpoint de
prueba local (servidor HTTP + túnel ngrok) que imprime la notificación
recibida y descarga el archivo automáticamente: ver
**[`testing/TEST_ENDPOINT.md`](testing/TEST_ENDPOINT.md)**. Para el detalle
campo por campo del POST y del archivo consolidado (JSON/CSV): ver
**[`NOTIFICATION_FORMAT.md`](NOTIFICATION_FORMAT.md)**.
