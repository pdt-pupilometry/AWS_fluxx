"""Parseo de keys `f{idx}_t{ts}.jpg` del Distributed Map."""

from __future__ import annotations

import re

FRAME_KEY_RE = re.compile(r"f(?P<idx>\d{6})_t(?P<ts_ms>\d+)\.jpg$")

def parse_frame_key(frame_key: str) -> tuple[int, float]:
    match = FRAME_KEY_RE.search(frame_key)
    if not match:
        raise ValueError(f"Key de frame con formato inesperado: {frame_key}")
    return int(match.group("idx")), int(match.group("ts_ms")) / 1000.0
