# Arquitectura del pipeline

Diagrama del flujo serverless de procesamiento de videos oculares. Detalle
completo (parámetros, IAM, variables de entorno) en [README.md](README.md#diagrama-de-arquitectura)
y en la especificación estado por estado del state machine.

```mermaid
flowchart TD
    subgraph ingest["Ingesta"]
        video["video .mp4<br/>{session_id}_{left|right}.mp4"]
        videosBucket[("S3<br/>VideosBucket")]
        eventbridge{{"EventBridge<br/>Object Created, suffix .mp4"}}
    end

    subgraph sfn["Step Functions — 1 ejecución por video (STANDARD)"]
        extract["1 · ExtractFrames<br/>Lambda 1 (Docker ARM64)<br/>OpenCV: gris → 480×640 → JPEG q90"]

        subgraph map["2 · SegmentFrames — Distributed Map"]
            itemreader["ItemReader<br/>S3 ListObjectsV2"]
            infer["ItemProcessor (EXPRESS)<br/>Lambda 2: ONNX YOLO26-seg<br/>fitEllipse → área pupila/iris"]
            resultwriter["ResultWriter<br/>manifest + SUCCEEDED_*/FAILED_*"]
            itemreader --> infer --> resultwriter
        end

        aggregate["3 · AggregateAndNotify<br/>Lambda 3 (zip ARM64)<br/>reconcilia FAILED, ordena, serializa"]

        extract --> map --> aggregate
    end

    subgraph storage["S3 — FramesBucket"]
        frames[("frames/{exec}/<br/>TTL 1 día")]
        results[("results/{exec}/<br/>TTL 7 días")]
        deliverables[("deliverables/{exec}/<br/>frames.json|csv<br/>TTL 7 días")]
    end

    endpointExt["Endpoint externo<br/>POST metadata + URL prefirmada<br/>GET download_url"]

    video -->|"aws s3 cp"| videosBucket
    videosBucket --> eventbridge
    eventbridge -->|"1 ejecución"| extract

    extract -->|"sube JPEGs"| frames
    itemreader -.->|"lista"| frames
    resultwriter -->|"escribe"| results
    aggregate -.->|"lee SUCCEEDED/FAILED"| results
    aggregate -->|"sube archivo consolidado"| deliverables
    aggregate -->|"4 · notifica"| endpointExt
    endpointExt -.->|"5 · GET"| deliverables

    classDef bucket fill:#2a78d6,stroke:#184f95,color:#fff
    classDef lambda fill:#1baf7a,stroke:#199e70,color:#fff
    classDef ext fill:#eb6834,stroke:#d95926,color:#fff
    class videosBucket,frames,results,deliverables bucket
    class extract,infer,aggregate lambda
    class endpointExt,eventbridge ext
```

## Notas de lectura del diagrama

- **Sin base de datos intermedia**: el estado del job viaja en el JSON de
  Step Functions (`$.job`); la Lambda 2 nunca escribe a ningún lado, solo
  `return` — el `ResultWriter` del Distributed Map junta los resultados.
- **`ToleratedFailurePercentage: 100`**: el Map siempre completa, incluso si
  fallan todas sus ejecuciones hijas; la reconciliación en Lambda 3
  (`FAILED_*.json` → métricas en 0) garantiza que el total de registros
  siempre coincida con `total_frames`.
- **El endpoint nunca recibe los datos de los frames en el POST**, solo
  metadata + un link prefirmado — desacopla el tamaño de la notificación del
  tamaño del video.
- Líneas punteadas = lectura; líneas sólidas = escritura o invocación.
