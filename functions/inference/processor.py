"""Procesa un frame: S3 get → decode → infer (fail-soft por frame)."""

from __future__ import annotations

from typing import Callable, Protocol
from records import ZERO_METRICS, FrameMetrics, base_record
from frame_keys import parse_frame_key
import numpy as np
import boto3
import cv2

class ObjectStore(Protocol):
    def get_bytes(self, bucket: str, key: str) -> bytes: ...

class S3ObjectStore:
    def __init__(self, client=None) -> None:
        self._s3 = client or boto3.client("s3")

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return self._s3.get_object(Bucket=bucket, Key=key)["Body"].read()


InferFn = Callable[[np.ndarray], FrameMetrics]

class FrameProcessor:
    def __init__(self, store: ObjectStore, infer: InferFn) -> None:
        self._store = store
        self._infer = infer

    def process(self, bucket: str, frame_key: str, session_id: str, eye: str) -> dict:
        frame_index, timestamp_s = parse_frame_key(frame_key)
        record = base_record(session_id, eye, frame_index, timestamp_s)
        try:
            raw = self._store.get_bytes(bucket, frame_key)
            img_gray = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
            if img_gray is None:
                raise RuntimeError(f"No se pudo decodificar s3://{bucket}/{frame_key}")
            metrics = self._infer(img_gray)
        except Exception as exc:
            print(f"WARN: fallo procesando {frame_key}: {exc}")
            metrics = dict(ZERO_METRICS)
        return {**record, **metrics}
