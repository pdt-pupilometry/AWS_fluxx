"""Parseo de nombres de video y keys de frame (sin I/O)."""

from __future__ import annotations

def parse_session_and_eye(key: str) -> tuple[str, str]:
    """Extrae (session_id, eye) de una key S3 `{session_id}_{left|right}.mp4`."""
    filename = key.rsplit("/", 1)[-1]
    base = filename.replace(".mp4", "").replace(".avi", "")
    try:
        session_id, eye = base.rsplit("_", 1)
    except ValueError:
        session_id, eye = base, "unknown"
    return session_id, eye.lower()

def frame_object_key(frames_prefix: str, frame_index: int, timestamp_ms: int) -> str:
    return f"{frames_prefix}f{frame_index:06d}_t{timestamp_ms}.jpg"

def frames_prefix_for_execution(execution_name: str) -> str:
    return f"frames/{execution_name}/"
