"""Contrato de métricas por frame hacia el notifier."""

from __future__ import annotations

from typing import TypedDict

class FrameMetrics(TypedDict):
    pupil_area_pixels: float
    iris_area_pixels: float
    pupil_iris_ratio: float
    pupil_confidence: float
    iris_confidence: float

ZERO_METRICS: FrameMetrics = {
    "pupil_area_pixels": 0.0,
    "iris_area_pixels": 0.0,
    "pupil_iris_ratio": 0.0,
    "pupil_confidence": 0.0,
    "iris_confidence": 0.0,
}

def base_record(session_id: str, eye: str, frame_index: int, timestamp_s: float) -> dict:
    return {
        "session_id": session_id,
        "eye": eye,
        "frame_index": frame_index,
        "timestamp": timestamp_s,
    }
