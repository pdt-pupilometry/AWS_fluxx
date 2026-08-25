"""Preprocesamiento de frames: gris → resize → JPEG."""

from __future__ import annotations

from config import ExtractorConfig
from dataclasses import dataclass
import numpy as np
import cv2

@dataclass(frozen=True)
class EncodedFrame:
    index: int
    timestamp_ms: int
    jpeg_bytes: bytes

class FramePreprocessor:
    def __init__(self, config: ExtractorConfig) -> None:
        self._width = config.target_width
        self._height = config.target_height
        self._encode_params = [cv2.IMWRITE_JPEG_QUALITY, config.jpeg_quality]

    def encode(self, frame_bgr: np.ndarray, frame_index: int, fps: float) -> EncodedFrame:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(
            gray,
            (self._width, self._height),
            interpolation=cv2.INTER_AREA,
        )
        ok, buffer = cv2.imencode(".jpg", resized, self._encode_params)
        if not ok:
            raise RuntimeError(f"No se pudo codificar el frame {frame_index}")
        timestamp_ms = round(frame_index * 1000.0 / fps)
        return EncodedFrame(
            index=frame_index,
            timestamp_ms=timestamp_ms,
            jpeg_bytes=buffer.tobytes(),
        )
