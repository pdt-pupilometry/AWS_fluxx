"""
Lambda 2 — Inferencia YOLO26-seg por frame (invocada por el Distributed Map).

Recibe un batch de Step Functions (ItemBatcher):
{
  "BatchInput": {"frames_bucket": "...", "session_id": "...", "eye": "..."},
  "Items": [{"frame_key": "frames/{execution_name}/f{idx:06d}_t{ts_ms}.jpg"}, ...]
}

Cada frame se procesa dentro de su propio try/except: un fallo de decode,
inferencia o geometría en UN frame nunca debe abortar la invocación completa
(eso la haría terminar en FAILED_*.json del Distributed Map, con riesgo de
perder ese frame en el JSON final) — en su lugar, se devuelve un registro
con las métricas en 0 para ese frame puntual.
"""

import re

import boto3
import cv2
import numpy as np

from yolo_onnx import infer_frame_metrics

s3 = boto3.client("s3")

FRAME_KEY_RE = re.compile(r"f(?P<idx>\d{6})_t(?P<ts_ms>\d+)\.jpg$")

ZERO_METRICS = {
    "pupil_area_pixels": 0.0,
    "iris_area_pixels": 0.0,
    "pupil_iris_ratio": 0.0,
    "pupil_confidence": 0.0,
    "iris_confidence": 0.0,
}


def _parse_frame_key(frame_key: str) -> tuple[int, float]:
    match = FRAME_KEY_RE.search(frame_key)
    if not match:
        raise ValueError(f"Key de frame con formato inesperado: {frame_key}")
    return int(match.group("idx")), int(match.group("ts_ms")) / 1000.0


def process_frame(bucket: str, frame_key: str, session_id: str, eye: str) -> dict:
    frame_index, timestamp_s = _parse_frame_key(frame_key)
    base_record = {
        "session_id": session_id,
        "eye": eye,
        "frame_index": frame_index,
        "timestamp": timestamp_s,
    }
    try:
        obj = s3.get_object(Bucket=bucket, Key=frame_key)
        img_gray = cv2.imdecode(np.frombuffer(obj["Body"].read(), np.uint8), cv2.IMREAD_GRAYSCALE)
        if img_gray is None:
            raise RuntimeError(f"No se pudo decodificar s3://{bucket}/{frame_key}")
        metrics = infer_frame_metrics(img_gray)
    except Exception as exc:  # decode, inferencia o geometria: nunca abortar la invocacion
        print(f"WARN: fallo procesando {frame_key}: {exc}")
        metrics = dict(ZERO_METRICS)

    return {**base_record, **metrics}


def lambda_handler(event, context):
    batch_input = event["BatchInput"]
    bucket = batch_input["frames_bucket"]
    session_id = batch_input["session_id"]
    eye = batch_input["eye"]

    results = []
    for item in event["Items"]:
        frame_key = item["frame_key"]
        if not frame_key.endswith(".jpg"):
            continue
        results.append(process_frame(bucket, frame_key, session_id, eye))
    return results
