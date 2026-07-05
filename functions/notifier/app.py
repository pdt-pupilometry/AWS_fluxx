"""
Lambda 3 — Agregación, reconciliación de fallos y notificación al endpoint externo.

Cuando el Distributed Map termina, su ResultWriter deja en S3 un manifest.json
con la lista de archivos SUCCEEDED_*.json y FAILED_*.json (los outputs de las
1000+ Lambdas de inferencia NO viajan por el payload del state machine: eso
excedería el límite de 256 KB de Step Functions).

Esta Lambda:
  1. Lee el manifest y TODOS los SUCCEEDED_*.json -> registros reales.
  2. Lee TAMBIÉN los FAILED_*.json (fallos de infraestructura: timeout, OOM,
     throttling agotado) -> reconstruye, desde el `Input` de cada ejecución
     fallida, un registro con métricas en 0 para cada frame afectado.
     Objetivo: el consolidado SIEMPRE incluye los N/N frames, nunca omite uno
     silenciosamente.
  3. Ordena por frame_index y serializa el consolidado como UN SOLO archivo
     JSON o CSV (parámetro OUTPUT_FORMAT) que sube al bucket de frames. No se
     envía la data en el body del POST: con miles de frames por video, el
     body podría pesar varios MB y arriesgar timeouts o límites de tamaño del
     lado del endpoint.
  4. Genera una URL prefirmada de descarga (S3 GetObject) y envía al endpoint
     una notificación PEQUEÑA (metadata + esa URL), con reintentos y backoff.
"""

import csv
import gzip
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone

import boto3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ENDPOINT_URL = os.environ["ENDPOINT_URL"]
ENDPOINT_API_KEY = os.environ.get("ENDPOINT_API_KEY", "")
OUTPUT_FORMAT = os.environ.get("OUTPUT_FORMAT", "json").lower()  # "json" o "csv"
GZIP_FILE = os.environ.get("GZIP_FILE", "true").lower() == "true"
# Los roles de Lambda usan credenciales temporales (STS): una URL prefirmada
# nunca dura más que esas credenciales, sin importar ExpiresIn. 1h es un
# default conservador y seguro; el endpoint debe descargar el archivo pronto
# después de recibir la notificación, no tratar la URL como un link permanente.
PRESIGNED_URL_EXPIRATION_SECONDS = int(os.environ.get("PRESIGNED_URL_EXPIRATION_SECONDS", "3600"))

FRAME_KEY_RE = re.compile(r"f(?P<idx>\d{6})_t(?P<ts_ms>\d+)\.jpg$")
CSV_FIELDNAMES = [
    "session_id",
    "eye",
    "timestamp",
    "pupil_area_pixels",
    "iris_area_pixels",
    "pupil_iris_ratio",
    "pupil_confidence",
    "iris_confidence",
]

s3 = boto3.client("s3")

_retry = Retry(
    total=5,
    backoff_factor=1.0,  # 1s, 2s, 4s, 8s, 16s
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"],
    respect_retry_after_header=True,
)
_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=_retry))
_session.mount("http://", HTTPAdapter(max_retries=_retry))


def _read_json_from_s3(bucket: str, key: str):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


def _parse_frame_key(frame_key: str) -> tuple[int, float]:
    match = FRAME_KEY_RE.search(frame_key)
    if not match:
        raise ValueError(f"Key de frame con formato inesperado: {frame_key}")
    return int(match.group("idx")), int(match.group("ts_ms")) / 1000.0


def _zero_record(session_id: str, eye: str, frame_key: str) -> dict:
    frame_index, timestamp_s = _parse_frame_key(frame_key)
    return {
        "session_id": session_id,
        "eye": eye,
        "frame_index": frame_index,
        "timestamp": timestamp_s,
        "pupil_area_pixels": 0.0,
        "iris_area_pixels": 0.0,
        "pupil_iris_ratio": 0.0,
        "pupil_confidence": 0.0,
        "iris_confidence": 0.0,
    }


def _reconcile_failed_entry(entry: dict) -> list[dict]:
    """Reconstruye registros vacíos para los frames de una ejecución hija fallida,
    usando su `Input` original (BatchInput.session_id/eye + Items[].frame_key)."""
    raw_input = entry.get("Input")
    if raw_input is None:
        return []
    if isinstance(raw_input, str):
        raw_input = json.loads(raw_input)

    batch_input = raw_input.get("BatchInput", {})
    session_id = batch_input.get("session_id", "unknown")
    eye = batch_input.get("eye", "unknown")

    records = []
    for item in raw_input.get("Items", []):
        frame_key = item.get("frame_key")
        if not frame_key:
            continue
        try:
            records.append(_zero_record(session_id, eye, frame_key))
        except ValueError as exc:
            print(f"WARN: no se pudo reconciliar {frame_key}: {exc}")
    return records


def collect_and_reconcile(result_writer: dict) -> list[dict]:
    manifest_bucket = result_writer["Bucket"]
    manifest = _read_json_from_s3(manifest_bucket, result_writer["Key"])
    result_files = manifest.get("ResultFiles", {})

    frames: list[dict] = []

    for result_file in result_files.get("SUCCEEDED", []):
        entries = _read_json_from_s3(manifest_bucket, result_file["Key"])
        for entry in entries:
            output = entry.get("Output")
            if output is None:
                continue
            if isinstance(output, str):
                output = json.loads(output)
            frames.extend(output if isinstance(output, list) else [output])

    failed_files = result_files.get("FAILED", [])
    if failed_files:
        print(f"ADVERTENCIA: {len(failed_files)} archivo(s) FAILED en el manifest, reconciliando...")
        for result_file in failed_files:
            entries = _read_json_from_s3(manifest_bucket, result_file["Key"])
            for entry in entries:
                frames.extend(_reconcile_failed_entry(entry))

    return frames


def _serialize_json(public_frames: list[dict]) -> bytes:
    return json.dumps(public_frames).encode("utf-8")


def _serialize_csv(public_frames: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    writer.writerows(public_frames)
    return buffer.getvalue().encode("utf-8")


def upload_deliverable(bucket: str, execution_name: str, public_frames: list[dict]) -> tuple[str, str]:
    """Serializa el consolidado (JSON o CSV) y lo sube como un único objeto S3.
    Devuelve (key, content_type)."""
    if OUTPUT_FORMAT == "csv":
        body, content_type, extension = _serialize_csv(public_frames), "text/csv", "csv"
    else:
        body, content_type, extension = _serialize_json(public_frames), "application/json", "json"

    put_kwargs = {"ContentType": content_type}
    if GZIP_FILE:
        body = gzip.compress(body)
        put_kwargs["ContentEncoding"] = "gzip"

    key = f"deliverables/{execution_name}/frames.{extension}"
    s3.put_object(Bucket=bucket, Key=key, Body=body, **put_kwargs)
    return key, content_type


def generate_download_url(bucket: str, key: str) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=PRESIGNED_URL_EXPIRATION_SECONDS,
    )


def post_notification(payload: dict) -> None:
    headers = {"Content-Type": "application/json"}
    if ENDPOINT_API_KEY:
        headers["x-api-key"] = ENDPOINT_API_KEY
    response = _session.post(ENDPOINT_URL, json=payload, headers=headers, timeout=(5, 30))
    response.raise_for_status()


def lambda_handler(event, context):
    job = event["job"]
    frames = collect_and_reconcile(event["result_writer"])

    # Orden temporal garantizado, sin importar el orden de término de las Lambdas
    frames.sort(key=lambda f: f["frame_index"])

    total_expected = job["total_frames"]
    if len(frames) != total_expected:
        print(
            f"ADVERTENCIA: se esperaban {total_expected} frames y se armaron {len(frames)} "
            f"para session_id={job['session_id']} eye={job['eye']}"
        )

    # Formato exacto pedido por el endpoint: se descarta frame_index (uso interno para ordenar)
    public_frames = [
        {
            "session_id": f["session_id"],
            "eye": f["eye"],
            "timestamp": f["timestamp"],
            "pupil_area_pixels": f["pupil_area_pixels"],
            "iris_area_pixels": f["iris_area_pixels"],
            "pupil_iris_ratio": f["pupil_iris_ratio"],
            "pupil_confidence": f["pupil_confidence"],
            "iris_confidence": f["iris_confidence"],
        }
        for f in frames
    ]

    deliverable_key, content_type = upload_deliverable(job["frames_bucket"], job["execution_name"], public_frames)
    download_url = generate_download_url(job["frames_bucket"], deliverable_key)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_EXPIRATION_SECONDS)).isoformat()

    post_notification(
        {
            "session_id": job["session_id"],
            "eye": job["eye"],
            "video_key": job["source_video"],
            "fps": job["fps"],
            "total_frames": total_expected,
            "format": OUTPUT_FORMAT,
            "content_type": content_type,
            "compressed": GZIP_FILE,
            "download_url": download_url,
            "expires_at": expires_at,
        }
    )

    print(
        f"Notificado {ENDPOINT_URL}: {len(public_frames)} frames en formato {OUTPUT_FORMAT} "
        f"disponibles en s3://{job['frames_bucket']}/{deliverable_key}"
    )
    return {
        "session_id": job["session_id"],
        "eye": job["eye"],
        "frames_processed": len(public_frames),
        "deliverable_s3_key": deliverable_key,
        "endpoint": ENDPOINT_URL,
    }
