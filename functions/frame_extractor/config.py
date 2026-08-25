"""Configuración de la Lambda extractora (inyectable, desde env)."""

from __future__ import annotations

from dataclasses import dataclass
import os

@dataclass(frozen=True)
class ExtractorConfig:
    frames_bucket: str
    jpeg_quality: int = 90
    target_width: int = 480
    target_height: int = 640
    upload_workers: int = 16
    tmp_dir: str = "/tmp"

    @classmethod
    def from_env(cls) -> "ExtractorConfig":
        return cls(
            frames_bucket=os.environ["FRAMES_BUCKET"],
            jpeg_quality=int(os.environ.get("JPEG_QUALITY", "90")),
            target_width=int(os.environ.get("TARGET_WIDTH", "480")),
            target_height=int(os.environ.get("TARGET_HEIGHT", "640")),
            upload_workers=int(os.environ.get("UPLOAD_WORKERS", "16")),
            tmp_dir=os.environ.get("TMP_DIR", "/tmp"),
        )
