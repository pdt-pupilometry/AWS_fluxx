# Bitácora del proyecto

Registro cronológico de los problemas reales que aparecieron al llevar este
pipeline desde el código hasta producción en AWS, cómo se diagnosticó cada
uno y con qué se resolvió. Complementa al [README](README.md): el README
describe cómo funciona el sistema; esta bitácora, todo lo que se rompió en el
camino y las lecciones que dejó.

Todo ocurrió el **2026-07-05**, durante la primera puesta en marcha completa
(cuenta AWS nueva, usuario IAM `Tesis` sin permisos previos).

---

## 1. `sam build` falló: faltaba Python 3.12 local

- **Síntoma**: `Build Failed — PythonPipBuilder:Validation - Binary validation
  failed for python ... constraints for runtime: python3.12`. Las dos
  imágenes Docker compilaron bien; falló el empaquetado de la Lambda
  `notifier`.
- **Diagnóstico**: `notifier` no es imagen Docker, es **zip** (`Runtime:
  python3.12` en `template.yaml`) — SAM la construye con el Python local de
  la máquina, y el sistema solo tenía 3.14 (Homebrew).
- **Solución**: `brew install python@3.12`.
- **Lección**: el runtime del zip debe existir localmente aunque el resto del
  stack sea contenedores. El mismo requisito reaparece para el venv de tests
  (`requirements.txt` raíz): numpy/onnxruntime pinneados no tienen wheels
  para 3.13+.

## 2. `sam deploy` rechazó el parámetro vacío `EndpointApiKey=`

- **Síntoma**: `Error: Invalid value for '--parameter-overrides':
  EndpointApiKey= is not a valid format`.
- **Diagnóstico**: el parser del formato corto `Key=Value` de SAM no acepta
  valores vacíos, y `ENDPOINT_API_KEY` es opcional (vacío en `.env`).
- **Solución**: en `scripts/deploy.sh`, armar los overrides como array bash
  (`PARAM_OVERRIDES=(...)`) y **omitir** `EndpointApiKey` cuando está vacío —
  el template ya tiene `Default: ""`.
- **Lección**: los parámetros opcionales se omiten, no se pasan vacíos.

## 3. `AccessDenied` sobre el stack de bootstrap de SAM

- **Síntoma**: `User ... is not authorized to perform:
  cloudformation:CreateChangeSet on resource: ...
  stack/aws-sam-cli-managed-default/*`.
- **Diagnóstico**: `--resolve-s3` crea un **segundo stack** de CloudFormation
  (`aws-sam-cli-managed-default`, dueño del bucket de artefactos), y la
  policy de least-privilege solo cubría el stack de la aplicación.
- **Solución**: agregar el ARN de ese stack al statement de CloudFormation en
  la managed policy (ver [IAM_PERMISSIONS.md](IAM_PERMISSIONS.md)).

## 4. Changeset `FAILED`: permiso del transform de SAM

- **Síntoma**: el changeset del bootstrap quedó `FAILED` con `AccessDenied
  ... cloudformation:CreateChangeSet on resource:
  arn:aws:cloudformation:us-east-1:aws:transform/Serverless-2016-10-31`.
- **Diagnóstico**: expandir `Transform: AWS::Serverless-2016-10-31` requiere
  `CreateChangeSet` sobre un **recurso de macro propiedad de AWS** (el
  "account id" del ARN es literalmente `aws`). Permiso que casi todo el
  mundo olvida al armar policies para SAM.
- **Solución**: statement `SamTransform` en la policy.

## 5. Stack de bootstrap colgado en `REVIEW_IN_PROGRESS`

- **Síntoma**: `Error: Stack aws-sam-cli-managed-default is missing Tags
  and/or Outputs ... not in a healthy state`.
- **Diagnóstico**: los intentos fallidos anteriores dejaron el stack de
  bootstrap creado pero sin ejecutar su changeset (un "cascarón" vacío). SAM
  se niega a reutilizarlo.
- **Solución**: `aws cloudformation delete-stack --stack-name
  aws-sam-cli-managed-default` y dejar que el siguiente deploy lo recree.

## 6. `AccessDenied` sobre el Companion Stack de ECR

- **Síntoma**: `not authorized to perform: cloudformation:CreateStack on
  resource: ... stack/ocular-pipeline-54143623-CompanionStack/*`.
- **Diagnóstico**: `--resolve-image-repos` crea un **tercer stack** (el
  "Companion Stack" que gestiona los repos ECR de las imágenes), con un hash
  generado por SAM en el nombre.
- **Solución**: agregar el patrón `<stack>-*-CompanionStack/*` a la policy.
- **Lección** (3–6): un `sam deploy` con `--resolve-s3 --resolve-image-repos`
  toca hasta **tres stacks** de CloudFormation, no uno.

## 7. Cuota de concurrencia Lambda en 10 → cambio de región

- **Síntoma**: `CREATE_FAILED NotifierFunction — Specified
  ReservedConcurrentExecutions ... decreases account's
  UnreservedConcurrentExecution below its minimum value of [10]`.
- **Diagnóstico**: la cuenta tenía la cuota "Concurrent executions" en **10**
  en sa-east-1 (cuentas nuevas arrancan así; el default estándar es 1000).
  AWS exige que el pool sin reservar nunca baje de 10 ⇒ con cuota 10 ninguna
  función puede reservar concurrencia, ni siquiera 1.
- **Solución**: se pidió el aumento a 1000 en sa-east-1 (quedó pendiente) y
  se descubrió que **us-east-1 ya tenía 1000 aprobados** de una solicitud
  anterior. Se migró el deploy a us-east-1 — verificando antes que la infra
  v1 preexistente (`iris-*`) no colisionara con los nombres del stack nuevo.
- **Lección**: las cuotas de Lambda son **por región**; revisar
  `service-quotas get-service-quota --quota-code L-B99A9384` antes del primer
  deploy.

## 8. `409` al recrear los buckets tras un rollback

- **Síntoma**: `CREATE_FAILED VideosBucket — A conflicting conditional
  operation is currently in progress against this resource. Please try
  again. (S3, 409)` — y cada reintento inmediato volvía a fallar igual.
- **Diagnóstico**: los nombres de bucket son **globales**. Un deploy fallido
  hace rollback y **borra** los buckets creados; S3 tarda de minutos a ~1h en
  liberar un nombre recién borrado; reintentar antes de tiempo falla y el
  nuevo rollback **reinicia el reloj** — un ciclo que se perpetúa solo.
- **Solución**: borrar el stack en `ROLLBACK_COMPLETE`, **esperar 30–60 min
  sin reintentar**, y correr el deploy una sola vez. Funcionó al primer
  intento tras la espera.
- **Lección**: con errores de namespace global, reintentar rápido es
  contraproducente.

## 9. Límite de 2048 caracteres en inline policies

- **Síntoma**: la consola de IAM rechazó la policy: "exceeds the
  non-whitespace character limit of 2048".
- **Diagnóstico**: las **inline policies** de usuario tienen cuota fija de
  2048 caracteres; la de least-privilege completa no entra.
- **Solución**: crearla como **managed policy** (límite 6144) y adjuntarla al
  usuario.

## 10. Falso éxito E2E: el 100% de las inferencias crasheaba en silencio

El hallazgo más importante del proyecto.

- **Síntoma**: tres ejecuciones consecutivas terminaron `SUCCEEDED`, el
  endpoint recibió su notificación y el archivo consolidado tenía los
  `total_frames` registros esperados… pero **todas las métricas en 0**. La
  primera sospecha fue que los videos de prueba no tenían pupila/iris.
- **Diagnóstico** (en capas):
  1. `describe-map-run` reveló la verdad: `total: 8286, succeeded: 0,
     failed: 8286` — no era "sin detección" (eso sería SUCCEEDED con ceros),
     era el **100% de las ejecuciones hijas fallando**.
  2. Los `FAILED_*.json` del ResultWriter: `Runtime.ExitError — Runtime
     exited with error: signal: aborted` en cada item.
  3. CloudWatch Logs: `terminate called after throwing an instance of
     'onnxruntime::OnnxRuntimeException' — Attempt to use DefaultLogger but
     none has been registered`, precedido por `Error in cpuinfo: failed to
     parse the list of possible/present processors`, con `INIT_REPORT ...
     Phase: init, Status: error` — el contenedor moría **durante el import**,
     antes de procesar ningún frame.
  4. Causa raíz: bug conocido de **onnxruntime 1.19.x en Lambda ARM64** — en
     el microVM de Firecracker, `cpuinfo` no puede parsear
     `/sys/devices/system/cpu/*` y la excepción revienta la init estática
     nativa (SIGABRT).
  5. Por qué nadie lo vio: `ToleratedFailurePercentage: 100` (el flujo sigue
     aunque falle todo) + la reconciliación del notifier (que convierte cada
     fallo en un registro con métricas 0) enmascararon el desastre **por
     diseño** — las dos features de resiliencia, combinadas, taparon un fallo
     total.
- **Solución**:
  1. `onnxruntime 1.19.2 → 1.27.0` en `functions/inference/requirements.txt`
     (desde ~1.2x el fallo de cpuinfo es un warning no fatal). Verificado
     primero en local con la imagen arm64 (`docker run --entrypoint python
     inferencefunction:latest -c "import onnxruntime"`) y después E2E:
     `succeeded: 2475/2477`, deliverable con **94.2% de frames con detección
     real** (confidences >0.9, ratios fisiológicos).
  2. Nuevo campo **`frames_failed`** en la notificación al endpoint (cuántos
     registros fueron reconciliados con ceros por fallo de infraestructura),
     para que una corrida degradada sea detectable — ver
     [NOTIFICATION_FORMAT.md](NOTIFICATION_FORMAT.md).
- **Lecciones**:
  - Un `SUCCEEDED` de Step Functions no dice nada de la **calidad** del
    resultado; con tolerancia de fallos al 100%, hay que mirar
    `describe-map-run` o exponer el conteo de fallos en el output.
  - "Funciona en Docker local" no implica "funciona en Lambda": el crash no
    reproducía en Docker Desktop porque el `/sys` de Firecracker es distinto.
  - Distinguir siempre "sin detección" (SUCCEEDED con ceros) de "no se pudo
    procesar" (FAILED reconciliado a ceros).

## 11. Memoria de Lambda capada a 3008 MB (y la cuota que miente)

- **Síntoma**: al subir la memoria por encima de 3008 (probado con 4096 y
  luego 5307): `'MemorySize' value failed to satisfy constraint: Member must
  have value less than or equal to 3008`, y rollback del stack.
- **Diagnóstico**: otra cuota de cuenta nueva — el máximo de memoria por
  función viene capado a **3008 MB** (el máximo del servicio es 10240).
  Detalle traicionero: Service Quotas muestra "Max allocated memory: **8
  GB**" para esta misma cuenta/región, pero Lambda igual rechaza >3008 — **el
  valor visible en Service Quotas no refleja el cap real de enforcement** en
  cuentas nuevas. La única señal confiable es el error del deploy.
- **Solución**: quedarse en 3008 mientras tanto; aumento solicitado vía
  `service-quotas request-service-quota-increase --quota-code L-CD1C0CC4
  --desired-value 10240` en us-east-1 (estado `PENDING`). Nota: la API
  rechaza valores chicos con "must be greater than the default quota value
  of 1024.0" — las unidades del request no coinciden con las del
  `get-service-quota` (que responde en GB); 10240 (interpretado como MB)
  fue aceptado. En sa-east-1 esa cuota ni siquiera existe vía API
  (`NoSuchResourceException`) — ahí sería un case de soporte.
- **Lección**: para cuotas de Lambda, el único test confiable es intentar el
  deploy; el valor que muestra Service Quotas puede no ser el que se aplica.

## 12. Optimización de rendimiento para videos largos (medida, no adivinada)

- **Motivación**: el objetivo real son videos de 20+ minutos (~36.000
  frames); la config por defecto extrapolaba a ~30+ min por video.
- **Método**: mismo video de prueba (2477 frames), cambiando **solo
  parámetros** entre corridas y midiendo cada etapa con
  `get-execution-history`:

| Configuración | Extract | Map | Total |
|---|---|---|---|
| 2048MB, batch=1 (defaults) | 20.3s | 115.9s | ~139s |
| Extractor 3008MB, batch=8 | 14.1s | 50.6s | ~67s |
| Ambas 3008MB, batch=8 | 13.0s | 39.6s | **~55s (-60%)** |

- **Resultado**: defaults recomendados en `.env.example`
  (`MAX_ITEMS_PER_BATCH=8`, `EXTRACTOR_MEMORY=3008`,
  `INFERENCE_MEMORY=3008`), con `deploy.sh` extendido para pasar las
  memorias como parámetros. Proyección para 20 min: **~5-6 minutos por
  video**. Detalle en el README, sección "Rendimiento medido".
- **Lección**: en Lambda la CPU escala con la memoria (pagar más por
  GB-segundo pero durar proporcionalmente menos ≈ costo neto neutro, mucha
  menos latencia), y el batching amortiza el overhead fijo por invocación.
  Ninguna de las dos palancas requirió tocar código.

## 13. "¿Por qué tarda 3s por frame si el modelo tarda <1s?" — el techo de vCPU

- **Motivación**: incluso con la config optimizada (batch=8, 3008MB), el Map
  de un video de 1936 frames tardó 38.6s. En la máquina local el modelo
  segmenta en <1s por imagen — ¿dónde se pierde el tiempo?
- **Diagnóstico** (con CloudWatch, no intuición):
  1. `ConcurrentExecutions` máx: 242 — correcto (1936/8 = 242 batches, todos
     en paralelo). La paralelización funciona.
  2. `REPORT` por invocación: `Duration ~19.2s` (8 frames = **2.4s/frame**),
     `Init Duration ~0.7s` (cold start irrelevante), `Max Memory Used 800MB`
     de 3008 (sobra memoria).
  3. Conclusión: el cuello es **CPU por contenedor**. 3008MB = ~1.7 vCPU; en
     la Mac (8+ cores M-series) el mismo modelo vuela. Además `cpuinfo` está
     roto en Firecracker (ver #10), así que ONNX Runtime no puede autodetectar
     cores — el código fijaba `intra_op_num_threads = 2` a mano.
  4. Benchmark local con la imagen arm64 real, simulando los vCPU de Lambda
     con `docker run --cpus`:

| vCPU (memoria equivalente) | ms/frame | vs. actual |
|---|---|---|
| 1.7 (3008MB) | 1104 | baseline |
| **3 (5307MB)** | **578** | **1.9x** |
| 4 (7076MB) | 596 | igual que 3 (satura) |
| 6 (10240MB) | 703 | peor (contención) |

- **Solución** (en dos partes):
  1. `yolo_onnx.py`: `intra_op_num_threads` ahora se calcula de
     `AWS_LAMBDA_FUNCTION_MEMORY_SIZE` (~1 vCPU por 1769MB) — desplegado; a
     3008 sigue siendo 2 hilos (sin cambio), pero acompaña automáticamente a
     la memoria cuando suba.
  2. Subir a 5307MB (3 vCPU / 3 hilos): **bloqueado por la cuota de #11**
     hasta que AWS apruebe el aumento. Al aprobarse: cambiar
     `EXTRACTOR_MEMORY`/`INFERENCE_MEMORY` a 5307 en `.env` y redesplegar —
     Map esperado ~2x más rápido (~19s para 1936 frames; proyección 20 min de
     video: el Map deja de ser el cuello).
- **Lección**: "el modelo tarda <1s" siempre es relativo al hardware; en
  Lambda los vCPU se compran con memoria. Y cuando `cpuinfo` está roto, los
  hilos de ORT hay que fijarlos a mano — el default silencioso puede
  desperdiciar los cores que sí se pagan.

---

## Estado final del día

- Stack `ocular-pipeline` desplegado y sano en us-east-1.
- Inferencia real verificada (94.2% de frames con detección en el video de
  prueba, `pupil_confidence` ~0.95).
- Pipeline completo en ~55s para un video de ~80s; proyección ~5-6 min para
  videos de 20 min.
- Documentación generada en el camino: [IAM_PERMISSIONS.md](IAM_PERMISSIONS.md),
  [NOTIFICATION_FORMAT.md](NOTIFICATION_FORMAT.md),
  [testing/TEST_ENDPOINT.md](testing/TEST_ENDPOINT.md), esta bitácora, y las
  secciones de cuotas/errores del [README](README.md).
- Integración con el backend real verificada: tras un `422` inicial (validación
  del lado del backend, corregida allí), corrida E2E completa `SUCCEEDED`
  contra `https://...devtunnels.ms/api/pupilometry/sessions/` — 1936/1936
  frames segmentados, `frames_failed: 0`, notificación aceptada.
- Pendiente: aprobación del aumento de memoria Lambda (#11) → subir
  `EXTRACTOR_MEMORY`/`INFERENCE_MEMORY` a 5307 en `.env` y redesplegar para
  el ~2x del Map (#13). El código de hilos dinámicos ya quedó desplegado.
