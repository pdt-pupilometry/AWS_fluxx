"""Fusiona SUCCEEDED + FAILED del ResultWriter → N/N frames."""

from __future__ import annotations

from frame_keys import parse_frame_key
from typing import Protocol
import json

class JsonObjectStore(Protocol):
    def read_json(self, bucket: str, key: str): ...

def zero_record(session_id: str, eye: str, frame_key: str) -> dict:
    frame_index, timestamp_s = parse_frame_key(frame_key)
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

def reconcile_failed_entry(entry: dict) -> list[dict]:
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
            records.append(zero_record(session_id, eye, frame_key))
        except ValueError as exc:
            print(f"WARN: no se pudo reconciliar {frame_key}: {exc}")
    return records

class ResultReconciler:
    def __init__(self, store: JsonObjectStore) -> None:
        self._store = store

    def collect(self, result_writer: dict) -> tuple[list[dict], int]:
        bucket = result_writer["Bucket"]
        manifest = self._store.read_json(bucket, result_writer["Key"])
        result_files = manifest.get("ResultFiles", {})

        frames: list[dict] = []
        frames_failed = 0

        for result_file in result_files.get("SUCCEEDED", []):
            entries = self._store.read_json(bucket, result_file["Key"])
            for entry in entries:
                output = entry.get("Output")
                if output is None:
                    continue
                if isinstance(output, str):
                    output = json.loads(output)
                frames.extend(output if isinstance(output, list) else [output])

        failed_files = result_files.get("FAILED", [])
        if failed_files:
            print(
                f"ADVERTENCIA: {len(failed_files)} archivo(s) FAILED en el manifest, "
                "reconciliando..."
            )
            for result_file in failed_files:
                entries = self._store.read_json(bucket, result_file["Key"])
                for entry in entries:
                    reconciled = reconcile_failed_entry(entry)
                    frames_failed += len(reconciled)
                    frames.extend(reconciled)

        return frames, frames_failed
