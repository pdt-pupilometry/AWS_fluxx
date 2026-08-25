"""Serialización pública JSON/CSV (extensible por formato)."""

from __future__ import annotations

from typing import Protocol
import json
import csv
import io

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

PUBLIC_FIELDS = CSV_FIELDNAMES

class FrameSerializer(Protocol):
    content_type: str
    extension: str

    def serialize(self, public_frames: list[dict]) -> bytes: ...

class JsonFrameSerializer:
    content_type = "application/json"
    extension = "json"

    def serialize(self, public_frames: list[dict]) -> bytes:
        return json.dumps(public_frames).encode("utf-8")

class CsvFrameSerializer:
    content_type = "text/csv"
    extension = "csv"

    def serialize(self, public_frames: list[dict]) -> bytes:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(public_frames)
        return buffer.getvalue().encode("utf-8")

def serializer_for(output_format: str) -> FrameSerializer:
    if output_format == "csv":
        return CsvFrameSerializer()
    return JsonFrameSerializer()

def to_public_frames(frames: list[dict]) -> list[dict]:
    return [{field: frame[field] for field in PUBLIC_FIELDS} for frame in frames]
