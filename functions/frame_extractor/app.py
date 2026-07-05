"""
Lambda 1 — Extracción y pre-procesamiento de frames.

Recibe {bucket, key, execution_name} desde el estado ExtractFrames del ASL.
Descarga el video {session_id}_{left|right}.mp4 a /tmp, extrae TODOS los
frames con OpenCV, los convierte a escala de grises, los redimensiona a
480x640 y los sube como .jpg al bucket temporal de frames.

El prefijo de frames incluye el nombre de la ejecución de Step Functions
(no el session_id a secas) para que volver a subir el mismo video no
mezcle frames de una corrida anterior en el ListObjectsV2 del Distributed Map.
"""

import concurrent.futures
import os

import boto3
import cv2

s3 = boto3.client("s3")

FRAMES_BUCKET = os.environ["FRAMES_BUCKET"]
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "90"))
TARGET_WIDTH = int(os.environ.get("TARGET_WIDTH", "480"))
TARGET_HEIGHT = int(os.environ.get("TARGET_HEIGHT", "640"))
UPLOAD_WORKERS = 16  # subidas a S3 en paralelo; el cuello es la red, no la CPU


def _parse_session_and_eye(key: str) -> tuple[str, str]:
    filename = key.rsplit("/", 1)[-1]
    base = filename.replace(".mp4", "").replace(".avi", "")
    try:
        session_id, eye = base.rsplit("_", 1)
    except ValueError:
        session_id, eye = base, "unknown"
    return session_id, eye.lower()


def _upload_frame(key: str, jpg_bytes: bytes, metadata: dict) -> None:
    s3.put_object(
        Bucket=FRAMES_BUCKET,
        Key=key,
        Body=jpg_bytes,
        ContentType="image/jpeg",
        Metadata=metadata,
    )


def lambda_handler(event, context):
    bucket = event["bucket"]
    key = event["key"]
    execution_name = event["execution_name"]

    session_id, eye = _parse_session_and_eye(key)

    local_path = f"/tmp/{execution_name}_{key.rsplit('/', 1)[-1]}"
    s3.download_file(bucket, key, local_path)

    cap = cv2.VideoCapture(local_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV no pudo abrir el video s3://{bucket}/{key}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames_prefix = f"frames/{execution_name}/"
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]

    frame_index = 0
    futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            # Pre-procesamiento: escala de grises + resize (ancho x alto)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            resized = cv2.resize(gray, (TARGET_WIDTH, TARGET_HEIGHT), interpolation=cv2.INTER_AREA)

            ok_enc, buffer = cv2.imencode(".jpg", resized, encode_params)
            if not ok_enc:
                raise RuntimeError(f"No se pudo codificar el frame {frame_index}")

            timestamp_ms = round(frame_index * 1000.0 / fps)
            frame_key = f"{frames_prefix}f{frame_index:06d}_t{timestamp_ms}.jpg"
            metadata = {
                "session-id": session_id,
                "eye": eye,
                "frame-index": str(frame_index),
                "timestamp-ms": str(timestamp_ms),
            }
            futures.append(pool.submit(_upload_frame, frame_key, buffer.tobytes(), metadata))
            frame_index += 1

        # Propagar cualquier error de subida antes de reportar éxito
        for future in concurrent.futures.as_completed(futures):
            future.result()

    cap.release()
    os.remove(local_path)

    if frame_index == 0:
        raise RuntimeError(f"El video s3://{bucket}/{key} no contiene frames legibles")

    return {
        "session_id": session_id,
        "eye": eye,
        "fps": fps,
        "total_frames": frame_index,
        "frames_bucket": FRAMES_BUCKET,
        "frames_prefix": frames_prefix,
        "execution_name": execution_name,
        "source_video": f"s3://{bucket}/{key}",
    }
